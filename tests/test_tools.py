import json
import subprocess
from pathlib import Path

import pytest

from ripple.cli import main
from ripple.models import DependencyNode, FileRecord
from ripple.models import TestMappingRecord as MappingRecord
from ripple.scanner import scan_repository
from ripple.tools import ToolSession


def git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def write(repo: Path, relative_path: str, content: str) -> None:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def tool_repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "RIPPLE Tests")
    git(tmp_path, "config", "user.email", "ripple@example.test")
    write(
        tmp_path,
        "app/models.py",
        """class User:
    def full_name(self):
        return "User"
""",
    )
    write(tmp_path, "app/other.py", "class User:\n    pass\n")
    write(
        tmp_path,
        "app/repository.py",
        """from app.models import User


def save_user():
    return User()
""",
    )
    long_body = "\n".join(f"    value_{number} = {number}" for number in range(130))
    write(
        tmp_path,
        "app/service.py",
        f"""import os
from app.repository import save_user as persist


def create_user():
    return persist()


def untested():
    return os.getcwd()


def long_function():
{long_body}
""",
    )
    write(tmp_path, "app/orphan.py", "def orphan():\n    pass\n")
    write(
        tmp_path,
        "tests/test_service.py",
        """from app.service import create_user


def test_create_user():
    create_user()
""",
    )
    calls = "\n".join("    Account()" for _ in range(45))
    write(
        tmp_path,
        "tests/test_models.py",
        f"""from app.models import User as Account


def test_many_users():
{calls}
""",
    )
    write(tmp_path, "broken.py", "def broken(:\n    pass\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "tool fixture")
    return tmp_path


@pytest.fixture
def session(tool_repository: Path) -> ToolSession:
    return ToolSession(scan_repository(tool_repository))


def test_registry_validates_arguments_and_assigns_evidence_ids(
    session: ToolSession,
) -> None:
    for number, name in enumerate(
        (
            "search_code",
            "inspect_symbol",
            "find_references",
            "get_dependencies",
            "find_tests",
        ),
        start=1,
    ):
        result = session.invoke(name, {})
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "invalid_arguments"
        assert result.evidence_id == f"e{number}"
        assert result.truncated is False

    unknown = session.invoke("missing_tool", {})
    assert unknown.error is not None
    assert unknown.error.code == "unknown_tool"
    assert unknown.evidence_id == "e6"


def test_search_code_happy_path_errors_and_limit(session: ToolSession) -> None:
    found = session.invoke(
        "search_code", {"query": "create user", "kind": "symbol", "limit": 5}
    )
    assert found.ok is True
    assert found.data["hits"][0]["symbol"] == "app/service.py::create_user"

    limited = session.invoke("search_code", {"query": "user", "limit": 1})
    assert len(limited.data["hits"]) == 1
    assert limited.truncated is True

    empty = session.invoke("search_code", {"query": "   "})
    assert empty.error.code == "empty_query"
    missing = session.invoke("search_code", {"query": "zzzz-not-present"})
    assert missing.error.code == "no_results"
    invalid = session.invoke("search_code", {"query": "user", "limit": 16})
    assert invalid.error.code == "invalid_arguments"


def test_inspect_symbol_file_symbol_errors_and_source_cap(session: ToolSession) -> None:
    outline = session.invoke("inspect_symbol", {"target": "app/service.py"})
    assert outline.ok is True
    assert {item["id"] for item in outline.data["outline"]} >= {
        "app/service.py::create_user",
        "app/service.py::long_function",
    }

    symbol = session.invoke("inspect_symbol", {"target": "app/service.py::create_user"})
    assert symbol.ok is True
    assert "def create_user" in symbol.data["source"]
    assert symbol.truncated is False

    long_symbol = session.invoke(
        "inspect_symbol", {"target": "app/service.py::long_function"}
    )
    assert long_symbol.ok is True
    assert len(long_symbol.data["source"].splitlines()) == 120
    assert long_symbol.truncated is True

    missing = session.invoke("inspect_symbol", {"target": "app/missing.py"})
    assert missing.error.code == "not_found"
    broken = session.invoke("inspect_symbol", {"target": "broken.py"})
    assert broken.error.code == "parse_error"
    unsafe = session.invoke("inspect_symbol", {"target": "../../etc/passwd"})
    assert unsafe.error.code == "path_outside_repo"


def test_find_references_aliases_ambiguity_and_cap(session: ToolSession) -> None:
    found = session.invoke(
        "find_references", {"symbol_id": "app/models.py::User", "limit": 40}
    )
    assert found.ok is True
    assert len(found.data["references"]) == 40
    assert found.truncated is True
    assert any(
        item["path"] == "tests/test_models.py" and item["kind"] == "call"
        for item in found.data["references"]
    )

    ambiguous = session.invoke("find_references", {"symbol_id": "User"})
    assert ambiguous.error.code == "ambiguous_name"
    missing = session.invoke("find_references", {"symbol_id": "app/models.py::Missing"})
    assert missing.error.code == "not_found"
    unsafe = session.invoke("find_references", {"symbol_id": "../../etc/passwd::User"})
    assert unsafe.error.code == "path_outside_repo"


def test_get_dependencies_directions_depth_errors_and_cap(session: ToolSession) -> None:
    imports = session.invoke(
        "get_dependencies",
        {"path": "app/service.py", "direction": "imports", "depth": 2},
    )
    assert imports.ok is True
    assert imports.data["neighbors"] == [
        {"path": "app/repository.py", "depth": 1},
        {"path": "app/models.py", "depth": 2},
    ]

    imported_by = session.invoke(
        "get_dependencies",
        {"path": "app/models.py", "direction": "imported_by", "depth": 1},
    )
    assert {item["path"] for item in imported_by.data["neighbors"]} == {
        "app/repository.py",
        "tests/test_models.py",
    }
    assert imported_by.data["fan_in"] == 2

    missing = session.invoke(
        "get_dependencies",
        {"path": "app/missing.py", "direction": "imports", "depth": 1},
    )
    assert missing.error.code == "not_found"
    external = session.invoke(
        "get_dependencies",
        {"path": "os", "direction": "imports", "depth": 1},
    )
    assert external.error.code == "external_module"
    invalid = session.invoke(
        "get_dependencies",
        {"path": "app/service.py", "direction": "imports", "depth": 3},
    )
    assert invalid.error.code == "invalid_arguments"

    extra_paths = tuple(Path(f"generated/leaf_{number:03}.py") for number in range(101))
    extra_files = tuple(
        FileRecord(path=path, module=path.stem, is_test=False) for path in extra_paths
    )
    graph = (
        DependencyNode(
            path=Path("app/service.py"),
            dependencies=extra_paths,
            dependents=(),
            type_checking_dependencies=(),
        ),
        *(
            DependencyNode(
                path=path,
                dependencies=(),
                dependents=(Path("app/service.py"),),
                type_checking_dependencies=(),
            )
            for path in extra_paths
        ),
    )
    large_index = session.index.model_copy(
        update={
            "files": session.index.files + extra_files,
            "dependency_graph": graph,
        }
    )
    capped = ToolSession(large_index).invoke(
        "get_dependencies",
        {"path": "app/service.py", "direction": "imports", "depth": 1},
    )
    assert len(capped.data["neighbors"]) == 100
    assert capped.truncated is True


def test_find_tests_matches_symbols_no_tests_errors_and_cap(
    session: ToolSession,
) -> None:
    found = session.invoke("find_tests", {"target": "app/service.py"})
    assert found.ok is True
    assert any(
        item["test_path"] == "tests/test_service.py" for item in found.data["tests"]
    )

    symbol = session.invoke("find_tests", {"target": "app/service.py::create_user"})
    assert symbol.ok is True
    assert symbol.data["tests"][0]["references_target"] is True

    no_tests = session.invoke("find_tests", {"target": "app/orphan.py"})
    assert no_tests.ok is True
    assert no_tests.data["tests"] == []
    missing = session.invoke("find_tests", {"target": "app/missing.py"})
    assert missing.error.code == "not_found"
    unsafe = session.invoke("find_tests", {"target": "/etc/passwd"})
    assert unsafe.error.code == "path_outside_repo"

    mappings = tuple(
        MappingRecord(
            test_path=Path(f"tests/test_orphan_{number:02}.py"),
            source_path=Path("app/orphan.py"),
            test_symbols=(),
            source_symbols=(),
            reasons=("naming",),
        )
        for number in range(51)
    )
    large_index = session.index.model_copy(update={"test_mappings": mappings})
    capped = ToolSession(large_index).invoke("find_tests", {"target": "app/orphan.py"})
    assert len(capped.data["tests"]) == 50
    assert capped.truncated is True


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_code", {"query": "create user", "limit": 2}),
        ("inspect_symbol", {"target": "app/service.py::create_user"}),
        ("find_references", {"symbol_id": "app/models.py::User", "limit": 2}),
        (
            "get_dependencies",
            {"path": "app/service.py", "direction": "imports", "depth": 2},
        ),
        ("find_tests", {"target": "app/service.py"}),
    ],
)
def test_each_tool_is_callable_from_cli(
    tool_repository: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    arguments: dict[str, object],
) -> None:
    exit_code = main(["tool", str(tool_repository), name, json.dumps(arguments)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert payload["ok"] is True
    assert payload["evidence_id"] == "e1"
    assert "truncated" in payload


def test_cli_handles_unknown_tool_and_invalid_json(
    tool_repository: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unknown_code = main(["tool", str(tool_repository), "unknown", "{}"])
    unknown = json.loads(capsys.readouterr().out)
    assert unknown_code == 1
    assert unknown["error"]["code"] == "unknown_tool"

    invalid_code = main(["tool", str(tool_repository), "search_code", "{"])
    invalid = json.loads(capsys.readouterr().out)
    assert invalid_code == 1
    assert invalid["error"]["code"] == "invalid_json"
