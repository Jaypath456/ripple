import pytest

from ripple.agent_models import (
    AffectedComponent,
    CandidateUpdate,
    ReportDraft,
    SuggestedTest,
)
from ripple.ledger import CandidateLedger, EvidenceRecord
from ripple.tools import ToolResult
from ripple.validate import validate_report_draft


def _record(evidence_id: str, tool: str, target: str, *, strong: bool = True):
    return EvidenceRecord(
        evidence_id=evidence_id,
        tool_name=tool,
        arguments={"target": target},
        result=ToolResult(ok=True, data={}, evidence_id=evidence_id),
        touched_targets=frozenset({target}),
        strong=strong,
    )


def test_ledger_rejects_nonexistent_and_stale_updates(indexed_repo) -> None:
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", "src/auth.py"))
    assert not ledger.apply(
        CandidateUpdate(
            target="missing.py",
            status="suspected",
            reason="does not exist",
            evidence_ids=("e1",),
        ),
        "e1",
    )
    assert not ledger.apply(
        CandidateUpdate(
            target="src/auth.py",
            status="suspected",
            reason="stale observation",
            evidence_ids=("e1",),
        ),
        "e2",
    )
    assert ledger.candidates == {}


def test_ledger_transition_and_submission_checks(indexed_repo) -> None:
    target = "src/auth.py::refresh_token"
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", target))
    assert ledger.seed(target, "search match", "e1")
    assert ledger.apply(
        CandidateUpdate(
            target=target,
            status="confirmed",
            reason="source confirms behavior",
            evidence_ids=("e1",),
        ),
        "e1",
    )
    assert "reference and test checks" in ledger.submission_problem()
    ledger.add_evidence(_record("e2", "find_references", target))
    ledger.add_evidence(_record("e3", "find_tests", target))
    assert ledger.submission_problem() is None


def test_twice_rejected_candidate_cannot_be_resuspected(indexed_repo) -> None:
    target = "src/auth.py"
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", target))
    ledger.seed(target, "seed", "e1")
    reject = CandidateUpdate(
        target=target,
        status="rejected",
        reason="not involved",
        evidence_ids=("e1",),
    )
    assert ledger.apply(reject, "e1")
    assert not ledger.apply(reject, "e1")
    assert ledger.candidates[target].rejection_count == 2
    resuspect = CandidateUpdate(
        target=target,
        status="suspected",
        reason="try to reopen",
        evidence_ids=("e1",),
    )
    assert not ledger.apply(resuspect, "e1")
    assert ledger.candidates[target].status == "rejected"


def test_validator_drops_unconfirmed_missing_and_bad_evidence(indexed_repo) -> None:
    target = "src/auth.py::refresh_token"
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", target, strong=False))
    ledger.seed(target, "seed", "e1")
    ledger.apply(
        CandidateUpdate(
            target=target,
            status="confirmed",
            reason="confirmed",
            evidence_ids=("e1",),
        ),
        "e1",
    )
    component = AffectedComponent(
        target=target,
        change_type="modify",
        change_kind="auth",
        reason="implementation target",
        confidence="high",
        evidence=("e1", "missing"),
    )
    unconfirmed = component.model_copy(update={"target": "src/service.py"})
    result = validate_report_draft(
        ReportDraft(affected_components=(component, unconfirmed)), indexed_repo, ledger
    )
    assert len(result.components) == 1
    assert result.components[0].confidence == "medium"
    assert result.components[0].evidence == ("e1",)
    assert any("unconfirmed" in item for item in result.dropped_claims)
    assert any("confidence capped" in item for item in result.dropped_claims)
    assert any("invalid evidence removed" in item for item in result.dropped_claims)


