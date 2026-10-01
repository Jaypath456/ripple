"""Fixed-command Git diff parsing and semantic Python change analysis."""

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ripple.diff_models import DiffHunk, FileChange

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")


class DiffError(ValueError):
    """The requested Git comparison cannot be resolved safely."""


@dataclass(frozen=True)
class ParsedFileDiff:
    change: FileChange
    hunks: tuple[DiffHunk, ...]
    old_source: str | None
    new_source: str | None


@dataclass(frozen=True)
class DiffSnapshot:
    base: str
    head: str
    files: tuple[ParsedFileDiff, ...]

    def by_path(self, path: str) -> ParsedFileDiff | None:
        return next(
            (
                item
                for item in self.files
                if path
                in {
                    item.change.path,
                    item.change.old_path,
                    item.change.new_path,
                }
            ),
            None,
        )


def _git(repo: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        raise DiffError(result.stderr.strip() or "Git command failed")
    return result.stdout


def _safe_path(raw: str) -> str:
    path = Path(PurePosixPath(raw))
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise DiffError(f"unsafe diff path: {raw}")
    return path.as_posix()


def resolve_commit(repo: Path, value: str) -> str:
    resolved = _git(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    commit = resolved.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DiffError(f"invalid commit: {value}")
    return commit


def resolve_range(
    repo: Path, report_commit: str, requested_range: str
) -> tuple[str, str, str | None]:
    if requested_range.count("..") != 1 or "..." in requested_range:
        raise DiffError("range must have the form base..head")
    raw_base, raw_head = requested_range.split("..", 1)
    if not raw_base or not raw_head:
        raise DiffError("range must have the form base..head")
    supplied_base = resolve_commit(repo, raw_base)
    head = resolve_commit(repo, raw_head)
    report_base = resolve_commit(repo, report_commit)
    if supplied_base == report_base:
        return report_base, head, None

    merge_base = _git(repo, "merge-base", report_base, head, check=False).strip()
    if not merge_base:
        raise DiffError(
            "report commit and requested head have no merge base; refusing unrelated comparison"
        )
    if merge_base == report_base:
        effective = report_base
    else:
        effective = resolve_commit(repo, merge_base)
    warning = (
        f"requested base {supplied_base} differs from report commit {report_base}; "
        f"using effective merge base {effective}"
    )
    return effective, head, warning


def _blob(repo: Path, commit: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{path}"],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _parse_name_status(output: str) -> list[tuple[str, str | None, str]]:
    changes: list[tuple[str, str | None, str]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        code = fields[0]
        status = code[0] if code else ""
        if status == "R" and len(fields) == 3:
            changes.append(("R", _safe_path(fields[1]), _safe_path(fields[2])))
        elif status in {"A", "M", "D"} and len(fields) == 2:
            path = _safe_path(fields[1])
            changes.append((status, path if status == "D" else None, path))
        else:
            raise DiffError(f"unsupported name-status record: {line}")
    return changes


def _file_patch(
    repo: Path, base: str, head: str, old_path: str | None, new_path: str
) -> str:
    paths = [path for path in (old_path, new_path) if path]
    return _git(
        repo,
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--binary",
        "-U0",
        "-M",
        base,
        head,
        "--",
        *dict.fromkeys(paths),
    )


def _parse_hunks(patch: str) -> tuple[DiffHunk, ...]:
    hunks: list[DiffHunk] = []
    current: dict[str, object] | None = None
    for line in patch.splitlines():
        match = _HUNK.match(line)
        if match:
            if current is not None:
                hunks.append(DiffHunk(**current))
            current = {
                "old_start": int(match.group(1)),
                "old_count": int(match.group(2) or "1"),
                "new_start": int(match.group(3)),
                "new_count": int(match.group(4) or "1"),
                "header": line,
                "lines": (),
            }
        elif current is not None:
            current["lines"] = (*current["lines"], line)
    if current is not None:
        hunks.append(DiffHunk(**current))
    return tuple(hunks)


def _symbols(source: str, path: str) -> tuple[dict[str, object], str | None]:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        return {}, f"line {error.lineno}: {error.msg}"
    found: dict[str, object] = {}

    def visit(nodes: list[ast.stmt], parents: tuple[str, ...] = ()) -> None:
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = ".".join((*parents, node.name))
                found[qualname] = node
                visit(node.body, (*parents, node.name))

    visit(tree.body)
    return found, None


def _overlaps(start: int, count: int, node: ast.AST) -> bool:
    if count == 0:
        return False
    end = start + count - 1
    return start <= (node.end_lineno or node.lineno) and end >= node.lineno


def _changed_symbols(
    old_source: str | None,
    new_source: str | None,
    old_path: str,
    new_path: str,
    hunks: tuple[DiffHunk, ...],
) -> tuple[tuple[str, ...], str | None]:
    old_symbols, old_error = (
        _symbols(old_source, old_path) if old_source else ({}, None)
    )
    new_symbols, new_error = (
        _symbols(new_source, new_path) if new_source else ({}, None)
    )
    names: set[str] = set(old_symbols) ^ set(new_symbols)
    for hunk in hunks:
        names.update(
            name
            for name, node in old_symbols.items()
            if _overlaps(hunk.old_start, hunk.old_count, node)
        )
        names.update(
            name
            for name, node in new_symbols.items()
            if _overlaps(hunk.new_start, hunk.new_count, node)
        )
    return tuple(sorted(names)), old_error or new_error


class _SemanticNormalizer(ast.NodeTransformer):
    def _body(self, body: list[ast.stmt]) -> list[ast.stmt]:
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            return body[1:]
        return body

    def visit_Module(self, node: ast.Module) -> ast.AST:
        node.body = self._body(node.body)
        return self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        node.body = self._body(node.body)
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.body = self._body(node.body)
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        node.body = self._body(node.body)
        return self.generic_visit(node)


def semantic_ast(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    normalized = _SemanticNormalizer().visit(tree)
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, include_attributes=False)


def is_cosmetic_change(old_source: str | None, new_source: str | None) -> bool:
    if old_source is None or new_source is None:
        return False
    old_tree = semantic_ast(old_source)
    new_tree = semantic_ast(new_source)
    return old_tree is not None and old_tree == new_tree


def parse_diff(repo: Path, base: str, head: str) -> DiffSnapshot:
    """Parse A/M/D/R records and zero-context hunks without executing code."""

    records = _parse_name_status(_git(repo, "diff", "--name-status", "-M", base, head))
    files: list[ParsedFileDiff] = []
    for status, old_path, new_path in records:
        actual_old = old_path or new_path
        patch = _file_patch(repo, base, head, old_path, new_path)
        binary = "GIT binary patch" in patch or "Binary files " in patch
        old_source = _blob(repo, base, actual_old)
        new_source = _blob(repo, head, new_path)
        hunks = () if binary else _parse_hunks(patch)
        symbols: tuple[str, ...] = ()
        parse_error: str | None = None
        if (actual_old.endswith(".py") or new_path.endswith(".py")) and not binary:
            symbols, parse_error = _changed_symbols(
                old_source, new_source, actual_old, new_path, hunks
            )
        change = FileChange(
            path=new_path,
            status=status,
            old_path=old_path if status == "R" else None,
            new_path=new_path if status == "R" else None,
            changed_symbols=symbols,
            cosmetic_only=(
                new_path.endswith(".py")
                and not binary
                and is_cosmetic_change(old_source, new_source)
            ),
            binary=binary,
            parse_error=parse_error,
        )
        files.append(ParsedFileDiff(change, hunks, old_source, new_source))
    return DiffSnapshot(base=base, head=head, files=tuple(files))
