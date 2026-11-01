"""P1 provenance, atomic refusals and unchanged V2 checkpoint gates."""

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from test_validation import _record

from ripple.agent import RIPPLE_V2, AgentController
from ripple.agent_models import (
    AffectedComponent,
    AgentDecision,
    CandidateUpdate,
    FeatureRequest,
    GroundedClaim,
    ReportDraft,
    Risk,
    SuggestedTest,
)
from ripple.ledger import CandidateLedger, EvidenceRecord
from ripple.llm import ScriptedLLM
from ripple.tools import ToolResult
from ripple.validate import validate_report_draft


def update(target="src/auth.py", status="confirmed", ids=("e1",)):
    return CandidateUpdate(
        target=target, status=status, reason="structural evidence", evidence_ids=ids
    )


def failed_record(evidence_id="e2", target="src/auth.py"):
    return EvidenceRecord(
        evidence_id,
        "inspect_symbol",
        {"target": target},
        ToolResult(ok=False, evidence_id=evidence_id),
        frozenset({target}),
        True,
    )


def test_failed_observation_does_not_replace_latest_success(indexed_repo, tmp_path):
    controller = AgentController(
        indexed_repo, ScriptedLLM(), output_root=tmp_path / "out"
    )
    good = _record("e1", "inspect_symbol", "src/auth.py")
    bad = failed_record()
    for record in [good, bad]:
        controller.ledger.add_evidence(record)
        controller.observations.append(
            {"result": record.result.model_dump(mode="json")}
        )
    controller.ledger.seed("src/auth.py", "seed", "e1")
    # P0 selected e2, which made this legitimate e1-only update fail.
    assert controller._latest_successful_evidence() == "e1"
    decision = AgentDecision(
        tool_name="submit_report",
        arguments={},
        reason="ready",
        ledger_updates=(update(),),
    )
    assert controller._apply_updates(decision, controller._latest_successful_evidence())
    assert controller.ledger.candidates["src/auth.py"].status == "confirmed"
    assert list(controller.ledger.evidence) == ["e1", "e2"]
    assert controller.observations[-1]["result"]["ok"] is False


def test_only_failed_and_unknown_observations_give_no_required_support(indexed_repo):
    controller = AgentController(indexed_repo, ScriptedLLM())
    controller.ledger.add_evidence(failed_record())
    controller.observations = [
        {"result": {"ok": False, "evidence_id": "e2"}},
        {"result": {"ok": True, "evidence_id": "invented"}},
    ]
    assert controller._latest_successful_evidence() is None
    assert not controller.ledger.apply(update(ids=("e2",)), None)
    assert not controller.ledger.seed("src/auth.py", "failed", "e2")


@pytest.mark.parametrize("mode", ["apply", "checkpoint"])
def test_failed_evidence_cannot_support_even_a_forged_strong_record(indexed_repo, mode):
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", "src/auth.py"))
    ledger.seed("src/auth.py", "seed", "e1")
    ledger.add_evidence(failed_record())
    before = copy.deepcopy(ledger.candidates)
    result = (
        ledger.apply_checked(update(ids=("e2",)), "e2")
        if mode == "apply"
        else ledger.decide_checked(
            "src/auth.py", "confirm", ("e2",), "forged", frozenset({"e2"})
        )
    )
    assert not result.changed and result.reason_code == "failed_evidence"
    assert ledger.candidates == before
    draft = ReportDraft(
        affected_components=(
            AffectedComponent(
                target="src/auth.py",
                change_type="modify",
                change_kind="auth",
                reason="forged support",
                confidence="high",
                evidence=("e2",),
            ),
        )
    )
    ledger.apply(update(), "e1")
    assert not validate_report_draft(draft, indexed_repo, ledger).components


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", "invalid_target"),
        ("stale", "latest_evidence_required"),
        ("unknown", "unknown_evidence"),
        ("untouched", "untouched_target"),
        ("repeat_reject", "already_rejected"),
        ("confirmed", "already_confirmed"),
    ],
)
def test_action_refusals_have_codes_and_are_atomic(indexed_repo, case, code):
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", "src/auth.py"))
    ledger.seed("src/auth.py", "seed", "e1")
    proposed = update()
    latest = "e1"
    if case == "missing":
        proposed = update(target="absent.py")
    if case == "stale":
        latest = "e9"
    if case == "unknown":
        proposed = update(ids=("e1", "absent"))
    if case == "untouched":
        proposed = update(target="src/service.py")
    if case == "repeat_reject":
        proposed = update(status="rejected")
        assert ledger.apply(proposed, latest)
    if case == "confirmed":
        assert ledger.apply(proposed, latest)
    before = copy.deepcopy((ledger.candidates, ledger.evidence))
    outcome = ledger.apply_checked(proposed, latest)
    assert not outcome.changed and outcome.reason_code == code
    assert (ledger.candidates, ledger.evidence) == before


