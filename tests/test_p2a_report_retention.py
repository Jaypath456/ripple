"""Offline report retention, with no new discoveries or weakened validation."""

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_agent import _complete_script
from test_v2_protocol import CONFIRMED, REQUEST, DraftScript, _component
from test_v2_protocol import cli_repo as _cli_repo
from test_validation import _record

from ripple.agent import RIPPLE_V2, AgentController, analyze_repository
from ripple.agent_models import AffectedComponent, FeatureRequest, ReportDraft
from ripple.ledger import CandidateLedger
from ripple.llm import ScriptedLLM
from ripple.report_fallback import retain_confirmed_components
from ripple.tools import ToolResult
from ripple.validate import validate_report_draft


@pytest.fixture
def cli_repo(tmp_path):
    return _cli_repo.__wrapped__(tmp_path)


def confirmed_ledger(index, targets=("src/auth.py",)):
    ledger = CandidateLedger(index)
    presented = {}
    for number, target in enumerate(targets):
        evidence_id = f"e{number * 3 + 1}"
        ledger.add_evidence(_record(evidence_id, "inspect_symbol", target))
        assert ledger.seed(target, "seed", evidence_id)
        presented[target] = {evidence_id}
        assert ledger.decide(
            target,
            "confirm",
            (evidence_id,),
            "accepted checkpoint",
            frozenset({evidence_id}),
        )[0]
        ledger.add_evidence(_record(f"e{number * 3 + 2}", "find_references", target))
        ledger.add_evidence(_record(f"e{number * 3 + 3}", "find_tests", target))
    return ledger, presented


def empty_report(index, ledger):
    return validate_report_draft(ReportDraft(affected_components=()), index, ledger)


def test_omitted_confirmed_target_is_revalidated_with_original_citations(indexed_repo):
    ledger, presented = confirmed_ledger(indexed_repo)
    before = copy.deepcopy((ledger.candidates, ledger.evidence, presented))
    original = empty_report(indexed_repo, ledger)
    result = retain_confirmed_components(original, indexed_repo, ledger, presented)
    assert result.retained == ("src/auth.py",) and not result.refused
    component = result.report.components[0]
    assert component.evidence == ("e1",) and component.confidence == "low"
    assert component.change_type == "modify"
    assert validate_report_draft(
        ReportDraft(affected_components=(component,)), indexed_repo, ledger
    ).components == (component,)
    assert (ledger.candidates, ledger.evidence, presented) == before
    assert not original.components
    again = retain_confirmed_components(result.report, indexed_repo, ledger, presented)
    assert not again.retained and again.report == result.report


def test_only_already_confirmed_files_are_retained_in_a_multi_file_report(indexed_repo):
    ledger, presented = confirmed_ledger(
        indexed_repo, ("src/auth.py", "src/service.py")
    )
    original_component = AffectedComponent(
        target="src/auth.py",
        change_type="delete",
        change_kind="api",
        reason="independently validated draft",
        confidence="high",
        evidence=("e1",),
    )
    original = validate_report_draft(
        ReportDraft(affected_components=(original_component,)), indexed_repo, ledger
    )
    result = retain_confirmed_components(original, indexed_repo, ledger, presented)
    assert result.report.components[0] == original_component
    assert result.retained == ("src/service.py",)
    assert {c.target for c in result.report.components} == {
        "src/auth.py",
        "src/service.py",
    }
    assert result.report.components[1].evidence == ("e4",)


@pytest.mark.parametrize("status", ["suspected", "rejected"])
def test_no_new_confirmations_or_predictions_are_created(indexed_repo, status):
    ledger, presented = confirmed_ledger(indexed_repo)
    ledger.candidates["src/auth.py"].status = status
    result = retain_confirmed_components(
        empty_report(indexed_repo, ledger), indexed_repo, ledger, presented
    )
    assert not result.report.components and not result.retained


