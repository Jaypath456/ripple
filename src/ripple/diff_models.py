"""Typed public models for deterministic Stage B verification."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

STAGE_B_CONFIG_VERSION = "stage-b-v1"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiffHunk(FrozenModel):
    old_start: int = Field(ge=0)
    old_count: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_count: int = Field(ge=0)
    header: str
    lines: tuple[str, ...]


class FileChange(FrozenModel):
    path: str
    status: Literal["A", "M", "D", "R"]
    changed_symbols: tuple[str, ...] = ()
    cosmetic_only: bool = False
    old_path: str | None = None
    new_path: str | None = None
    binary: bool = False
    parse_error: str | None = None


FindingCategory = Literal[
    "expected",
    "adjacent",
    "unexpected",
    "missing_predicted",
    "missing_test",
    "stale_caller",
]
FindingVerdict = Literal["justified", "suspicious", "unexplained", "n/a"]


class DiffFinding(FrozenModel):
    path: str
    category: FindingCategory
    verdict: FindingVerdict
    explanation: str
    evidence: tuple[str, ...] = ()


class VerificationStats(FrozenModel):
    changed_files: int = Field(ge=0)
    ignored_files: int = Field(ge=0)
    findings: int = Field(ge=0)
    investigated_files: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    runtime_seconds: float = Field(ge=0)
    config_version: str = STAGE_B_CONFIG_VERSION


class DiffAnalysis(FrozenModel):
    verification_id: str
    report_id: str
    request: str
    requested_range: str
    base: str
    head: str
    base_warning: str | None = None
    status: Literal["completed", "partial", "failed"]
    changes: tuple[FileChange, ...]
    findings: tuple[DiffFinding, ...]
    ignored_files: tuple[FileChange, ...] = ()
    file_precision: float = Field(ge=0, le=1)
    file_recall: float = Field(ge=0, le=1)
    trace_path: Path
    run_statistics: VerificationStats
    config_version: str = STAGE_B_CONFIG_VERSION


class VerificationRun(FrozenModel):
    analysis: DiffAnalysis
    json_path: Path
    markdown_path: Path
    trace_path: Path


class InvestigationDecision(FrozenModel):
    tool_name: Literal[
        "file_diff", "inspect_symbol", "find_references", "submit_verdict"
    ]
    arguments: dict[str, object] = Field(default_factory=dict)
    verdict: FindingVerdict | None = None
    explanation: str = Field(min_length=3, max_length=1000)
    evidence: tuple[str, ...] = ()
