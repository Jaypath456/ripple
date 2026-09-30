import json
import subprocess
from pathlib import Path

import pytest

from ripple.cache import scan_repository_cached
from ripple.cli import main
from ripple.models import INDEX_SCHEMA_VERSION, RepositoryIndex
from ripple.scanner import scan_repository


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def write(repo: Path, relative_path: str, content: str) -> None:
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
    git(repo, "commit", "-q", "-m", "reference fixture")
    return git(repo, "rev-parse", "HEAD")


def build_reference_fixture(repository: Path) -> None:
    write(
        repository,
        "app/models.py",
        """def permission_required(function):
    return function


class User:
    def full_name(self):
        return "User"


def authenticate():
    return True
""",
    )
    write(
        repository,
        "app/consumer.py",
        """from app.models import User, authenticate, permission_required
from app.models import User as Account
from app.models import authenticate as auth
import app.models as model_alias
import app.models

User()
Account()
authenticate()
auth()
app.models.User()


class Admin(User):
    pass


@permission_required
def handler():
    User()
    model_alias.User()
    User.full_name()
    user_type = User
    callback = User.full_name
    instance.full_name()
    count = 1
    return count
""",
    )
    write(
        repository,
        "app/local.py",
        """class Local:
    pass


def helper():
    pass


def run():
    helper()
    Local()
""",
    )
    write(repository, "broken.py", "def broken(:\n    pass\n")
    commit_all(repository)


def test_resolves_imported_and_same_file_references(
    repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_reference_fixture(repository)

    first_scan = scan_repository(repository)
    second_scan = scan_repository(repository)
    references = {
        (reference.name, reference.line): reference
        for reference in first_scan.references
        if reference.source_path == Path("app/consumer.py")
    }

    user_id = "app/models.py::User"
    authenticate_id = "app/models.py::authenticate"
    method_id = "app/models.py::User.full_name"
    handler_id = "app/consumer.py::handler"

    assert references[("User", 7)].target_symbol == user_id
    assert references[("User", 7)].kind == "call"
    assert references[("User", 7)].enclosing_symbol is None
    assert references[("Account", 8)].target_symbol == user_id
    assert references[("authenticate", 9)].target_symbol == authenticate_id
    assert references[("auth", 10)].target_symbol == authenticate_id
    assert references[("app.models.User", 11)].target_symbol == user_id
    assert references[("model_alias.User", 21)].target_symbol == user_id
    assert references[("User.full_name", 22)].target_symbol == method_id
    assert references[("User", 23)].kind == "name"
    assert references[("User", 23)].target_symbol == user_id
    assert references[("User.full_name", 24)].kind == "attribute"
    assert references[("User.full_name", 24)].target_symbol == method_id

    subclass = references[("User", 14)]
    assert subclass.kind == "subclass"
    assert subclass.target_symbol == user_id
    assert subclass.enclosing_symbol == "app/consumer.py::Admin"

    decorator = references[("permission_required", 18)]
    assert decorator.kind == "decorator"
    assert decorator.target_symbol == "app/models.py::permission_required"
    assert decorator.enclosing_symbol == handler_id

    assert references[("User", 20)].enclosing_symbol == handler_id
    unresolved = references[("instance.full_name", 25)]
    assert unresolved.kind == "call"
    assert unresolved.target_symbol is None
    assert unresolved.confidence == "low"
    assert "count" not in {reference.name for reference in first_scan.references}

    local_references = {
        reference.name: reference
        for reference in first_scan.references
        if reference.source_path == Path("app/local.py")
    }
    assert local_references["helper"].target_symbol == "app/local.py::helper"
    assert local_references["Local"].target_symbol == "app/local.py::Local"
    assert all(
        reference.enclosing_symbol == "app/local.py::run"
        for reference in local_references.values()
    )

    assert not any(
        reference.source_path == Path("broken.py")
        for reference in first_scan.references
    )
    assert first_scan.references == second_scan.references
    assert [
        (reference.source_path.as_posix(), reference.line)
        for reference in first_scan.references
    ] == sorted(
        (reference.source_path.as_posix(), reference.line)
        for reference in first_scan.references
    )

    assert main(["scan", str(repository), "--no-cache"]) == 0
    output = capsys.readouterr().out
    assert f"References: {len(first_scan.references)}" in output


def test_schema_two_cache_is_rebuilt_with_phase_two_facts(repository: Path) -> None:
    write(
        repository,
        "module.py",
        """def helper():
    pass


def run():
    helper()
""",
    )
    commit_all(repository)
    initial = scan_repository_cached(repository)
    assert initial.cache_path is not None
    assert initial.index.references

    old_payload = json.loads(initial.cache_path.read_text())
    old_payload["schema_version"] = 2
    old_payload.pop("dependency_graph")
    old_payload.pop("test_mappings")
    initial.cache_path.write_text(json.dumps(old_payload))

    rebuilt = scan_repository_cached(repository)

    assert rebuilt.cache_hit is False
    assert rebuilt.index.schema_version == INDEX_SCHEMA_VERSION == 3
    assert rebuilt.index.references[0].target_symbol == "module.py::helper"
    round_tripped = RepositoryIndex.model_validate_json(rebuilt.cache_path.read_text())
    assert round_tripped.references == rebuilt.index.references
