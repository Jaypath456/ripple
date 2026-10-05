"""Validated public models for the bounded change-impact agent."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AGENT_CONFIG_VERSION = "mvp-v1.1"
MVP_CONFIG_VERSION = AGENT_CONFIG_VERSION
FULL_REPORT_CONFIG_VERSION = "full-report-v1"
# V2 adds the candidate-decision checkpoint protocol (V2.1: new-feature decision
# framing); V1 remains pinned for history.
FULL_REPORT_V2_CONFIG_VERSION = "full-report-v2.1"


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
        "repo_facts",
        "co_changed",
        "submit_report",
    ]
    arguments: dict[str, Any]
    reason: str = Field(min_length=3, max_length=500)
    ledger_updates: tuple[CandidateUpdate, ...] = ()


class CandidateDecision(FrozenModel):
    """One model proposal at a V2 decision checkpoint; Python validates it."""

    target: str
    decision: Literal["confirm", "reject", "keep"]
    evidence_ids: tuple[str, ...] = ()
    reason: str = Field(min_length=3, max_length=500)
    missing_evidence: str | None = Field(default=None, max_length=300)


class CandidateDecisionSet(FrozenModel):
    decisions: tuple[CandidateDecision, ...]


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


class GroundedClaim(FrozenModel):
    description: str = Field(min_length=3, max_length=1000)
    targets: tuple[str, ...] = Field(min_length=1)
    evidence: tuple[str, ...] = Field(min_length=1)


class RegressionArea(FrozenModel):
    target: str
    reason: str = Field(min_length=3, max_length=800)
    evidence: tuple[str, ...] = Field(min_length=1)
    score: int = Field(default=0, ge=0)


class Risk(FrozenModel):
    description: str = Field(min_length=3, max_length=1000)
    severity: Literal["low", "medium", "high"]
    related_targets: tuple[str, ...] = Field(min_length=1)
    evidence: tuple[str, ...] = Field(min_length=1)


class BlindSpot(FrozenModel):
    description: str = Field(min_length=3, max_length=1000)
    evidence: tuple[str, ...] = ()


class ReportDraft(FrozenModel):
    affected_components: tuple[AffectedComponent, ...]
    schema_changes: tuple[GroundedClaim | str, ...] = ()
    api_changes: tuple[GroundedClaim | str, ...] = ()
    config_changes: tuple[GroundedClaim | str, ...] = ()
    regression_areas: tuple[RegressionArea | str, ...] = ()
    suggested_tests: tuple[SuggestedTest, ...] = ()
    implementation_order: tuple[str, ...] = ()
    risks: tuple[Risk | str, ...] = ()
    blind_spots: tuple[BlindSpot | str, ...] = ()


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
    config_version: str = FULL_REPORT_CONFIG_VERSION
    decision_checkpoints: int = Field(default=0, ge=0)
    invalid_tool_targets: int = Field(default=0, ge=0)


RunStatus = Literal["completed", "partial", "abstained", "failed"]


class ChangeImpactReport(FrozenModel):
    report_id: str
    request: str
    commit: str
    status: RunStatus
    affected_components: tuple[AffectedComponent, ...]
    schema_changes: tuple[GroundedClaim, ...] = ()
    api_changes: tuple[GroundedClaim, ...] = ()
    config_changes: tuple[GroundedClaim, ...] = ()
    regression_areas: tuple[RegressionArea, ...] = ()
    suggested_tests: tuple[SuggestedTest, ...] = ()
    implementation_order: tuple[str, ...] = ()
    risks: tuple[Risk, ...] = ()
    blind_spots: tuple[BlindSpot, ...] = ()
    dropped_claims: tuple[str, ...] = ()
    run_stats: RunStats


class AgentRun(FrozenModel):
    report: ChangeImpactReport
    report_path: Path
    trace_path: Path
    markdown_path: Path


class RankedPathDraft(FrozenModel):
    paths: tuple[str, ...]


class BaselineDecision(FrozenModel):
    tool_name: Literal[
        "search_code",
        "inspect_symbol",
        "find_references",
        "get_dependencies",
        "find_tests",
        "repo_facts",
        "co_changed",
        "submit_report",
    ]
    arguments: dict[str, Any] = {}
    predicted_paths: tuple[str, ...] = ()