@pytest.mark.parametrize(
    "case,code",
    [
        ("unindexed", "target_not_indexed"),
        ("unindexed_symbol", "target_not_indexed"),
        ("history", "missing_checkpoint_confirmation"),
        ("empty_ids", "missing_confirmation_evidence"),
        ("bad_ids", "invalid_confirmation_evidence"),
        ("not_presented", "evidence_not_presented"),
        ("unknown", "unknown_evidence"),
        ("failed", "failed_evidence"),
        ("mismatched", "evidence_id_mismatch"),
        ("untouched", "untouched_target"),
        ("lexical", "strong_evidence_required"),
        ("forged_lexical_strength", "strong_evidence_required"),
        ("unknown_tool", "strong_evidence_required"),
        ("malformed_references", "strong_evidence_required"),
        ("missing_ledger_citation", "confirmation_not_in_ledger_citations"),
        ("refs", "checks_incomplete"),
        ("tests", "checks_incomplete"),
        ("forged_checks", "checks_incomplete"),
    ],
)
def test_ineligible_confirmations_are_explicitly_refused(indexed_repo, case, code):
    ledger, presented = confirmed_ledger(indexed_repo)
    candidate = ledger.candidates["src/auth.py"]
    if case == "unindexed":
        candidate.target = "../outside.py"
    if case == "unindexed_symbol":
        candidate.target = "src/auth.py::invented"
    if case == "history":
        candidate.history[-1]["via"] = "action"
    if case == "empty_ids":
        candidate.history[-1]["evidence_ids"] = []
    if case == "bad_ids":
        candidate.history[-1]["evidence_ids"] = [{}]
    if case == "not_presented":
        presented.clear()
    if case == "unknown":
        del ledger.evidence["e1"]
    if case == "failed":
        ledger.evidence["e1"] = replace(
            ledger.evidence["e1"], result=ToolResult(ok=False, evidence_id="e1")
        )
    if case == "mismatched":
        ledger.evidence["e1"] = replace(ledger.evidence["e1"], evidence_id="wrong")
    if case == "untouched":
        ledger.evidence["e1"] = replace(
            ledger.evidence["e1"], touched_targets=frozenset({"src/service.py"})
        )
    if case == "lexical":
        ledger.evidence["e1"] = replace(
            ledger.evidence["e1"], tool_name="search_code", strong=False
        )
    if case == "forged_lexical_strength":
        ledger.evidence["e1"] = replace(
            ledger.evidence["e1"], tool_name="search_code", strong=True
        )
    if case == "unknown_tool":
        ledger.evidence["e1"] = replace(
            ledger.evidence["e1"], tool_name="invented_tool"
        )
    if case == "malformed_references":
        ledger.evidence["e1"] = replace(
            ledger.evidence["e1"],
            tool_name="find_references",
            result=ToolResult(ok=True, evidence_id="e1", data={"references": None}),
        )
    if case == "missing_ledger_citation":
        candidate.evidence_ids.clear()
    if case == "refs":
        candidate.checked_refs = False
    if case == "tests":
        candidate.checked_tests = False
    if case == "forged_checks":
        del ledger.evidence["e2"]
    before = copy.deepcopy((ledger.candidates, ledger.evidence, presented))
    result = retain_confirmed_components(
        empty_report(indexed_repo, ledger), indexed_repo, ledger, presented
    )
    assert not result.report.components and result.refused == (
        (candidate.target, code),
    )
    assert (ledger.candidates, ledger.evidence, presented) == before


def test_test_files_and_parse_errors_are_not_retained(indexed_repo):
    ledger, presented = confirmed_ledger(indexed_repo, ("tests/test_auth.py",))
    result = retain_confirmed_components(
        empty_report(indexed_repo, ledger), indexed_repo, ledger, presented
    )
    assert result.refused == (("tests/test_auth.py", "target_not_source"),)
    ledger, presented = confirmed_ledger(indexed_repo)
    broken = indexed_repo.model_copy(
        update={
            "files": tuple(
                f.model_copy(update={"parse_error": "fixture parse error"})
                if f.path == Path("src/auth.py")
                else f
                for f in indexed_repo.files
            )
        }
    )
    result = retain_confirmed_components(
        empty_report(broken, ledger), broken, ledger, presented
    )
    assert result.refused == (("src/auth.py", "source_parse_error"),)


def test_ordinary_validator_cannot_be_bypassed(indexed_repo, monkeypatch):
    ledger, presented = confirmed_ledger(indexed_repo)
    original = empty_report(indexed_repo, ledger)
    monkeypatch.setattr(
        "ripple.report_fallback.validate_report_draft", lambda *args: original
    )
    result = retain_confirmed_components(original, indexed_repo, ledger, presented)
    assert not result.report.components and result.refused == (
        ("src/auth.py", "validator_rejected"),
    )


def test_refused_confirmation_is_visible_in_report_without_trace(indexed_repo):
    ledger, presented = confirmed_ledger(indexed_repo)
    presented.clear()
    original = empty_report(indexed_repo, ledger)
    result = retain_confirmed_components(original, indexed_repo, ledger, presented)
    assert not result.report.components
    assert result.report.dropped_claims == (
        "Confirmed report retention refused [evidence_not_presented]: src/auth.py",
    )
    assert not original.dropped_claims
    repeated = retain_confirmed_components(
        result.report, indexed_repo, ledger, presented
    )
    assert repeated.report == result.report


class ObservedDraftScript(DraftScript):
    def __init__(self, drafts):
        super().__init__(copy.deepcopy(drafts))
        self.prompts = []

    def interpret(self, prompt):
        self.prompts.append(("interpret", prompt))
        return super().interpret(prompt)

    def choose_next_action(self, prompt):
        self.prompts.append(("choose_next_action", prompt))
        return super().choose_next_action(prompt)

    def decide_candidates(self, prompt):
        self.prompts.append(("decide_candidates", prompt))
        return super().decide_candidates(prompt)

    def draft_report(self, prompt):
        self.prompts.append(("draft_report", prompt))
        return super().draft_report(prompt)


