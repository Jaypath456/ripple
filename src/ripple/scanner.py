"""Deterministic discovery of tracked Python files in Git repositories."""

import ast
import subprocess
import tokenize
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ripple.graph import build_dependency_graph
from ripple.models import FileRecord, ImportRecord, RepositoryIndex, SymbolRecord
from ripple.references import extract_references
from ripple.test_mapping import build_test_mappings


class ScanError(ValueError):
    """A repository cannot be scanned for an expected user-facing reason."""


@dataclass(frozen=True)
class _RepositoryState:
    repo_root: Path
    commit: str
    dirty: bool
    python_paths: tuple[Path, ...]


def _run_git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ScanError(result.stderr.strip() or "Git command failed")
    return result.stdout


def module_name(path: Path) -> str:
    """Derive a Python module name from a repository-relative path."""

    parts = list(path.parts)
    if parts and parts[0] == "src":
        parts.pop(0)

    filename = parts.pop()
    stem = Path(filename).stem
    if stem != "__init__":
        parts.append(stem)

    return ".".join(parts)


def is_test_file(path: Path) -> bool:
    """Return whether a repository-relative path follows a common test pattern."""

    return (
        "tests" in path.parts[:-1]
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
    )


def _first_docstring_line(node: ast.AST) -> str | None:
    docstring = ast.get_docstring(node, clean=True)
    if docstring is None:
        return None
    return next((line.strip() for line in docstring.splitlines() if line.strip()), None)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    signature = f"{node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        signature += f" -> {ast.unparse(node.returns)}"
    return signature


def _symbol(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    path: Path,
    *,
    class_name: str | None = None,
) -> SymbolRecord:
    qualname = f"{class_name}.{node.name}" if class_name else node.name
    is_class = isinstance(node, ast.ClassDef)
    kind = "class" if is_class else "method" if class_name else "function"
    return SymbolRecord(
        id=f"{path.as_posix()}::{qualname}",
        kind=kind,
        name=node.name,
        qualname=qualname,
        path=path,
        start_line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        signature=None if is_class else _signature(node),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        decorators=tuple(ast.unparse(item) for item in node.decorator_list),
        bases=tuple(ast.unparse(base) for base in node.bases) if is_class else (),
        doc=_first_docstring_line(node),
    )


def _extract_symbols(tree: ast.Module, path: Path) -> tuple[SymbolRecord, ...]:
    symbols: list[SymbolRecord] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_symbol(node, path))
        elif isinstance(node, ast.ClassDef):
            symbols.append(_symbol(node, path))
            symbols.extend(
                _symbol(child, path, class_name=node.name)
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    return tuple(symbols)


def _is_type_checking_test(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == "TYPE_CHECKING"
        or (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "typing"
            and node.attr == "TYPE_CHECKING"
        )
    )


def _absolute_import_module(
    module: str,
    level: int,
    source_module: str,
    source_is_package: bool,
) -> str | None:
    if level == 0:
        return module

    package_parts = source_module.split(".")
    if not source_is_package:
        package_parts.pop()
    parent_steps = level - 1
    if not package_parts or parent_steps >= len(package_parts):
        return None

    base_parts = package_parts[: len(package_parts) - parent_steps]
    if module:
        base_parts.extend(module.split("."))
    return ".".join(base_parts)


def _resolve_import_target(
    module: str | None,
    names: tuple[str, ...],
    is_from_import: bool,
    module_paths: dict[str, Path],
) -> Path | None:
    if module is None:
        return None
    if is_from_import and len(names) == 1 and names[0] != "*":
        imported_module = ".".join(part for part in (module, names[0]) if part)
        if imported_module in module_paths:
            return module_paths[imported_module]
    return module_paths.get(module)


def _extract_imports(
    tree: ast.Module,
    path: Path,
    source_module: str,
    module_paths: dict[str, Path],
) -> tuple[ImportRecord, ...]:
    records: list[tuple[int, int, ImportRecord]] = []
    sequence = 0

    def add_imports(node: ast.Import, type_checking_only: bool) -> None:
        nonlocal sequence
        for imported in node.names:
            aliases = ((imported.name, imported.asname),) if imported.asname else ()
            records.append(
                (
                    node.col_offset,
                    sequence,
                    ImportRecord(
                        source_path=path,
                        module=imported.name,
                        names=(),
                        aliases=aliases,
                        target_path=module_paths.get(imported.name),
                        line=node.lineno,
                        level=0,
                        is_from_import=False,
                        type_checking_only=type_checking_only,
                    ),
                )
            )
            sequence += 1

    def add_from_import(node: ast.ImportFrom, type_checking_only: bool) -> None:
        nonlocal sequence
        module = node.module or ""
        names = tuple(imported.name for imported in node.names)
        aliases = tuple(
            (imported.name, imported.asname)
            for imported in node.names
            if imported.asname
        )
        absolute_module = _absolute_import_module(
            module,
            node.level,
            source_module,
            path.name == "__init__.py",
        )
        records.append(
            (
                node.col_offset,
                sequence,
                ImportRecord(
                    source_path=path,
                    module=module,
                    names=names,
                    aliases=aliases,
                    target_path=_resolve_import_target(
                        absolute_module,
                        names,
                        True,
                        module_paths,
                    ),
                    line=node.lineno,
                    level=node.level,
                    is_from_import=True,
                    type_checking_only=type_checking_only,
                ),
            )
        )
        sequence += 1

    def visit(node: ast.AST, type_checking_only: bool = False) -> None:
        if isinstance(node, ast.Import):
            add_imports(node, type_checking_only)
            return
        if isinstance(node, ast.ImportFrom):
            add_from_import(node, type_checking_only)
            return
        if isinstance(node, ast.If):
            body_is_type_checking = type_checking_only or _is_type_checking_test(
                node.test
            )
            for child in node.body:
                visit(child, body_is_type_checking)
            for child in node.orelse:
                visit(child, type_checking_only)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, type_checking_only)

    visit(tree)
    records.sort(key=lambda item: (item[2].line, item[0], item[1]))
    return tuple(record for _, _, record in records)


