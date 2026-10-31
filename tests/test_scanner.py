import json
import subprocess
from pathlib import Path

import pytest

from ripple.cache import scan_repository_cached
from ripple.cli import main
from ripple.models import INDEX_SCHEMA_VERSION, RepositoryIndex
from ripple.scanner import ScanError, scan_repository


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def write(repo: Path, relative_path: str, content: str = "") -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "RIPPLE Tests")
    git(tmp_path, "config", "user.email", "ripple@example.test")
    return tmp_path


def commit_all(repo: Path) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "test fixture")
    return git(repo, "rev-parse", "HEAD")


def test_non_git_directory_is_rejected_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ScanError, match="not a Git repository"):
        scan_repository(tmp_path)


def test_scan_discovers_only_tracked_python_files(repository: Path) -> None:
    write(repository, "app/tracked.py")
    write(repository, "notes.txt")
    commit_all(repository)
    write(repository, "app/untracked.py")

    index = scan_repository(repository)

    assert [record.path for record in index.files] == [Path("app/tracked.py")]


@pytest.mark.parametrize(
    ("relative_path", "expected_is_test"),
    [
        ("tests/helpers.py", True),
        ("app/test_users.py", True),
        ("app/users_test.py", True),
        ("app/users.py", False),
    ],
)
def test_test_file_detection(
    repository: Path, relative_path: str, expected_is_test: bool
) -> None:
    write(repository, relative_path)
    commit_all(repository)

    index = scan_repository(repository)

    assert index.files[0].is_test is expected_is_test


def test_module_names_for_package_init_and_src_layout(repository: Path) -> None:
    for path in (
        "app/models/user.py",
        "app/models/__init__.py",
        "src/ripple/scanner.py",
        "src/ripple/__init__.py",
    ):
        write(repository, path)
    commit_all(repository)

    modules = {
        record.path.as_posix(): record.module
        for record in scan_repository(repository).files
    }

    assert modules == {
        "app/models/__init__.py": "app.models",
        "app/models/user.py": "app.models.user",
        "src/ripple/__init__.py": "ripple",
        "src/ripple/scanner.py": "ripple.scanner",
    }


def test_scan_captures_current_commit(repository: Path) -> None:
    write(repository, "module.py")
    expected_commit = commit_all(repository)

    assert scan_repository(repository).commit == expected_commit


def test_repository_without_commits_is_rejected_cleanly(repository: Path) -> None:
    with pytest.raises(ScanError, match="repository has no commits"):
        scan_repository(repository)


def test_cli_scan_prints_summary(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repository, "src/example/core.py")
    write(repository, "tests/test_core.py")
    commit = commit_all(repository)

    exit_code = main(["scan", str(repository)])

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:10] == [
        f"Repository: {repository.name}",
        f"Commit: {commit[:7]}",
        "Dirty: no",
        "Python files: 2",
        "Test files: 1",
        "Symbols: 0",
        "Imports: 0",
        "References: 0",
        "Parse errors: 0",
        f"Cache: written (.ripple/index/{commit}.json)",
    ]
    assert lines[10].startswith("Scan time: ")
    assert lines[10].endswith("s")


