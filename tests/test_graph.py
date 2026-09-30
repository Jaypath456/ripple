import subprocess
from pathlib import Path

from ripple.graph import (
    build_dependency_graph,
    fan_in,
    implementation_order,
    neighbors,
)
from ripple.models import FileRecord, ImportRecord
from ripple.scanner import scan_repository


def file(path: str, *, is_test: bool = False) -> FileRecord:
    return FileRecord(path=Path(path), module=Path(path).stem, is_test=is_test)


def imported(
    source: str,
    target: str | None,
    *,
    type_checking: bool = False,
) -> ImportRecord:
    return ImportRecord(
        source_path=Path(source),
        module="external" if target is None else Path(target).stem,
        names=(),
        aliases=(),
        target_path=Path(target) if target else None,
        line=1,
        level=0,
        is_from_import=False,
        type_checking_only=type_checking,
    )


def test_graph_queries_ignore_external_and_type_checking_edges() -> None:
    files = tuple(file(f"{name}.py") for name in "abcde")
    imports = (
        imported("a.py", "b.py"),
        imported("a.py", "b.py"),
        imported("a.py", "b.py", type_checking=True),
        imported("a.py", "d.py", type_checking=True),
        imported("a.py", None),
        imported("b.py", "c.py"),
        imported("c.py", "a.py"),
    )

    graph = build_dependency_graph(files, imports)
    nodes = {node.path: node for node in graph}

    assert [node.path for node in graph] == [Path(f"{name}.py") for name in "abcde"]
    assert nodes[Path("a.py")].dependencies == (Path("b.py"),)
    assert nodes[Path("a.py")].type_checking_dependencies == (Path("d.py"),)
    assert nodes[Path("c.py")].dependents == (Path("b.py"),)
    assert fan_in(graph, Path("c.py")) == 1

    assert [
        (item.path, item.depth) for item in neighbors(graph, Path("a.py"), "imports", 1)
    ] == [(Path("b.py"), 1)]
    assert [
        (item.path, item.depth) for item in neighbors(graph, Path("a.py"), "imports", 2)
    ] == [
        (Path("b.py"), 1),
        (Path("c.py"), 2),
    ]
    assert [
        (item.path, item.depth)
        for item in neighbors(graph, Path("c.py"), "imported_by", 2)
    ] == [
        (Path("b.py"), 1),
        (Path("a.py"), 2),
    ]
    assert Path("d.py") not in {
        item.path for item in neighbors(graph, Path("a.py"), "imports", 2)
    }


def test_implementation_order_is_dependency_first_and_cycle_safe() -> None:
    files = tuple(
        file(path) for path in ("model.py", "service.py", "route.py", "unrelated.py")
    )
    graph = build_dependency_graph(
        files,
        (
            imported("route.py", "service.py"),
            imported("service.py", "model.py"),
        ),
    )
    requested = tuple(file.path for file in files)

    ordered = implementation_order(graph, requested)

    assert set(ordered) == set(requested)
    assert len(ordered) == len(requested)
    assert ordered.index(Path("model.py")) < ordered.index(Path("service.py"))
    assert ordered.index(Path("service.py")) < ordered.index(Path("route.py"))

    cycle = build_dependency_graph(
        tuple(file(path) for path in ("a.py", "y.py", "z.py")),
        (
            imported("a.py", "z.py"),
            imported("y.py", "z.py"),
            imported("z.py", "y.py"),
        ),
    )
    cycle_paths = (Path("z.py"), Path("a.py"), Path("y.py"))
    first = implementation_order(cycle, cycle_paths)
    second = implementation_order(cycle, cycle_paths)
    assert first == second
    assert set(first) == set(cycle_paths)
    assert len(first) == len(cycle_paths)
    assert first.index(Path("z.py")) < first.index(Path("a.py"))


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


def test_test_mapping_uses_import_reference_and_exact_naming(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "RIPPLE Tests")
    git(tmp_path, "config", "user.email", "ripple@example.test")
    write(tmp_path, "src/app/auth.py", "def authenticate():\n    return True\n")
    write(
        tmp_path, "src/app/services/user_service.py", "class UserService:\n    pass\n"
    )
    write(
        tmp_path,
        "tests/test_auth.py",
        "from app.auth import authenticate\n\ndef test_auth():\n    authenticate()\n",
    )
    write(tmp_path, "tests/test_user_service.py", "def test_placeholder():\n    pass\n")
    write(tmp_path, "tests/test_unrelated.py", "def test_unrelated():\n    pass\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "mapping fixture")

    index = scan_repository(tmp_path)
    mappings = {
        (mapping.test_path, mapping.source_path): mapping
        for mapping in index.test_mappings
    }

    auth = mappings[(Path("tests/test_auth.py"), Path("src/app/auth.py"))]
    assert auth.reasons == ("imports", "references", "naming")
    assert auth.source_symbols == ("src/app/auth.py::authenticate",)
    assert auth.test_symbols == ("tests/test_auth.py::test_auth",)

    naming = mappings[
        (
            Path("tests/test_user_service.py"),
            Path("src/app/services/user_service.py"),
        )
    ]
    assert naming.reasons == ("naming",)
    assert not any(
        mapping.test_path == Path("tests/test_unrelated.py")
        for mapping in index.test_mappings
    )
