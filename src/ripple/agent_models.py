"""Validated public models for the bounded Phase 4 agent."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AGENT_CONFIG_VERSION = "mvp-v1"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeatureRequest(FrozenModel):
    text: str = Field(min_length=8, max_length=2000)

    @field_validator("text")
    @classmethod
    def useful_text(cls, value: str) -> str:
        value = value.strip()
        if len(value.split()) < 2:
            raise ValueError("feature request must contain at least two words")
        return value


class ChangeKind(StrEnum):
    DATA_MODEL = "data_model"
    API = "api"
    AUTH = "auth"
    CONFIG = "config"
    MIGRATION = "migration"
    BUSINESS_LOGIC = "business_logic"
    UI = "ui"
    TESTS = "tests"


class FeatureIntent(FrozenModel):
    summary: str = Field(min_length=3, max_length=500)
    change_kinds: tuple[ChangeKind, ...] = Field(min_length=1)
    search_terms: tuple[str, ...] = Field(min_length=3, max_length=15)
    open_questions: tuple[str, ...] = ()

    @field_validator("search_terms")
    @classmethod
    def clean_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(
            dict.fromkeys(value.strip() for value in values if value.strip())
        )
        if len(cleaned) < 3:
            raise ValueError("at least three distinct search terms are required")
        return cleaned


CandidateStatus = Literal["suspected", "confirmed", "rejected"]


class CandidateUpdate(FrozenModel):
    target: str
    status: CandidateStatus
    reason: str = Field(min_length=3, max_length=500)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class AgentDecision(FrozenModel):
    tool_name: Literal[
        "search_code",
        "inspect_symbol",
        "find_references",
        "get_dependencies",
        "find_tests",
        "submit_report",
    ]
    arguments: dict[str, Any]
    reason: str = Field(min_length=3, max_length=500)
    ledger_updates: tuple[CandidateUpdate, ...] = ()


class AffectedComponent(FrozenModel):
    target: str
    change_type: Literal["modify", "add", "delete", "new_file"]
    change_kind: ChangeKind
    reason: str = Field(min_length=3, max_length=800)
    confidence: Literal["high", "medium", "low"]
    evidence: tuple[str, ...] = Field(min_length=1)


class SuggestedTest(FrozenModel):
    action: Literal["update", "add"]
    test_path: str
    covers: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=3, max_length=800)
    evidence: tuple[str, ...] = Field(min_length=1)


class ReportDraft(FrozenModel):
    affected_components: tuple[AffectedComponent, ...]
    schema_changes: tuple[str, ...] = ()
    api_changes: tuple[str, ...] = ()
    config_changes: tuple[str, ...] = ()
    regression_areas: tuple[str, ...] = ()
    suggested_tests: tuple[SuggestedTest, ...] = ()
    implementation_order: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    blind_spots: tuple[str, ...] = ()


class RunStats(FrozenModel):
    model: str
    tool_calls: int = Field(ge=0)
    duplicate_calls: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    runtime_seconds: float = Field(ge=0)
    stop_reason: str
    dirty: bool
    config_version: str = AGENT_CONFIG_VERSION


RunStatus = Literal["completed", "partial", "abstained", "failed"]


class ChangeImpactReport(FrozenModel):
    report_id: str
    request: str
    commit: str
    status: RunStatus
    affected_components: tuple[AffectedComponent, ...]
    schema_changes: tuple[str, ...] = ()
    api_changes: tuple[str, ...] = ()
    config_changes: tuple[str, ...] = ()
    regression_areas: tuple[str, ...] = ()
    suggested_tests: tuple[SuggestedTest, ...] = ()
    implementation_order: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    blind_spots: tuple[str, ...] = ()
    dropped_claims: tuple[str, ...] = ()
    run_stats: RunStats


class AgentRun(FrozenModel):
    report: ChangeImpactReport
    report_path: Path
    trace_path: Path
