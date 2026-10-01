"""Deterministic bounded tools over a cached RepositoryIndex."""

import difflib
import json
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ripple.facts import FACT_KINDS, FACT_LIMIT, FactModel, extract_repository_facts
from ripple.graph import fan_in, neighbors
from ripple.history import HistoryError, co_changed
from ripple.models import RepositoryIndex, SymbolRecord
from ripple.search import SearchIndex

SEARCH_LIMIT = 15
INSPECT_SOURCE_LINES = 120
INSPECT_OUTLINE_SYMBOLS = 100
REFERENCE_LIMIT = 40
DEPENDENCY_LIMIT = 100
TEST_LIMIT = 50


class ToolError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    hint: str | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    data: Any | None = None
    error: ToolError | None = None
    evidence_id: str
    truncated: bool = False


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SearchCodeArgs(_Arguments):
    query: str
    kind: Literal["any", "symbol", "file", "string"] = "any"
    limit: int = Field(default=10, ge=1, le=SEARCH_LIMIT)


class InspectSymbolArgs(_Arguments):
    target: str


class FindReferencesArgs(_Arguments):
    symbol_id: str
    limit: int = Field(default=REFERENCE_LIMIT, ge=1, le=REFERENCE_LIMIT)


class GetDependenciesArgs(_Arguments):
    path: str
    direction: Literal["imports", "imported_by"]
    depth: Literal[1, 2] = 1


class FindTestsArgs(_Arguments):
    target: str


class RepoFactsArgs(_Arguments):
    kind: str
    filter: str | None = None


class CoChangedArgs(_Arguments):
    path: str
    limit: int = Field(default=10, ge=1, le=10)


@dataclass(frozen=True)
class _ToolFailure(Exception):
    code: str
    message: str
    hint: str | None = None


_ARGUMENT_MODELS: dict[str, type[_Arguments]] = {
    "search_code": SearchCodeArgs,
    "inspect_symbol": InspectSymbolArgs,
    "find_references": FindReferencesArgs,
    "get_dependencies": GetDependenciesArgs,
    "find_tests": FindTestsArgs,
    "repo_facts": RepoFactsArgs,
    "co_changed": CoChangedArgs,
}

TOOL_NAMES = tuple(_ARGUMENT_MODELS)


def validate_tool_arguments(name: str, arguments: object) -> dict[str, Any]:
    """Return canonical validated arguments or raise ``ValueError``."""

    argument_model = _ARGUMENT_MODELS.get(name)
    if argument_model is None:
        raise ValueError(f"unknown tool: {name}")
    try:
        validated = argument_model.model_validate(arguments)
    except ValidationError as error:
        raise ValueError("tool arguments failed validation") from error
    return validated.model_dump(mode="json")


def error_result(
    evidence_id: str,
    code: str,
    message: str,
    hint: str | None = None,
) -> ToolResult:
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message=message, hint=hint),
        evidence_id=evidence_id,
    )


