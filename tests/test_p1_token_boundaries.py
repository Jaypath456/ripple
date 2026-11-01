"""A configured ceiling gates every model request without editing prompts."""

import json
from types import SimpleNamespace

import pytest

from ripple.agent import RIPPLE_V2, AgentController, TokenBudgetExceeded
from ripple.agent_models import FeatureRequest, ReportDraft
from ripple.llm import LLMResponse, ScriptedLLM


@pytest.mark.parametrize(
    "operation",
    ["interpret", "choose_next_action", "draft_report", "decide_candidates"],
)
def test_every_request_boundary_checks_spent_budget(indexed_repo, tmp_path, operation):
    calls = []

    def model(prompt):
        calls.append(prompt)
        return LLMResponse(payload={}, model="mock", total_tokens=10)

    provider = SimpleNamespace(
        model="mock",
        interpret=model,
        choose_next_action=model,
        draft_report=model,
        decide_candidates=model,
    )
    controller = AgentController(
        indexed_repo, provider, max_tokens=10, output_root=tmp_path / "out"
    )
    controller._llm("interpret", "first prompt unchanged")
    with pytest.raises(TokenBudgetExceeded):
        controller._llm(operation, "second prompt unchanged")
    assert calls == ["first prompt unchanged"] and controller.llm_calls == 1
    events = [
        json.loads(line) for line in controller.trace.path.read_text().splitlines()
    ]
    assert events[-1]["event"] == "request_boundary_blocked"


def test_zero_budget_stops_before_interpretation_and_is_not_provider_failure(
    indexed_repo, tmp_path
):
    controller = AgentController(
        indexed_repo, ScriptedLLM(), max_tokens=0, output_root=tmp_path / "out"
    )
    run = controller.run(FeatureRequest(text="Refresh authentication tokens"))
    assert (
        run.report.status == "abstained"
        and run.report.run_stats.stop_reason == "token_ceiling"
    )
    assert run.report.run_stats.llm_calls == 0
    assert "provider_error" not in controller.trace.path.read_text()


@pytest.mark.parametrize("usage", [(None, None, None), (6, 4, None)])
def test_unknown_or_split_usage_cannot_bypass_ceiling(indexed_repo, tmp_path, usage):
    provider = SimpleNamespace(
        model="mock",
        interpret=lambda prompt: LLMResponse(
            payload={},
            model="mock",
            input_tokens=usage[0],
            output_tokens=usage[1],
            total_tokens=usage[2],
        ),
    )
    controller = AgentController(
        indexed_repo, provider, max_tokens=10, output_root=tmp_path / "out"
    )
    controller._llm("interpret", "unaltered")
    with pytest.raises(TokenBudgetExceeded):
        controller._llm("interpret", "unaltered")
    # Existing serialized counters are not silently changed into estimates.
    assert controller.total_tokens == 0
    assert controller._budget_tokens == (10 if usage[0] is not None else 0)


def test_classification_repair_budget_is_checked_before_increment(
    indexed_repo, tmp_path, monkeypatch
):
    controller = AgentController(
        indexed_repo,
        ScriptedLLM(),
        variant=RIPPLE_V2,
        max_tokens=0,
        output_root=tmp_path / "out",
    )
    monkeypatch.setattr(
        controller,
        "_repairable_errors",
        lambda draft: [{"error": "classification", "target": "src/auth.py"}],
    )
    with pytest.raises(TokenBudgetExceeded):
        controller._repair_draft(
            FeatureRequest(text="Refresh authentication tokens"),
            ReportDraft(affected_components=()),
        )
    assert controller.report_repairs == 0 and controller.llm_calls == 0
