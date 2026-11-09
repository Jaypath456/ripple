"""Reviewed P3 runtime, strict defaults, exact experimental flags and local stops."""

import copy
import dataclasses
import inspect
import json
from pathlib import Path

import pytest
from test_p2b_request_formatting import approved, synthetic_sdk

from ripple import pilot
from ripple import pilot_experimental as experimental
from ripple.agent import RIPPLE_V2, RIPPLE_V2_BOUNDED, AgentController
from ripple.context_budget import ContextBudgetExceeded
from ripple.pilot_admission import PilotStop, inspect_journal, read_journal
from ripple.pilot_formatting import FORMAT_ID, verify_journal_formatting

ROOT = Path(__file__).parents[1]
ACCURACY_FLAGS = (
    "proof_selection",
    "diverse_seeding",
    "review_progression",
    "precision_guard",
    "confidence_tiers",
)


@pytest.fixture
def simulated_identity(monkeypatch):
    identity = experimental.repository_identity(require_clean=False)
    monkeypatch.setattr(experimental, "repository_identity", lambda **kw: identity)
    return identity


def test_strict_default_and_first_experiment_policy():
    assert inspect.signature(pilot.run_pair).parameters["variant"].default == RIPPLE_V2
    assert not RIPPLE_V2.bounded_context
    policy = experimental.specification()
    assert policy["request_formatting"] == FORMAT_ID
    assert policy["agent_variant"] == dataclasses.asdict(RIPPLE_V2_BOUNDED)
    assert policy["agent_variant"]["bounded_context"] is True
    assert all(policy["agent_variant"][flag] is False for flag in ACCURACY_FLAGS)
    assert policy["agent_variant"]["validator"] is True
    assert policy["provider_guarantees_verified"] is False


@pytest.mark.parametrize("flag", ("bounded_context", *ACCURACY_FLAGS))
def test_every_variant_flag_is_bound_to_approval(tmp_path, simulated_identity, flag):
    _, approval, _ = approved(tmp_path)
    expected = copy.deepcopy(approval["binding"])
    approval["binding"]["experimental_policy"]["agent_variant"][flag] = not approval[
        "binding"
    ]["experimental_policy"]["agent_variant"][flag]
    with pytest.raises(PilotStop, match="task_not_approved"):
        experimental.validate_approval(
            approval, expected, pilot.TASKS[0], pilot.TASKS[0]
        )


@pytest.mark.parametrize(
    "name", ("agent.py", "ledger.py", "context_budget.py", "proof.py")
)
def test_reviewed_runtime_bytes_remain_mandatory(monkeypatch, name):
    original = Path.read_bytes
    target = ROOT / "src/ripple" / name

    def read(path):
        body = original(path)
        return body + b"\n# synthetic tamper\n" if path == target else body

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(PilotStop, match="inherited_runtime_or_frozen_modified"):
        pilot.verify_runtime()


def test_old_runtime_baseline_is_rejected(monkeypatch):
    assert pilot.verify_runtime()["base"] == pilot.BASE
    monkeypatch.setattr(pilot, "BASE", "112092aae61d96891af192adead7bded833ea569")
    with pytest.raises(PilotStop, match="inherited_runtime_or_frozen_modified"):
        pilot.verify_runtime()


def test_old_baseline_approval_is_rejected(tmp_path, simulated_identity):
    _, approval, _ = approved(tmp_path)
    expected = copy.deepcopy(approval["binding"])
    approval["binding"]["base"] = "112092aae61d96891af192adead7bded833ea569"
    with pytest.raises(PilotStop, match="task_not_approved"):
        experimental.validate_approval(
            approval, expected, pilot.TASKS[0], pilot.TASKS[0]
        )


def test_bounded_capture_preserves_off_on_replay_and_flags(
    tmp_path, monkeypatch, simulated_identity
):
    providers = synthetic_sdk(monkeypatch)
    output, _, path = approved(tmp_path)
    observed = []
    original = AgentController.__init__

    def initialize(self, *args, **kwargs):
        observed.append((kwargs["variant"], kwargs["retain_confirmed_report"]))
        original(self, *args, **kwargs)

    monkeypatch.setattr(AgentController, "__init__", initialize)
    with pilot.offline():
        result = experimental.execute_one(
            ROOT / "dev/p2b",
            output,
            pilot.TASKS[0],
            path,
            profile=experimental.PROFILE,
            acknowledged_task=pilot.TASKS[0],
        )
    assert observed == [
        (RIPPLE_V2_BOUNDED, False),
        (RIPPLE_V2_BOUNDED, False),
        (RIPPLE_V2_BOUNDED, True),
    ]
    assert result["same_recorded_responses"] and result["provenance_identical"]
    assert (
        result["off_live_replay_identical"]
        and result["additional_inference_for_replay"] == 0
    )
    assert result["comparisons"]["on"]["exact_source_set"]
    assert len(providers) == 1 and providers[0].calls == result["wire_requests"]
    assert verify_journal_formatting(read_journal(output)) == providers[0].calls
    assert inspect_journal(output)["evaluation_eligible"]


def test_context_budget_stop_is_durable_unscored_and_ineligible(
    tmp_path, monkeypatch, simulated_identity
):
    providers = synthetic_sdk(monkeypatch)
    output, _, path = approved(tmp_path)

    def refuse(self, intent):
        assert self.variant == RIPPLE_V2_BOUNDED
        raise ContextBudgetExceeded("synthetic required core exceeds budget")

    monkeypatch.setattr(AgentController, "_decision_prompt", refuse)
    with pilot.offline(), pytest.raises(PilotStop, match="context_budget"):
        experimental.execute_one(
            ROOT / "dev/p2b",
            output,
            pilot.TASKS[0],
            path,
            profile=experimental.PROFILE,
            acknowledged_task=pilot.TASKS[0],
        )
    assert len(providers) == 1 and providers[0].calls == 1
    report = inspect_journal(output)
    row = report["tasks"][0]
    assert row["status"] == "CENSORED" and row["reason"] == "context_budget"
    assert not row["evaluation_eligible"] and not row["unknown_cost"]
    assert row["accounted_tokens"] == row["reserved_tokens"]
    events = read_journal(output)
    assert verify_journal_formatting(events) == 1
    assert not any(e["kind"] == "task_finished" for e in events)
    result = json.loads((output / pilot.TASKS[0] / "result.json").read_text())
    assert (
        result["accuracy_evaluable"] is False
        and result["terminal_reason"] == "context_budget"
    )
    assert "comparisons" not in result
    assert result["same_recorded_responses"] and result["provenance_identical"]
    summaries = list(output.glob("terminal-*.json"))
    assert (
        len(summaries) == 1
        and json.loads(summaries[0].read_text())["reason"] == "context_budget"
    )
