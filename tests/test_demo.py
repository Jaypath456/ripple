"""Tests for the demo's UI-independent logic (Streamlit is not required)."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo import build_fixtures, logic
from ripple.diff_models import FindingCategory
from ripple.llm import LLMResponse, OpenAILLM
from ripple.tools import TOOL_NAMES


@pytest.fixture(scope="module")
def scenarios() -> dict[str, dict]:
    return {name: logic.load_scenario(name) for name in logic.list_scenarios()}


def test_all_three_scenarios_load(scenarios) -> None:
    assert list(scenarios) == list(logic.SCENARIO_ORDER)
    for scenario in scenarios.values():
        assert scenario["generation"]["model"] == build_fixtures.SCRIPTED_MODEL
        assert scenario["report"]["status"] == "completed"


def test_malformed_and_missing_fixtures_fail_clearly(tmp_path: Path) -> None:
    with pytest.raises(logic.DemoError, match="not found"):
        logic.load_scenario("absent", tmp_path)
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(logic.DemoError, match="not valid JSON"):
        logic.load_scenario("bad", tmp_path)
    (tmp_path / "partial.json").write_text(json.dumps({"id": "x"}))
    with pytest.raises(logic.DemoError, match="missing"):
        logic.load_scenario("partial", tmp_path)


def test_benchmark_values_come_from_the_frozen_summary() -> None:
    benchmark = logic.load_benchmark()
    summary = json.loads(logic.SUMMARY_PATH.read_text())
    expected = {row["system"]: row for row in summary["aggregates"]}
    rows = logic.benchmark_rows(benchmark, logic.HEADLINE_SYSTEMS)
    assert [row["System"] for row in rows] == list(logic.HEADLINE_SYSTEMS)
    for row in rows:
        assert row["F1"] == logic.fmt_metric(expected[row["System"]]["f1"])
    status = summary["status_mix_by_system"]["RIPPLE"]
    assert benchmark["ripple_abstained"] == status["abstained"]
    assert benchmark["ripple_runs"] == sum(status.values())


def test_benchmark_loading_errors(tmp_path: Path) -> None:
    with pytest.raises(logic.DemoError, match="not found"):
        logic.load_benchmark(tmp_path / "missing.json")
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"config_version": "x"}))
    with pytest.raises(logic.DemoError, match="missing"):
        logic.load_benchmark(path)


def test_metric_formatting() -> None:
    assert logic.fmt_metric(0.29876) == "0.299"
    assert logic.fmt_metric(None) == "n/a"
    assert logic.fmt_count(29036571) == "29,036,571"
    assert logic.fmt_count(None) == "n/a"


def test_report_view_shows_components_and_dropped_claims(scenarios) -> None:
    view = logic.report_view(scenarios["soft_delete"]["report"])
    targets = {item["target"] for item in view["components"]}
    assert "users/models.py::User" in targets
    assert "users/migrations/<proposed migration>" in targets
    assert (
        "unconfirmed component dropped: users/auth.py::login" in view["dropped_claims"]
    )
    assert view["stats"]["stop_reason"] == "submitted"


def test_trace_steps_pair_decisions_results_and_ledger(scenarios) -> None:
    scenario = scenarios["soft_delete"]
    steps = logic.trace_steps(scenario)
    phases = [step["phase"] for step in steps]
    assert phases[0] == "seed" and phases[-1] == "expand"
    assert phases.index("expand") > phases.index("explore")
    assert steps[1]["reason"] == "Inspect the user model."
    assert steps[1]["evidence_id"] == "e2"
    assert any(
        "REJECTED" not in note and "rejected" in note
        for s in steps
        for note in s["ledger"]
    )
    assert any(step["tool"] == "submit_report" for step in steps)
    assert any(step["duplicate"] for step in steps)
    assert all(step["summary"] for step in steps)


def test_ledger_rows_show_all_three_states(scenarios) -> None:
    statuses = {row["status"] for row in logic.ledger_rows(scenarios["soft_delete"])}
    assert statuses == {"confirmed", "suspected", "rejected"}


def test_stage_b_categories_render_and_scenarios_teach_them(scenarios) -> None:
    assert set(logic.CATEGORY_INFO) == set(get_args(FindingCategory))
    found = {
        name: {row["category"] for row in logic.stage_b_rows(s["verification"])}
        for name, s in scenarios.items()
    }
    assert "stale_caller" in found["required_argument"]
    assert {"missing_test", "unexpected"} <= found["deletion_behavior"]
    assert "expected" in found["soft_delete"]
    with pytest.raises(logic.DemoError, match="Unknown"):
        logic.stage_b_rows({"findings": [{"category": "made_up", "path": "x"}]})


def test_redaction_and_config_status_never_expose_keys(monkeypatch) -> None:
    secret = "abc123-very-secret-key"
    monkeypatch.setenv("RIPPLE_LLM_API_KEY", secret)
    monkeypatch.setenv("RIPPLE_LLM_MODEL", "some/model")
    assert secret not in logic.redact(f"failed with key {secret}")
    assert "sk-[REDACTED]" in logic.redact("token sk-ABCDEFGHIJKLMNOP", [])
    assert "Bearer [REDACTED]" in logic.redact("Authorization: Bearer abcdefghijk", [])
    status = logic.llm_config_status()
    assert status["configured"] and secret not in json.dumps(status)
    assert secret not in logic.friendly_error(RuntimeError(f"401 bad key {secret}"))


def test_replay_mode_makes_zero_api_calls(monkeypatch, scenarios) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("replay must not construct or call a model client")

    monkeypatch.setattr(OpenAILLM, "__init__", forbidden)
    monkeypatch.setattr(OpenAILLM, "_call", forbidden)
    monkeypatch.delenv("RIPPLE_LLM_API_KEY", raising=False)
    for name in logic.list_scenarios():
        scenario = logic.load_scenario(name)
        logic.trace_steps(scenario)
        logic.ledger_rows(scenario)
        logic.report_view(scenario["report"])
        logic.stage_b_rows(scenario["verification"])
        logic.captured_prompts(scenario)
        logic.build_explainer_context(scenario, logic.load_benchmark())


def test_live_path_validation(tmp_path: Path) -> None:
    with pytest.raises(logic.DemoError, match="Enter"):
        logic.validate_live_repo("  ")
    with pytest.raises(logic.DemoError, match="does not exist"):
        logic.validate_live_repo(str(tmp_path / "missing"))
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    with pytest.raises(logic.DemoError, match="not a directory"):
        logic.validate_live_repo(str(file_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(logic.DemoError, match="Not a Git repository"):
        logic.validate_live_repo(str(plain))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "app.js").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    with pytest.raises(logic.DemoError, match="Python"):
        logic.validate_live_repo(str(repo))
    (repo / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    assert logic.validate_live_repo(str(repo / ".")) == repo.resolve()


def test_explainer_context_is_grounded_and_answers_are_checked(scenarios) -> None:
    scenario = scenarios["required_argument"]
    context = logic.build_explainer_context(scenario, logic.load_benchmark())
    data = json.loads(context)
    assert {"report", "ledger", "evidence", "trace_steps", "stage_b"} <= set(data)
    assert data["benchmark_metadata"]["model"] == "openai/gpt-oss-20b"
    assert "stale_caller" in context and "admin/users.py" in context
    grounded = {
        "supported": True,
        "answer": "admin/users.py still calls delete_user with two arguments.",
        "citations": ["admin/users.py", "stale_caller"],
    }
    assert logic.check_answer(grounded, context).supported
    invented = grounded | {"citations": ["billing/refunds.py"]}
    assert logic.check_answer(invented, context).answer == logic.REFUSAL
    uncited = grounded | {"citations": []}
    assert logic.check_answer(uncited, context).answer == logic.REFUSAL
    assert logic.check_answer("not an object", context).answer == logic.REFUSAL
    prompt = logic.explainer_prompt("Why?", context)
    assert logic.REFUSAL in prompt and "<ripple_context>" in prompt

    class FakeLLM:
        def _call(self, prompt, model):
            assert model is logic.ExplainerAnswer
            return LLMResponse(payload=grounded, model="fake")

    assert logic.ask_explainer("Why stale?", context, FakeLLM()).supported
    with pytest.raises(logic.DemoError):
        logic.ask_explainer(" ", context, FakeLLM())


def test_demo_never_mutates_benchmark_artifacts(scenarios) -> None:
    paths = (logic.SUMMARY_PATH, logic.CONFIG_PATH)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    benchmark = logic.load_benchmark()
    logic.benchmark_rows(benchmark, logic.HEADLINE_SYSTEMS + logic.ABLATIONS)
    logic.build_explainer_context(scenarios["soft_delete"], benchmark)
    logic.benchmark_model()
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert before == after


def test_capabilities_tools_and_prompts_track_the_core(scenarios) -> None:
    assert [item["name"] for item in logic.tool_guide()] == list(TOOL_NAMES)
    assert "untrusted" in logic.provider_instructions()
    prompts = logic.captured_prompts(scenarios["soft_delete"])
    assert {"interpret", "choose_next_action", "draft_report"} <= set(prompts)
    assert "untrusted data" in prompts["choose_next_action"]
    assert any(
        "stale_caller" in item for item in logic.supported_capabilities()["supported"]
    )


def test_replay_fixtures_regenerate_from_the_real_core(
    tmp_path: Path, scenarios
) -> None:
    repo = tmp_path / "sample_app"
    commits = build_fixtures.build_sample_repo(repo)
    for name, saved in scenarios.items():
        assert saved["base_commit"] == commits["base"]
        fresh = build_fixtures._run_scenario(repo, commits, name, tmp_path / "work")
        key = lambda analysis: sorted(
            (item["path"], item["category"]) for item in analysis["findings"]
        )
        assert key(fresh["verification"]) == key(saved["verification"])
        assert [c["target"] for c in fresh["report"]["affected_components"]] == [
            c["target"] for c in saved["report"]["affected_components"]
        ]
        assert fresh["report"]["dropped_claims"] == saved["report"]["dropped_claims"]
