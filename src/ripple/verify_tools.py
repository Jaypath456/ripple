"""Small Stage B-only investigation tool session."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ripple.diffing import DiffSnapshot
from ripple.models import RepositoryIndex
from ripple.tools import ToolResult, ToolSession, error_result


class FileDiffArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str


@dataclass(frozen=True)
class VerificationEvidence:
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    touched_paths: frozenset[str]


class VerificationToolSession:
    """Expose only file_diff, inspect_symbol, and find_references."""

    def __init__(self, index: RepositoryIndex, snapshot: DiffSnapshot) -> None:
        self.index = index
        self.snapshot = snapshot
        self._base = ToolSession(index)
        self._next = 1
        self.evidence: dict[str, VerificationEvidence] = {}
        self._tracked = {item.path.as_posix() for item in index.files}
        self._tracked.update(item.change.path for item in snapshot.files)
        self._tracked.update(
            item.change.old_path
            for item in snapshot.files
            if item.change.old_path is not None
        )

    def _evidence_id(self) -> str:
        value = f"v{self._next}"
        self._next += 1
        return value

    def _safe_path(self, raw: str) -> str | None:
        path = Path(PurePosixPath(raw))
        if path.is_absolute() or ".." in path.parts or path == Path("."):
            return None
        return path.as_posix()

    def invoke(self, name: str, arguments: object) -> ToolResult:
        evidence_id = self._evidence_id()
        if name == "file_diff":
            try:
                validated = FileDiffArgs.model_validate(arguments)
            except ValidationError as error:
                return error_result(
                    evidence_id,
                    "invalid_arguments",
                    "tool arguments failed validation",
                    str(error),
                )
            result, touched = self._file_diff(validated.path, evidence_id)
        elif name in {"inspect_symbol", "find_references"}:
            delegated = self._base.invoke(name, arguments)
            result = delegated.model_copy(update={"evidence_id": evidence_id})
            touched = self._delegated_touched(name, arguments, result)
        else:
            return error_result(
                evidence_id,
                "unknown_tool",
                f"unknown Stage B tool: {name}",
                "available tools: file_diff, inspect_symbol, find_references",
            )
        self.evidence[evidence_id] = VerificationEvidence(
            tool_name=name,
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
            result=result,
            touched_paths=frozenset(touched),
        )
        return result

    def _delegated_touched(
        self, name: str, arguments: object, result: ToolResult
    ) -> set[str]:
        touched: set[str] = set()
        data = result.data if result.ok and isinstance(result.data, dict) else {}
        if name == "inspect_symbol":
            target = (
                str(arguments.get("target", "")) if isinstance(arguments, dict) else ""
            )
            touched.add(target.partition("::")[0])
            if data.get("path"):
                touched.add(str(data["path"]))
            if data.get("id"):
                touched.add(str(data["id"]).partition("::")[0])
        else:
            target = str(data.get("symbol_id", ""))
            touched.add(target.partition("::")[0])
            touched.update(str(item.get("path")) for item in data.get("references", []))
        return {item for item in touched if item}

    def _file_diff(
        self, raw_path: str, evidence_id: str
    ) -> tuple[ToolResult, set[str]]:
        path = self._safe_path(raw_path)
        if path is None:
            return (
                error_result(
                    evidence_id,
                    "invalid_arguments",
                    "file_diff path must be repository-relative",
                ),
                set(),
            )
        item = self.snapshot.by_path(path)
        if item is None:
            code = "not_in_diff" if path in self._tracked else "not_found"
            return (
                error_result(
                    evidence_id,
                    code,
                    f"path is {'not in the diff' if code == 'not_in_diff' else 'not found'}: {path}",
                ),
                {path},
            )
        if item.change.binary:
            return (
                error_result(
                    evidence_id,
                    "binary_file",
                    f"binary diff is not available for: {path}",
                ),
                {item.change.path, item.change.old_path or item.change.path},
            )
        snippets: list[str] = []
        for hunk in item.hunks[:20]:
            snippets.append("\n".join((hunk.header, *hunk.lines[:40])))
        return (
            ToolResult(
                ok=True,
                evidence_id=evidence_id,
                truncated=len(item.hunks) > 20
                or any(len(hunk.lines) > 40 for hunk in item.hunks[:20]),
                data={
                    "path": item.change.path,
                    "old_path": item.change.old_path,
                    "status": item.change.status,
                    "changed_symbols": list(item.change.changed_symbols),
                    "old_ranges": [
                        {"start": hunk.old_start, "count": hunk.old_count}
                        for hunk in item.hunks
                    ],
                    "new_ranges": [
                        {"start": hunk.new_start, "count": hunk.new_count}
                        for hunk in item.hunks
                    ],
                    "snippets": snippets,
                },
            ),
            {item.change.path, item.change.old_path or item.change.path},
        )