class ToolSession:
    """A repository-scoped tool dispatcher with sequential evidence IDs."""

    def __init__(self, index: RepositoryIndex) -> None:
        self.index = index
        self._next_evidence = 1
        self._search_index: SearchIndex | None = None
        self._fact_cache: dict[str, tuple[FactModel, ...]] | None = None

    def _evidence_id(self) -> str:
        evidence_id = f"e{self._next_evidence}"
        self._next_evidence += 1
        return evidence_id

    def invoke(self, name: str, arguments: object) -> ToolResult:
        evidence_id = self._evidence_id()
        argument_model = _ARGUMENT_MODELS.get(name)
        if argument_model is None:
            return error_result(
                evidence_id,
                "unknown_tool",
                f"unknown tool: {name}",
                f"available tools: {', '.join(TOOL_NAMES)}",
            )
        try:
            validated = argument_model.model_validate(arguments)
        except ValidationError as error:
            return error_result(
                evidence_id,
                "invalid_arguments",
                "tool arguments failed validation",
                str(error),
            )

        try:
            handler = getattr(self, f"_{name}")
            return handler(validated, evidence_id)
        except _ToolFailure as error:
            return error_result(
                evidence_id,
                error.code,
                error.message,
                error.hint,
            )

    def _safe_path(self, raw_path: str) -> Path:
        supplied = Path(raw_path)
        if supplied.is_absolute():
            raise _ToolFailure(
                "path_outside_repo",
                "tool paths must be repository-relative",
            )
        root = self.index.repo_root.resolve()
        candidate = (root / supplied).resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise _ToolFailure(
                "path_outside_repo",
                f"path escapes repository root: {raw_path}",
            ) from error
        return Path(relative.as_posix())

    def _split_target(self, target: str) -> tuple[Path, str | None]:
        path_text, separator, symbol_name = target.partition("::")
        path = self._safe_path(path_text)
        return path, symbol_name if separator else None

    def _closest(self, target: str, candidates: list[str]) -> str | None:
        matches = difflib.get_close_matches(target, candidates, n=3, cutoff=0.3)
        return f"closest matches: {', '.join(matches)}" if matches else None

    def _symbol(self, supplied: str) -> SymbolRecord:
        if "::" in supplied:
            path, qualname = self._split_target(supplied)
            normalized = f"{path.as_posix()}::{qualname}"
            symbol = next(
                (symbol for symbol in self.index.symbols if symbol.id == normalized),
                None,
            )
            if symbol is not None:
                return symbol
            hint = self._closest(
                normalized, [symbol.id for symbol in self.index.symbols]
            )
            raise _ToolFailure("not_found", f"symbol not found: {normalized}", hint)

        matches = [
            symbol
            for symbol in self.index.symbols
            if symbol.name == supplied or symbol.qualname == supplied
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            candidates = ", ".join(symbol.id for symbol in matches[:10])
            raise _ToolFailure(
                "ambiguous_name",
                f"symbol name is ambiguous: {supplied}",
                f"use a full symbol ID; candidates: {candidates}",
            )
        hint = self._closest(supplied, [symbol.id for symbol in self.index.symbols])
        raise _ToolFailure("not_found", f"symbol not found: {supplied}", hint)

    def _search_code(self, arguments: SearchCodeArgs, evidence_id: str) -> ToolResult:
        if not arguments.query.strip():
            return error_result(
                evidence_id,
                "empty_query",
                "search query must contain non-whitespace text",
            )
        if self._search_index is None:
            self._search_index = SearchIndex(self.index)
        hits, truncated = self._search_index.search(
            arguments.query,
            arguments.kind,
            arguments.limit,
        )
        if not hits:
            return error_result(
                evidence_id,
                "no_results",
                f"no {arguments.kind} results for query: {arguments.query}",
            )
        return ToolResult(
            ok=True,
            data={"hits": [hit.model_dump(mode="json") for hit in hits]},
            evidence_id=evidence_id,
            truncated=truncated,
        )

    def _inspect_symbol(
        self, arguments: InspectSymbolArgs, evidence_id: str
    ) -> ToolResult:
        path, qualname = self._split_target(arguments.target)
        file = next((file for file in self.index.files if file.path == path), None)
        if file is None:
            candidates = [file.path.as_posix() for file in self.index.files]
            return error_result(
                evidence_id,
                "not_found",
                f"file not found: {path}",
                self._closest(path.as_posix(), candidates),
            )
        if file.parse_error is not None:
            return error_result(
                evidence_id,
                "parse_error",
                f"cannot inspect {path}: {file.parse_error}",
            )

        file_symbols = tuple(
            symbol for symbol in self.index.symbols if symbol.path == path
        )
        if qualname is None:
            selected = file_symbols[:INSPECT_OUTLINE_SYMBOLS]
            return ToolResult(
                ok=True,
                data={
                    "path": path.as_posix(),
                    "module": file.module,
                    "outline": [
                        {
                            "id": symbol.id,
                            "kind": symbol.kind,
                            "signature": symbol.signature,
                            "decorators": list(symbol.decorators),
                            "start_line": symbol.start_line,
                            "end_line": symbol.end_line,
                        }
                        for symbol in selected
                    ],
                },
                evidence_id=evidence_id,
                truncated=len(file_symbols) > INSPECT_OUTLINE_SYMBOLS,
            )

        symbol_id = f"{path.as_posix()}::{qualname}"
        symbol = next(
            (symbol for symbol in file_symbols if symbol.id == symbol_id), None
        )
        if symbol is None:
            return error_result(
                evidence_id,
                "not_found",
                f"symbol not found: {symbol_id}",
                self._closest(symbol_id, [item.id for item in file_symbols]),
            )
        try:
            with tokenize.open(self.index.repo_root / path) as source_file:
                lines = source_file.read().splitlines()
        except (OSError, SyntaxError, UnicodeError) as error:
            return error_result(
                evidence_id,
                "parse_error",
                f"cannot read source for {symbol_id}: {error}",
            )
        source_lines = lines[symbol.start_line - 1 : symbol.end_line]
        truncated = len(source_lines) > INSPECT_SOURCE_LINES
        source = "\n".join(source_lines[:INSPECT_SOURCE_LINES])
        return ToolResult(
            ok=True,
            data={
                "id": symbol.id,
                "kind": symbol.kind,
                "signature": symbol.signature,
                "decorators": list(symbol.decorators),
                "doc": symbol.doc,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "source": source,
            },
            evidence_id=evidence_id,
            truncated=truncated,
        )

    def _find_references(
        self, arguments: FindReferencesArgs, evidence_id: str
    ) -> ToolResult:
        symbol = self._symbol(arguments.symbol_id)
        records: list[dict[str, object]] = [
            {
                "path": reference.source_path.as_posix(),
                "line": reference.line,
                "enclosing_symbol": reference.enclosing_symbol,
                "kind": reference.kind,
                "confidence": reference.confidence,
            }
            for reference in self.index.references
            if reference.target_symbol == symbol.id
        ]
        for imported in self.index.imports:
            if (
                imported.is_from_import
                and imported.target_path == symbol.path
                and symbol.name in imported.names
            ):
                records.append(
                    {
                        "path": imported.source_path.as_posix(),
                        "line": imported.line,
                        "enclosing_symbol": None,
                        "kind": "import",
                        "confidence": "high",
                    }
                )
        unique = {
            (
                record["path"],
                record["line"],
                record["enclosing_symbol"],
                record["kind"],
            ): record
            for record in records
        }
        ordered = sorted(
            unique.values(),
            key=lambda item: (
                str(item["path"]),
                int(item["line"]),
                str(item["kind"]),
                str(item["enclosing_symbol"] or ""),
            ),
        )
        selected = ordered[: arguments.limit]
        return ToolResult(
            ok=True,
            data={"symbol_id": symbol.id, "references": selected},
            evidence_id=evidence_id,
            truncated=len(ordered) > arguments.limit,
        )

    def _get_dependencies(
        self, arguments: GetDependenciesArgs, evidence_id: str
    ) -> ToolResult:
        path = self._safe_path(arguments.path)
        tracked_paths = {file.path for file in self.index.files}
        if path not in tracked_paths:
            if any(
                imported.target_path is None and imported.module == arguments.path
                for imported in self.index.imports
            ):
                return error_result(
                    evidence_id,
                    "external_module",
                    f"external modules are not dependency-graph nodes: {arguments.path}",
                )
            return error_result(
                evidence_id,
                "not_found",
                f"tracked Python file not found: {path}",
                self._closest(
                    path.as_posix(),
                    [item.as_posix() for item in sorted(tracked_paths)],
                ),
            )
        found = neighbors(
            self.index.dependency_graph,
            path,
            arguments.direction,
            arguments.depth,
        )
        selected = found[:DEPENDENCY_LIMIT]
        return ToolResult(
            ok=True,
            data={
                "path": path.as_posix(),
                "direction": arguments.direction,
                "depth": arguments.depth,
                "fan_in": fan_in(self.index.dependency_graph, path),
                "neighbors": [
                    {"path": item.path.as_posix(), "depth": item.depth}
                    for item in selected
                ],
            },
            evidence_id=evidence_id,
            truncated=len(found) > DEPENDENCY_LIMIT,
        )

    def _find_tests(self, arguments: FindTestsArgs, evidence_id: str) -> ToolResult:
        path, qualname = self._split_target(arguments.target)
        tracked_paths = {file.path for file in self.index.files}
        if path not in tracked_paths:
            return error_result(
                evidence_id,
                "not_found",
                f"tracked Python file not found: {path}",
            )
        symbol_id: str | None = None
        if qualname is not None:
            symbol_id = f"{path.as_posix()}::{qualname}"
            if not any(symbol.id == symbol_id for symbol in self.index.symbols):
                return error_result(
                    evidence_id,
                    "not_found",
                    f"symbol not found: {symbol_id}",
                )

        results: list[dict[str, object]] = []
        for mapping in self.index.test_mappings:
            if mapping.source_path != path:
                continue
            test_symbols = mapping.test_symbols or (None,)
            for test_symbol in test_symbols:
                results.append(
                    {
                        "test_path": mapping.test_path.as_posix(),
                        "test_symbol": test_symbol,
                        "match_reasons": list(mapping.reasons),
                        "references_target": (
                            symbol_id is not None
                            and symbol_id in mapping.source_symbols
                        ),
                    }
                )
        results.sort(
            key=lambda item: (
                str(item["test_path"]),
                str(item["test_symbol"] or ""),
            )
        )
        selected = results[:TEST_LIMIT]
        return ToolResult(
            ok=True,
            data={
                "target": symbol_id or path.as_posix(),
                "tests": selected,
            },
            evidence_id=evidence_id,
            truncated=len(results) > TEST_LIMIT,
        )

    def _repo_facts(self, arguments: RepoFactsArgs, evidence_id: str) -> ToolResult:
        if arguments.kind not in FACT_KINDS:
            return error_result(
                evidence_id,
                "unsupported_kind",
                f"unsupported repository fact kind: {arguments.kind}",
                f"supported kinds: {', '.join(FACT_KINDS)}",
            )
        if self._fact_cache is None:
            self._fact_cache = extract_repository_facts(self.index)
        facts = [
            fact.model_dump(mode="json") for fact in self._fact_cache[arguments.kind]
        ]
        if arguments.filter:
            needle = arguments.filter.casefold()
            facts = [
                fact
                for fact in facts
                if needle in json.dumps(fact, sort_keys=True).casefold()
            ]
        truncated = len(facts) > FACT_LIMIT
        facts = facts[:FACT_LIMIT]
        if not facts:
            return error_result(
                evidence_id,
                "none_detected",
                f"no {arguments.kind} facts were detected",
            )
        return ToolResult(
            ok=True,
            data={
                "kind": arguments.kind,
                "facts": facts,
            },
            evidence_id=evidence_id,
            truncated=truncated,
        )

    def _co_changed(self, arguments: CoChangedArgs, evidence_id: str) -> ToolResult:
        path = self._safe_path(arguments.path)
        try:
            result = co_changed(
                self.index.repo_root, path.as_posix(), limit=arguments.limit
            )
        except HistoryError as error:
            return error_result(evidence_id, error.code, str(error), error.hint)
        return ToolResult(
            ok=True,
            data=result.model_dump(mode="json"),
            evidence_id=evidence_id,
            truncated=result.truncated,
        )
