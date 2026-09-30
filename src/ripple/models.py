"""Validated data produced by a repository scan."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

INDEX_SCHEMA_VERSION = 3


class SymbolRecord(BaseModel):
    """A structural Python symbol discovered without executing its module."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal["class", "function", "method"]
    name: str
    qualname: str
    path: Path
    start_line: int
    end_line: int
    signature: str | None
    is_async: bool
    decorators: tuple[str, ...]
    bases: tuple[str, ...]
    doc: str | None


class ImportRecord(BaseModel):
    """A Python import and its deterministic repository resolution."""

    model_config = ConfigDict(frozen=True)

    source_path: Path
    module: str
    names: tuple[str, ...]
    aliases: tuple[tuple[str, str], ...]
    target_path: Path | None
    line: int
    level: int
    is_from_import: bool
    type_checking_only: bool


class ReferenceRecord(BaseModel):
    """A static reference to a repository symbol or unresolved call target."""

    model_config = ConfigDict(frozen=True)

    source_path: Path
    line: int
    enclosing_symbol: str | None
    target_symbol: str | None
    name: str
    kind: Literal["call", "name", "attribute", "subclass", "decorator"]
    confidence: Literal["high", "low"]


class DependencyNode(BaseModel):
    """Deterministic module-level dependency facts for one tracked file."""

    model_config = ConfigDict(frozen=True)

    path: Path
    dependencies: tuple[Path, ...]
    dependents: tuple[Path, ...]
    type_checking_dependencies: tuple[Path, ...]


class TestMappingRecord(BaseModel):
    """Static evidence connecting a test file to a likely source file."""

    model_config = ConfigDict(frozen=True)

    test_path: Path
    source_path: Path
    test_symbols: tuple[str, ...]
    source_symbols: tuple[str, ...]
    reasons: tuple[Literal["imports", "references", "naming"], ...]


class FileRecord(BaseModel):
    """A tracked Python file and its derived repository facts."""

    model_config = ConfigDict(frozen=True)

    path: Path
    module: str
    is_test: bool
    symbol_ids: tuple[str, ...] = ()
    parse_error: str | None = None


class RepositoryIndex(BaseModel):
    """The deterministic facts captured for a Git repository."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = INDEX_SCHEMA_VERSION
    repo_root: Path
    commit: str
    dirty: bool
    created_at: datetime
    files: tuple[FileRecord, ...]
    symbols: tuple[SymbolRecord, ...]
    imports: tuple[ImportRecord, ...]
    references: tuple[ReferenceRecord, ...]
    dependency_graph: tuple[DependencyNode, ...]
    test_mappings: tuple[TestMappingRecord, ...]