def test_cli_reports_normal_errors_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["scan", str(tmp_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: not a Git repository")


def test_scan_extracts_structural_symbols(repository: Path) -> None:
    source = '''@entity
class User(Base, TimestampMixin):
    """Primary user model.

    Additional detail is intentionally omitted from the index.
    """

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return self.name

    @classmethod
    async def load(cls, user_id: int = 1) -> User | None:
        return None


@route("/users")
def authenticate(email: str, active: bool = True) -> User | None:
    """Find a matching user.

    This detail should not be retained.
    """
    return None


async def refresh(*, force: bool = False) -> None:
    pass
'''
    write(repository, "app/models/user.py", source)
    commit_all(repository)

    index = scan_repository(repository)
    symbols = {symbol.qualname: symbol for symbol in index.symbols}

    user = symbols["User"]
    assert user.id == "app/models/user.py::User"
    assert user.kind == "class"
    assert user.name == "User"
    assert user.path == Path("app/models/user.py")
    assert (user.start_line, user.end_line) == (2, 15)
    assert user.signature is None
    assert user.is_async is False
    assert user.decorators == ("entity",)
    assert user.bases == ("Base", "TimestampMixin")
    assert user.doc == "Primary user model."

    display_name = symbols["User.display_name"]
    assert display_name.id == "app/models/user.py::User.display_name"
    assert display_name.kind == "method"
    assert display_name.signature == "display_name(self) -> str"
    assert display_name.decorators == ("property",)
    assert (display_name.start_line, display_name.end_line) == (9, 11)

    load = symbols["User.load"]
    assert load.kind == "method"
    assert load.is_async is True
    assert load.signature == "load(cls, user_id: int=1) -> User | None"
    assert load.decorators == ("classmethod",)

    authenticate = symbols["authenticate"]
    assert authenticate.kind == "function"
    assert authenticate.signature == (
        "authenticate(email: str, active: bool=True) -> User | None"
    )
    assert authenticate.decorators == ("route('/users')",)
    assert authenticate.doc == "Find a matching user."

    refresh = symbols["refresh"]
    assert refresh.kind == "function"
    assert refresh.is_async is True
    assert refresh.signature == "refresh(*, force: bool=False) -> None"

    assert index.files[0].symbol_ids == tuple(symbol.id for symbol in index.symbols)


def test_syntax_error_is_recorded_without_stopping_scan(repository: Path) -> None:
    write(repository, "broken.py", "def broken(:\n    pass\n")
    write(repository, "healthy.py", "import os\n\ndef healthy():\n    return True\n")
    commit_all(repository)

    first_scan = scan_repository(repository)
    second_scan = scan_repository(repository)
    files = {file.path.as_posix(): file for file in first_scan.files}

    assert files["broken.py"].parse_error is not None
    assert files["broken.py"].parse_error.startswith("line 1")
    assert files["broken.py"].symbol_ids == ()
    assert files["healthy.py"].parse_error is None
    assert files["healthy.py"].symbol_ids == ("healthy.py::healthy",)
    assert [symbol.id for symbol in first_scan.symbols] == ["healthy.py::healthy"]
    assert [(item.source_path, item.module) for item in first_scan.imports] == [
        (Path("healthy.py"), "os")
    ]
    assert first_scan.symbols == second_scan.symbols


def test_cli_summary_includes_symbols_imports_and_parse_errors(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repository, "healthy.py", "import os\n\nclass Healthy:\n    pass\n")
    write(repository, "broken.py", "class Broken(:\n    pass\n")
    commit = commit_all(repository)

    exit_code = main(["scan", str(repository)])

    assert exit_code == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:10] == [
        f"Repository: {repository.name}",
        f"Commit: {commit[:7]}",
        "Dirty: no",
        "Python files: 2",
        "Test files: 0",
        "Symbols: 1",
        "Imports: 1",
        "References: 0",
        "Parse errors: 1",
        f"Cache: written (.ripple/index/{commit}.json)",
    ]
    assert lines[10].startswith("Scan time: ")


def test_extracts_and_resolves_absolute_imports(repository: Path) -> None:
    write(repository, "app/models/__init__.py")
    write(repository, "app/models/user.py", "class User:\n    pass\n")
    write(repository, "app/services.py")
    write(
        repository,
        "app/consumer.py",
        """import os
import app.models.user
import app.services as services
import app.models
from app.models.user import User
from app.models.user import User as AccountUser
from app.models import user
from app.models import *
""",
    )
    commit_all(repository)

    first_scan = scan_repository(repository)
    second_scan = scan_repository(repository)
    imports = [
        item
        for item in first_scan.imports
        if item.source_path == Path("app/consumer.py")
    ]

    assert [item.module for item in imports] == [
        "os",
        "app.models.user",
        "app.services",
        "app.models",
        "app.models.user",
        "app.models.user",
        "app.models",
        "app.models",
    ]
    assert imports[0].target_path is None
    assert imports[0].line == 1
    assert imports[0].is_from_import is False
    assert imports[1].target_path == Path("app/models/user.py")
    assert imports[2].aliases == (("app.services", "services"),)
    assert imports[2].target_path == Path("app/services.py")
    assert imports[3].target_path == Path("app/models/__init__.py")
    assert imports[4].names == ("User",)
    assert imports[4].line == 5
    assert imports[4].is_from_import is True
    assert imports[4].target_path == Path("app/models/user.py")
    assert imports[5].aliases == (("User", "AccountUser"),)
    assert imports[6].names == ("user",)
    assert imports[6].target_path == Path("app/models/user.py")
    assert imports[7].names == ("*",)
    assert imports[7].target_path == Path("app/models/__init__.py")
    assert first_scan.imports == second_scan.imports


def test_resolves_relative_and_type_checking_imports(repository: Path) -> None:
    write(repository, "app/models/user.py")
    write(repository, "app/core/config.py")
    write(repository, "app/models/__init__.py", "from .user import User\n")
    write(
        repository,
        "app/services/auth.py",
        """from typing import TYPE_CHECKING
import typing
from ..models.user import User
from ..core import config

if TYPE_CHECKING:
    from ..models.user import User as TypedUser

if typing.TYPE_CHECKING:
    from app.models import user
""",
    )
    commit_all(repository)

    index = scan_repository(repository)
    package_import = next(
        item
        for item in index.imports
        if item.source_path == Path("app/models/__init__.py")
    )
    auth_imports = [
        item
        for item in index.imports
        if item.source_path == Path("app/services/auth.py")
    ]

    assert package_import.module == "user"
    assert package_import.level == 1
    assert package_import.target_path == Path("app/models/user.py")

    models_import = auth_imports[2]
    assert models_import.module == "models.user"
    assert models_import.level == 2
    assert models_import.target_path == Path("app/models/user.py")

    config_import = auth_imports[3]
    assert config_import.module == "core"
    assert config_import.names == ("config",)
    assert config_import.level == 2
    assert config_import.target_path == Path("app/core/config.py")

    assert auth_imports[4].type_checking_only is True
    assert auth_imports[4].aliases == (("User", "TypedUser"),)
    assert auth_imports[4].target_path == Path("app/models/user.py")
    assert auth_imports[5].type_checking_only is True
    assert auth_imports[5].target_path == Path("app/models/user.py")


def test_clean_index_is_cached_and_round_trips(repository: Path) -> None:
    write(repository, "module.py", "import os\n\nclass Example:\n    pass\n")
    commit = commit_all(repository)

    first = scan_repository_cached(repository)

    expected_path = repository / ".ripple" / "index" / f"{commit}.json"
    assert first.cache_hit is False
    assert first.cache_path == expected_path
    assert expected_path.is_file()
    assert first.index.schema_version == INDEX_SCHEMA_VERSION
    assert first.index.dirty is False

    loaded = RepositoryIndex.model_validate_json(expected_path.read_text())
    assert loaded == first.index
    assert [
        path for path in expected_path.parent.iterdir() if path.suffix == ".tmp"
    ] == []

    second = scan_repository_cached(repository)
    assert second.cache_hit is True
    assert second.cache_path == expected_path
    assert second.index == first.index


def test_no_cache_bypasses_reads_and_writes(repository: Path) -> None:
    write(repository, "module.py", "value = 1\n")
    commit_all(repository)
    cached = scan_repository_cached(repository)
    assert cached.cache_path is not None
    cached_json = cached.cache_path.read_text()

    uncached = scan_repository_cached(repository, use_cache=False)

    assert uncached.cache_hit is False
    assert uncached.cache_path is None
    assert uncached.index.created_at != cached.index.created_at
    assert cached.cache_path.read_text() == cached_json


def test_dirty_python_content_produces_distinct_cache_keys(repository: Path) -> None:
    write(repository, "module.py", "value = 1\n")
    commit = commit_all(repository)
    clean = scan_repository_cached(repository)

    write(repository, "module.py", "value = 2\n")
    first_dirty = scan_repository_cached(repository)
    write(repository, "module.py", "value = 3\n")
    second_dirty = scan_repository_cached(repository)

    assert clean.cache_path is not None
    assert clean.cache_path.name == f"{commit}.json"
    assert first_dirty.index.dirty is True
    assert first_dirty.cache_path is not None
    assert first_dirty.cache_path.name.startswith(f"{commit}-dirty-")
    assert first_dirty.cache_path != second_dirty.cache_path

    write(repository, "untracked.py", "value = 'ignored'\n")
    repeated_dirty = scan_repository_cached(repository)
    assert repeated_dirty.cache_hit is True
    assert repeated_dirty.cache_path == second_dirty.cache_path


def test_json_output_contains_only_the_repository_index(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repository, "module.py", "def example():\n    pass\n")
    commit_all(repository)

    exit_code = main(["scan", str(repository), "--json", "--no-cache"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["schema_version"] == INDEX_SCHEMA_VERSION
    assert payload["dirty"] is False
    assert payload["files"][0]["path"] == "module.py"
    assert payload["symbols"][0]["id"] == "module.py::example"
    assert payload["references"] == []


def test_cli_reports_cache_hits_and_no_cache_mode(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(repository, "module.py")
    commit_all(repository)

    assert main(["scan", str(repository)]) == 0
    capsys.readouterr()
    assert main(["scan", str(repository)]) == 0
    assert "Cache: hit (" in capsys.readouterr().out

    assert main(["scan", str(repository), "--no-cache"]) == 0
    assert "Cache: disabled" in capsys.readouterr().out


def test_serialized_collections_are_deterministically_ordered(repository: Path) -> None:
    write(repository, "z.py", "import os\n\ndef zed():\n    pass\n")
    write(repository, "a.py", "import sys\n\nclass Alpha:\n    pass\n")
    commit_all(repository)

    payload = json.loads(scan_repository(repository).model_dump_json())

    assert [file["path"] for file in payload["files"]] == ["a.py", "z.py"]
    assert [symbol["id"] for symbol in payload["symbols"]] == [
        "a.py::Alpha",
        "z.py::zed",
    ]
    assert [item["source_path"] for item in payload["imports"]] == [
        "a.py",
        "z.py",
    ]


def test_deleted_files_and_ripple_state_do_not_break_scanning(repository: Path) -> None:
    write(repository, "deleted.py", "value = 1\n")
    write(repository, ".ripple/owned.py", "raise RuntimeError\n")
    commit_all(repository)

    write(repository, ".ripple/owned.py", "changed = True\n")
    ripple_only_change = scan_repository(repository)
    assert ripple_only_change.dirty is False
    assert [file.path for file in ripple_only_change.files] == [Path("deleted.py")]

    (repository / "deleted.py").unlink()

    index = scan_repository(repository)

    assert index.dirty is True
    assert [file.path for file in index.files] == [Path("deleted.py")]
    assert index.files[0].parse_error is not None
    assert index.files[0].parse_error.startswith("unable to read file:")