@pytest.mark.parametrize(
    "decision,ids,presented,code",
    [
        ("invented", ("e1",), {"e1"}, "invalid_decision"),
        ("confirm", (), {"e1"}, "no_evidence"),
        ("confirm", ("e9",), {"e1"}, "not_presented"),
        ("confirm", ("e1",), {"e1"}, "strong_evidence_required"),
    ],
)
def test_checkpoint_refusals_are_atomic(indexed_repo, decision, ids, presented, code):
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "search_code", "src/auth.py", strong=False))
    ledger.seed("src/auth.py", "seed", "e1")
    before = copy.deepcopy(ledger.candidates)
    result = ledger.decide_checked(
        "src/auth.py", decision, ids, "proposal", frozenset(presented)
    )
    assert result.reason_code == code and not result.changed
    assert ledger.candidates == before


def test_evidence_is_append_only_and_isolated_from_caller_mutations(indexed_repo):
    ledger = CandidateLedger(indexed_repo)
    record = _record("e1", "inspect_symbol", "src/auth.py")
    ledger.add_evidence(record)
    record.arguments["target"] = "changed"
    assert ledger.evidence["e1"].arguments["target"] == "src/auth.py"
    with pytest.raises(FrozenInstanceError):
        record.strong = False
    with pytest.raises(ValueError, match="append-only"):
        ledger.add_evidence(record)
    bad = EvidenceRecord("e9", "inspect_symbol", {}, record.result, frozenset(), False)
    with pytest.raises(ValueError, match="does not match"):
        ledger.add_evidence(bad)
    assert list(ledger.evidence) == ["e1"]


def test_v2_failure_does_not_relax_checkpoint_only_confirmation(indexed_repo, tmp_path):
    controller = AgentController(
        indexed_repo, ScriptedLLM(), variant=RIPPLE_V2, output_root=tmp_path / "out"
    )
    controller.ledger.add_evidence(_record("e1", "inspect_symbol", "src/auth.py"))
    controller.ledger.seed("src/auth.py", "seed", "e1")
    decision = AgentDecision(
        tool_name="submit_report",
        arguments={},
        reason="ready",
        ledger_updates=(update(),),
    )
    before = copy.deepcopy(controller.ledger.candidates)
    assert not controller._apply_updates(decision, "e1")
    assert controller.ledger.candidates == before
    event = json.loads(controller.trace.path.read_text().splitlines()[-1])
    assert event["reason_code"] == "checkpoint_required"