def _parse_error(error: SyntaxError) -> str:
    location = f"line {error.lineno}" if error.lineno is not None else "unknown line"
    if error.offset is not None:
        location += f", column {error.offset}"
    return f"{location}: {error.msg}"


def _file_error(error: OSError | UnicodeError) -> str:
    if isinstance(error, OSError):
        detail = error.strerror or str(error)
    else:
        detail = str(error)
    return f"unable to read file: {detail}"


def _scan_file(
    repo_root: Path,
    path: Path,
    module_paths: dict[str, Path],
) -> tuple[
    FileRecord,
    tuple[SymbolRecord, ...],
    tuple[ImportRecord, ...],
    ast.Module | None,
]:
    try:
        with tokenize.open(repo_root / path) as source_file:
            tree = ast.parse(source_file.read(), filename=path.as_posix())
    except SyntaxError as error:
        return (
            FileRecord(
                path=path,
                module=module_name(path),
                is_test=is_test_file(path),
                parse_error=_parse_error(error),
            ),
            (),
            (),
            None,
        )
    except (OSError, UnicodeError) as error:
        return (
            FileRecord(
                path=path,
                module=module_name(path),
                is_test=is_test_file(path),
                parse_error=_file_error(error),
            ),
            (),
            (),
            None,
        )

    symbols = _extract_symbols(tree, path)
    imports = _extract_imports(tree, path, module_name(path), module_paths)
    return (
        FileRecord(
            path=path,
            module=module_name(path),
            is_test=is_test_file(path),
            symbol_ids=tuple(symbol.id for symbol in symbols),
        ),
        symbols,
        imports,
        tree,
    )


def _module_paths(paths: tuple[Path, ...]) -> dict[str, Path]:
    candidates: dict[str, list[Path]] = {}
    for path in paths:
        candidates.setdefault(module_name(path), []).append(path)
    return {
        module: module_candidates[0]
        for module, module_candidates in candidates.items()
        if module and len(module_candidates) == 1
    }


def _repository_state(repo: str | Path) -> _RepositoryState:
    requested_path = Path(repo).expanduser()
    if not requested_path.exists():
        raise ScanError(f"path does not exist: {requested_path}")
    if not requested_path.is_dir():
        raise ScanError(f"path is not a directory: {requested_path}")

    try:
        root_output = _run_git(requested_path, "rev-parse", "--show-toplevel")
    except ScanError as error:
        raise ScanError(f"not a Git repository: {requested_path}") from error

    repo_root = Path(root_output.strip()).resolve()
    try:
        commit = _run_git(repo_root, "rev-parse", "--verify", "HEAD").strip()
    except ScanError as error:
        raise ScanError(f"repository has no commits: {repo_root}") from error

    tracked_output = _run_git(repo_root, "ls-files", "-z")
    paths = tuple(
        sorted(
            path
            for item in tracked_output.split("\0")
            if item.endswith(".py")
            if (path := Path(item)).parts[0] != ".ripple"
        )
    )
    dirty = bool(
        _run_git(
            repo_root,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=no",
            "--",
            ".",
            ":(exclude).ripple",
        )
    )
    return _RepositoryState(
        repo_root=repo_root,
        commit=commit,
        dirty=dirty,
        python_paths=paths,
    )


def _scan_repository_state(state: _RepositoryState) -> RepositoryIndex:
    module_paths = _module_paths(state.python_paths)
    scanned_files = tuple(
        _scan_file(state.repo_root, path, module_paths) for path in state.python_paths
    )
    files = tuple(file for file, _, _, _ in scanned_files)
    symbols = tuple(
        symbol for _, file_symbols, _, _ in scanned_files for symbol in file_symbols
    )
    imports = tuple(
        import_record
        for _, _, file_imports, _ in scanned_files
        for import_record in file_imports
    )
    modules_by_path = {file.path: file.module for file in files}
    references = tuple(
        reference
        for file, _, _, tree in scanned_files
        if tree is not None
        for reference in extract_references(
            tree,
            file.path,
            symbols,
            imports,
            modules_by_path,
        )
    )
    dependency_graph = build_dependency_graph(files, imports)
    test_mappings = build_test_mappings(files, symbols, imports, references)
    return RepositoryIndex(
        repo_root=state.repo_root,
        commit=state.commit,
        dirty=state.dirty,
        created_at=datetime.now(UTC),
        files=files,
        symbols=symbols,
        imports=imports,
        references=references,
        dependency_graph=dependency_graph,
        test_mappings=test_mappings,
    )


def scan_repository(repo: str | Path) -> RepositoryIndex:
    """Create a fresh index of the tracked Python files in ``repo``."""

    return _scan_repository_state(_repository_state(repo))