def test_validator_requires_find_tests_evidence_and_existing_updates(
    indexed_repo,
) -> None:
    target = "src/auth.py::refresh_token"
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "find_tests", target))
    valid = SuggestedTest(
        action="update",
        test_path="tests/test_auth.py",
        covers=(target,),
        rationale="mapped test",
        evidence=("e1",),
    )
    missing = valid.model_copy(update={"test_path": "tests/missing.py"})
    unsupported = valid.model_copy(update={"evidence": ("e9",)})
    result = validate_report_draft(
        ReportDraft(
            affected_components=(), suggested_tests=(valid, missing, unsupported)
        ),
        indexed_repo,
        ledger,
    )
    assert result.tests == (valid,)
    assert len(result.dropped_claims) == 2


def test_validator_allows_clearly_proposed_new_test(indexed_repo) -> None:
    target = "src/auth.py::refresh_token"
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "find_tests", target))
    proposed = SuggestedTest(
        action="add",
        test_path="tests/test_refresh_edge.py",
        covers=(target,),
        rationale="proposed coverage for missing edge case",
        evidence=("e1",),
    )
    result = validate_report_draft(
        ReportDraft(affected_components=(), suggested_tests=(proposed,)),
        indexed_repo,
        ledger,
    )
    assert result.tests == (proposed,)
    assert result.dropped_claims == ()


@pytest.mark.parametrize(
    "target",
    [
        "src/auth.py",
        "src/auth.py::refresh_token",
        "src/service.py",
        "src/service.py::service",
        "tests/test_auth.py",
        "tests/test_auth.py::test_refresh",
    ],
)
def test_ledger_accepts_only_exact_existing_file_or_symbol_targets(
    indexed_repo, target: str
) -> None:
    ledger = CandidateLedger(indexed_repo)
    assert ledger.valid_target(target)
    assert not ledger.valid_target(target + "_invented")


def test_candidate_cap_is_enforced(indexed_repo) -> None:
    ledger = CandidateLedger(indexed_repo, cap=1)
    assert ledger.seed("src/auth.py", "first target", "e1")
    assert not ledger.seed("src/service.py", "over cap", "e1")
    assert len(ledger.candidates) == 1


def test_confirmed_candidate_is_terminal(indexed_repo) -> None:
    target = "src/auth.py"
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", target))
    ledger.seed(target, "seed", "e1")
    assert ledger.apply(
        CandidateUpdate(
            target=target,
            status="confirmed",
            reason="confirmed target",
            evidence_ids=("e1",),
        ),
        "e1",
    )
    assert not ledger.apply(
        CandidateUpdate(
            target=target,
            status="rejected",
            reason="late reversal",
            evidence_ids=("e1",),
        ),
        "e1",
    )
    assert ledger.candidates[target].status == "confirmed"


def test_evidence_must_touch_the_updated_target(indexed_repo) -> None:
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", "src/service.py"))
    assert not ledger.apply(
        CandidateUpdate(
            target="src/auth.py",
            status="suspected",
            reason="unrelated evidence",
            evidence_ids=("e1",),
        ),
        "e1",
    )


def test_new_file_component_is_dropped_even_when_target_exists(indexed_repo) -> None:
    target = "src/auth.py"
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", target))
    ledger.seed(target, "seed", "e1")
    ledger.apply(
        CandidateUpdate(
            target=target,
            status="confirmed",
            reason="confirmed target",
            evidence_ids=("e1",),
        ),
        "e1",
    )
    component = AffectedComponent(
        target=target,
        change_type="new_file",
        change_kind="auth",
        reason="invalid invention",
        confidence="low",
        evidence=("e1",),
    )
    result = validate_report_draft(
        ReportDraft(affected_components=(component,)), indexed_repo, ledger
    )
    assert result.components == ()
    assert "new-file" in result.dropped_claims[0]


@pytest.mark.parametrize(
    "field",
    [
        "schema_changes",
        "api_changes",
        "config_changes",
        "regression_areas",
        "implementation_order",
        "risks",
    ],
)
def test_phase5_narrative_claims_are_transparently_dropped(indexed_repo, field) -> None:
    result = validate_report_draft(
        ReportDraft(affected_components=(), **{field: ("speculation",)}),
        indexed_repo,
        CandidateLedger(indexed_repo),
    )
    assert result.components == ()
    assert result.dropped_claims == (f"unsupported Phase 5 narrative dropped: {field}",)