def test_checkpoint_visibility_is_additive(indexed_repo, tmp_path: Path):
    provider = ScriptedLLM(
        candidate_decisions=[
            {
                "decisions": [
                    {
                        "target": "src/auth.py",
                        "decision": "confirm",
                        "reason": "inspected implementation",
                        "evidence_ids": ["e1"],
                    }
                ]
            }
        ]
    )
    controller = AgentController(
        indexed_repo, provider, variant=RIPPLE_V2, output_root=tmp_path / "out"
    )
    controller._execute("inspect_symbol", {"target": "src/auth.py"})
    controller.ledger.seed("src/auth.py", "seed", "e1")
    assert controller._checkpoint(
        FeatureRequest(text="Refresh authentication tokens"), stall=0, force=True
    )
    events = [
        json.loads(line) for line in controller.trace.path.read_text().splitlines()
    ]
    assert any(event["event"] == "checkpoint_visibility" for event in events)
    accepted = next(event for event in events if event["event"] == "candidate_decision")
    assert (
        accepted["accepted"]
        and accepted["refusal"] is None
        and accepted["reason_code"] is None
    )


@pytest.mark.parametrize(
    "section",
    ["api_changes", "schema_changes", "config_changes", "risks", "suggested_tests"],
)
def test_failed_evidence_is_rejected_from_every_claim_section(indexed_repo, section):
    ledger = CandidateLedger(indexed_repo)
    ledger.add_evidence(_record("e1", "inspect_symbol", "src/auth.py"))
    ledger.seed("src/auth.py", "seed", "e1")
    assert ledger.apply(update(), "e1")
    record = EvidenceRecord(
        "e2",
        "find_tests",
        {},
        ToolResult(
            ok=False,
            evidence_id="e2",
            data={"tests": [{"test_path": "tests/test_auth.py"}]},
        ),
        frozenset({"src/auth.py"}),
        True,
    )
    ledger.add_evidence(record)
    if section == "risks":
        claim = Risk(
            description="unsupported risk",
            severity="high",
            related_targets=("src/auth.py",),
            evidence=("e2",),
        )
    elif section == "suggested_tests":
        claim = SuggestedTest(
            action="update",
            test_path="tests/test_auth.py",
            covers=("src/auth.py",),
            rationale="unsupported test",
            evidence=("e2",),
        )
    else:
        claim = GroundedClaim(
            description="unsupported change", targets=("src/auth.py",), evidence=("e2",)
        )
    draft = ReportDraft(affected_components=(), **{section: (claim,)})
    validated = validate_report_draft(draft, indexed_repo, ledger)
    assert not getattr(validated, "tests" if section == "suggested_tests" else section)


def test_legacy_seed_placeholder_cannot_be_used_as_proposal_support(indexed_repo):
    ledger = CandidateLedger(indexed_repo)
    assert ledger.seed("src/auth.py", "trusted fixture seed", "placeholder")
    assert (
        ledger.apply_checked(update(ids=("placeholder",)), "placeholder").reason_code
        == "unknown_evidence"
    )
    assert not ledger.decide(
        "src/auth.py",
        "confirm",
        ("placeholder",),
        "fabricated",
        frozenset({"placeholder"}),
    )[0]
    assert ledger.candidates["src/auth.py"].status == "suspected"


def test_refused_checkpoint_keep_cannot_write_missing_notes(indexed_repo, tmp_path):
    provider = ScriptedLLM(
        candidate_decisions=[
            {
                "decisions": [
                    {
                        "target": "absent.py",
                        "decision": "keep",
                        "reason": "unoffered target",
                        "missing_evidence": "must not be stored",
                    }
                ]
            }
        ]
    )
    controller = AgentController(
        indexed_repo, provider, variant=RIPPLE_V2, output_root=tmp_path / "out"
    )
    controller._execute("inspect_symbol", {"target": "src/auth.py"})
    controller.ledger.seed("src/auth.py", "seed", "e1")
    before = copy.deepcopy((controller.ledger.candidates, controller.missing_notes))
    assert not controller._checkpoint(
        FeatureRequest(text="Refresh authentication tokens"), stall=0, force=True
    )
    assert (controller.ledger.candidates, controller.missing_notes) == before
    event = json.loads(controller.trace.path.read_text().splitlines()[-1])
    assert event["reason_code"] == "target_not_offered"