@pytest.mark.parametrize(
    "case", ["omitted", "misclassified_after_repair", "fabricated_citations", "valid"]
)
def test_paired_scripted_reports_preserve_confirmations_without_extra_model_calls(
    cli_repo, tmp_path, case
):
    drafts = {
        "omitted": [[]],
        "misclassified_after_repair": [
            [_component(CONFIRMED, "new_file", ())],
            [_component(CONFIRMED, "new_file", ())],
        ],
        "fabricated_citations": [[_component(CONFIRMED, "modify", ("e999",))]],
        "valid": [[_component(CONFIRMED, "modify", ())]],
    }[case]
    outputs = []
    for enabled in [False, True]:
        provider = ObservedDraftScript(drafts)
        controller = AgentController(
            cli_repo,
            provider,
            variant=RIPPLE_V2,
            output_root=tmp_path / str(enabled),
            retain_confirmed_report=enabled,
        )
        run = controller.run(FeatureRequest(text=REQUEST))
        outputs.append((controller, provider, run))
    off, on = outputs
    assert off[1].prompts == on[1].prompts
    assert off[2].report.run_stats.llm_calls == on[2].report.run_stats.llm_calls
    assert off[2].report.run_stats.tool_calls == on[2].report.run_stats.tool_calls
    assert off[0].ledger.candidates == on[0].ledger.candidates
    confirmed = {c.target for c in on[0].ledger.confirmed()}
    assert confirmed and confirmed <= {
        c.target for c in on[2].report.affected_components
    }
    if case == "valid":
        assert off[2].report.affected_components == on[2].report.affected_components
        assert off[2].report.status == on[2].report.status == "completed"
    else:
        assert (
            off[2].report.status == "abstained"
            and not off[2].report.affected_components
        )
        assert on[2].report.status == "partial"
        assert all(
            c.confidence == "low" and "e999" not in c.evidence
            for c in on[2].report.affected_components
        )
        assert off[2].report.dropped_claims == on[2].report.dropped_claims
    events = [json.loads(line) for line in on[2].trace_path.read_text().splitlines()]
    assert any(e["event"] == "report_retention_evaluated" for e in events)
    assert events[0]["report_retention_policy"] == "confirmed-checkpoint-v1"


@pytest.mark.parametrize("flag", [None, "0", "true", "1"])
def test_entrypoint_feature_flag_is_explicit(indexed_repo, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("RIPPLE_RETAIN_CONFIRMED_REPORT", raising=False)
    else:
        monkeypatch.setenv("RIPPLE_RETAIN_CONFIRMED_REPORT", flag)
    captured = {}

    def controller(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run=lambda request: "fixture")

    monkeypatch.setattr("ripple.agent.AgentController", controller)
    assert (
        analyze_repository(
            indexed_repo,
            FeatureRequest(text="Refresh authentication tokens"),
            ScriptedLLM(),
        )
        == "fixture"
    )
    assert captured["retain_confirmed_report"] is (flag == "1")


def test_constructor_does_not_accept_truthy_strings_as_authorization(indexed_repo):
    with pytest.raises(ValueError, match="boolean"):
        AgentController(indexed_repo, ScriptedLLM(), retain_confirmed_report="0")


def test_zero_confirmation_trajectory_is_unchanged(cli_repo, tmp_path):
    outputs = []
    for enabled in [False, True]:
        provider = ObservedDraftScript([[]])
        provider.relevant = frozenset()
        controller = AgentController(
            cli_repo,
            provider,
            variant=RIPPLE_V2,
            output_root=tmp_path / str(enabled),
            retain_confirmed_report=enabled,
        )
        run = controller.run(FeatureRequest(text=REQUEST))
        outputs.append((controller, provider, run))
    assert outputs[0][1].prompts == outputs[1][1].prompts
    assert not outputs[1][0].ledger.confirmed()
    assert (
        outputs[0][2].report.affected_components
        == outputs[1][2].report.affected_components
        == ()
    )
    assert outputs[0][2].report.status == outputs[1][2].report.status == "abstained"
    assert (
        outputs[0][2].report.run_stats.llm_calls
        == outputs[1][2].report.run_stats.llm_calls
    )


def test_flag_cannot_change_historical_v1_protocol(indexed_repo, tmp_path):
    outputs = []
    for enabled in [False, True]:
        controller = AgentController(
            indexed_repo,
            _complete_script("src/auth.py::refresh_token"),
            output_root=tmp_path / str(enabled),
            retain_confirmed_report=enabled,
        )
        outputs.append(
            controller.run(FeatureRequest(text="Refresh authentication tokens"))
        )
    assert (
        outputs[0].report.affected_components == outputs[1].report.affected_components
    )
    assert outputs[0].report.run_stats == outputs[1].report.run_stats.model_copy(
        update={"runtime_seconds": outputs[0].report.run_stats.runtime_seconds}
    )
    assert "report_retention_evaluated" not in outputs[1].trace_path.read_text()


def test_flag_cannot_bypass_validator_ablation(cli_repo, tmp_path):
    provider = ObservedDraftScript([[]])
    controller = AgentController(
        cli_repo,
        provider,
        variant=replace(RIPPLE_V2, validator=False),
        output_root=tmp_path,
        retain_confirmed_report=True,
    )
    run = controller.run(FeatureRequest(text=REQUEST))
    assert controller.ledger.confirmed() and not run.report.affected_components
    assert "report_retention_evaluated" not in run.trace_path.read_text()
