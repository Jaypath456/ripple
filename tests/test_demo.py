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


# ----------------------------------------------------------------- showcase layer

APP = Path(__file__).resolve().parent.parent / "demo" / "app.py"


def _page_titles() -> list[str]:
    import ast

    titles = []
    for node in ast.walk(ast.parse(APP.read_text())):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "Page"
            and any(keyword.arg == "title" for keyword in node.keywords)
        ):
            keyword = next(item for item in node.keywords if item.arg == "title")
            titles.append(keyword.value.value)
    return titles


def test_showcase_has_a_small_top_level_navigation() -> None:
    assert _page_titles() == [
        "Demo",
        "How it works",
        "Results",
        "Live analysis",
        "Technical details",
    ]


def test_impact_cards_come_from_the_validated_report(scenarios) -> None:
    cards = logic.impact_cards(scenarios["soft_delete"])
    report = scenarios["soft_delete"]["report"]
    sources = [card for card in cards if card["kind"] == "source"]
    assert [card["path"] for card in sources] == [
        item["target"].partition("::")[0] for item in report["affected_components"]
    ]
    badges = {card["path"]: card["badge"] for card in cards}
    assert badges["users/models.py"] == "CONFIRMED"
    assert badges["tests/test_users.py"] == "TEST IMPACT"
    test_card = next(card for card in cards if card["kind"] == "test")
    assert test_card["reason"].startswith("Static test mapping")
    assert "PROPOSED BY PYTHON" in badges["users/migrations/<proposed migration>"]
    for card in cards:
        assert card["evidence"], card["path"]
        assert all(
            item["evidence_id"] in scenarios["soft_delete"]["evidence"]
            for item in card["evidence"]
        )
    assert "users/auth.py" not in badges  # dropped claim never becomes a card


def test_stage_b_cards_keep_categories_and_disclose_the_migration_caveat(
    scenarios,
) -> None:
    for scenario in scenarios.values():
        cards = logic.stage_b_cards(scenario["verification"])
        saved = sorted(
            (item["path"], item["category"])
            for item in scenario["verification"]["findings"]
        )
        assert sorted((card["path"], card["category"]) for card in cards) == saved
        flags = [card["flagged"] for card in cards]
        assert flags == sorted(flags, reverse=True)  # flagged issues first
    soft = logic.stage_b_cards(scenarios["soft_delete"]["verification"])
    caveats = [card for card in soft if card["caveat"]]
    assert [card["path"] for card in caveats] == [
        "users/migrations/0002_add_deleted_at.py"
    ]
    assert caveats[0]["category"] == "missing_test"


def test_timeline_progress_and_narrowing_are_derived_from_the_trace(scenarios) -> None:
    scenario = scenarios["soft_delete"]
    timeline = logic.investigation_timeline(scenario)
    assert timeline[1]["purpose"] == "Inspect User"
    assert timeline[1]["tool"] == "inspect_symbol"
    assert len(timeline) == len(logic.trace_steps(scenario))
    groups = logic.narrowing_columns(scenario)
    assert {row["target"] for row in groups["Rejected"]} == {
        "billing/service.py::delete_invoice"
    }
    stages = dict(logic.replay_progress(scenario))
    assert next(iter(stages)) == "Scanning repository"
    assert "1 unsupported claims dropped" in stages["Validating evidence"]


def test_showcase_headline_reads_the_canonical_summary() -> None:
    benchmark = logic.load_benchmark()
    head = logic.showcase_headline(benchmark)
    summary = json.loads(logic.SUMMARY_PATH.read_text())
    assert (head["tasks"], head["repositories"], head["runs"]) == (
        summary["task_count"],
        summary["repository_count"],
        summary["run_count"],
    )
    rows = {row["system"]: row for row in summary["aggregates"]}
    assert head["b3_f1"] == rows["B3"]["f1"]
    assert head["ripple_f1"] == rows["RIPPLE"]["f1"]
    assert (
        head["stage_b_false_alarm"]
        == (summary["stage_b"]["metrics"]["control_false_alarm_rate"])
    )
    assert (head["baselines"], head["ablations"]) == (5, 3)


def test_suggested_questions_only_mention_items_in_the_context(scenarios) -> None:
    for scenario in scenarios.values():
        context = logic.build_explainer_context(scenario)
        for question in logic.suggested_questions(scenario):
            mentioned = [word for word in question.split() if "/" in word]
            assert all(word.rstrip("?") in context for word in mentioned), question


def test_showcase_helpers_make_no_api_calls(monkeypatch, scenarios) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("showcase replay must not call a model")

    monkeypatch.setattr(OpenAILLM, "__init__", forbidden)
    monkeypatch.setattr(OpenAILLM, "_call", forbidden)
    for scenario in scenarios.values():
        logic.impact_cards(scenario)
        logic.stage_b_cards(scenario["verification"])
        logic.investigation_timeline(scenario)
        logic.replay_progress(scenario)
        logic.suggested_questions(scenario)
    logic.showcase_headline(logic.load_benchmark())


def _app_test():
    testing = pytest.importorskip("streamlit.testing.v1")
    return testing.AppTest.from_file(str(APP), default_timeout=60)


def test_guided_demo_discloses_progressively(monkeypatch) -> None:
    monkeypatch.delenv("RIPPLE_LLM_API_KEY", raising=False)
    monkeypatch.setattr(OpenAILLM, "__init__", lambda *a, **k: pytest.fail("API"))
    at = _app_test().run()
    assert not at.exception
    text = lambda: " ".join(item.value for item in at.markdown)
    assert "Know what a code change can break" in text()
    assert "Step 1" not in text()
    at.button(key="cta-try").click().run()
    assert "Pick a change to make" in text() and "Predicted impact" not in text()
    at.button(key="pick-required_argument").click().run()
    assert "required actor argument" in text()
    at.button(key="analyze").click().run()
    assert not at.exception
    assert "Predicted impact" in text() and "CONFIRMED" in text()
    assert "After the code was changed" not in text()
    at.button(key="after").click().run()
    assert not at.exception
    assert "STALE CALLER" in text() and "admin/users.py" in text()


@pytest.mark.parametrize(
    "page", ["page_how", "page_results", "page_live", "page_about"]
)
def test_every_showcase_page_renders(page: str, monkeypatch) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.delenv("RIPPLE_LLM_API_KEY", raising=False)
    source = APP.read_text().replace("from __future__ import annotations", "")
    source = source.replace(
        'st.navigation(list(NAVIGATION), position="top").run()', f"{page}()"
    )
    assert f"{page}()" in source
    at = testing.AppTest.from_string(
        f"import sys\nsys.path.insert(0, {str(APP.parent.parent)!r})\n" + source,
        default_timeout=60,
    ).run()
    assert not at.exception, [item.value for item in at.exception]
    assert len(at.markdown) >= 2  # CSS plus page content really rendered
    if page == "page_results":
        rendered = " ".join(item.value for item in at.markdown)
        assert "did not beat the simpler baseline" in " ".join(
            item.value for item in at.error
        )
        assert "Oracle Stage-A result; not end-to-end RIPPLE accuracy" in " ".join(
            item.value for item in at.caption
        )
        assert "F1 0.299" in rendered and "F1 0.016" in rendered


# ----------------------------------------------------------------- V2 diagnostics

DIAGNOSTIC_QUESTIONS = {
    "Why did it fail?": "failure",
    "Why did it abstain?": "failure",
    "Why did it stop?": "stop_reason",
    "Did it crash?": "crash",
    "Was there a provider failure?": "provider",
    "What was the stop reason?": "stop_reason",
    "How many candidates were confirmed?": "candidates",
    "How many were suspected candidates?": "candidates",
    "How many tool calls?": "tool_calls",
    "How many model calls?": "model_calls",
    "How many tokens?": "tokens",
    "How long did it take?": "runtime",
}
SEMANTIC_QUESTIONS = (
    "Why was users/service.py selected?",
    "What evidence supports this prediction?",
    "Why was billing/service.py::delete_invoice rejected?",
    "Why is admin/users.py a stale caller?",
)


def _captured_run(tmp_path: Path, llm) -> dict:
    from ripple.agent import AgentController
    from ripple.agent_models import FeatureRequest
    from ripple.scanner import scan_repository

    repo = tmp_path / "repo"
    subprocess.run(
        [
            "cp",
            "-r",
            str(Path(__file__).parent / "fixtures" / "cli_export_app"),
            str(repo),
        ],
        check=True,
    )
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    controller = AgentController(
        scan_repository(repo), llm, output_root=tmp_path / "out"
    )
    run = controller.run(FeatureRequest(text="Export the latest verification report"))
    return logic.capture_run(controller, run)


def _abstaining_llm():
    from ripple.llm import ScriptedLLM

    return ScriptedLLM(
        interpretations=[
            {
                "summary": "export",
                "change_kinds": ["api"],
                "search_terms": ["verification", "markdown", "latest"],
            }
        ],
        decisions=[
            {
                "tool_name": "inspect_symbol",
                "arguments": {"target": "src/app/cli.py"},
                "reason": "inspect",
            }
        ]
        * 6,
    )


def test_diagnostic_questions_are_routed_and_semantic_ones_are_not() -> None:
    for question, intent in DIAGNOSTIC_QUESTIONS.items():
        assert logic.diagnostic_intent(question) == intent, question
    for question in SEMANTIC_QUESTIONS:
        assert logic.diagnostic_intent(question) is None, question


def test_why_did_it_fail_is_answered_from_run_metadata(tmp_path: Path) -> None:
    payload = _captured_run(tmp_path, _abstaining_llm())
    stats = payload["report"]["run_stats"]
    assert payload["report"]["status"] == "abstained"
    for question in ("Why did it fail?", "Why did it abstain?"):
        answer = logic.answer_diagnostic(question, payload)
        assert answer.startswith("The run did not crash.")
        assert f"stopped with `{stats['stop_reason']}`" in answer
        assert "confirmed none" in answer
        assert f"after {stats['tool_calls']} tool calls" in answer
        assert "The provider itself did not fail." in answer
        assert "The model never proposed a candidate-state change." in answer
    summary = logic.outcome_summary(payload)
    assert summary["why"] == "No candidate gathered enough validated evidence."
    assert summary["investigated"] == len(payload["ledger"])


def test_count_answers_use_the_recorded_fields(tmp_path: Path) -> None:
    payload = _captured_run(tmp_path, _abstaining_llm())
    stats = payload["report"]["run_stats"]
    answer = logic.answer_diagnostic
    assert answer("How many tool calls?", payload).startswith(
        f"{stats['tool_calls']} tool calls ran. {stats['duplicate_calls']} repeated"
    )
    assert answer("How many model calls?", payload).startswith(
        f"{stats['llm_calls']} model calls"
    )
    assert "did not report token usage" in answer("How many tokens?", payload)
    assert answer("How long did it take?", payload) == (
        f"The run took {stats['runtime_seconds']:.1f} seconds."
    )
    assert "`no_progress`" in answer("What was the stop reason?", payload)
    assert answer("Was there a provider failure?", payload) == (
        "No provider failure was recorded in this run's trace."
    )
    stats_payload = {"report": {"run_stats": {"total_tokens": 94484}}, "trace": []}
    assert answer("How many tokens?", stats_payload) == (
        "The provider reported 94,484 tokens."
    )


def test_provider_failure_is_diagnosed_as_provider_not_crash(tmp_path: Path) -> None:
    from ripple.llm import LLMError, ScriptedLLM

    llm = ScriptedLLM(
        interpretations=[
            {
                "summary": "export",
                "change_kinds": ["api"],
                "search_terms": ["verification", "markdown", "latest"],
            }
        ],
        decisions=[
            LLMError(
                "LLM request failed: InternalServerError: <html><h1>504 Gateway "
                "Time-out</h1></html>"
            )
        ],
    )
    payload = _captured_run(tmp_path, llm)
    assert payload["report"]["status"] == "failed"
    diagnosis = logic.answer_diagnostic("Why did it fail?", payload)
    assert diagnosis.startswith(
        "RIPPLE itself did not crash; the model provider failed"
    )
    assert "504 Gateway Time-out" in diagnosis and "<html>" not in diagnosis
    assert "The provider itself did not fail." not in diagnosis
    assert logic.answer_diagnostic("Did the provider fail?", payload).startswith("Yes.")
    assert logic.outcome_summary(payload)["why"] == (
        "The model provider failed during the run."
    )


def test_diagnostics_never_call_a_model(monkeypatch, tmp_path: Path) -> None:
    payload = _captured_run(tmp_path, _abstaining_llm())

    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostics must not call a model")

    monkeypatch.setattr(OpenAILLM, "__init__", forbidden)
    monkeypatch.setattr(OpenAILLM, "_call", forbidden)
    for question in DIAGNOSTIC_QUESTIONS:
        assert logic.answer_diagnostic(question, payload)
    assert logic.diagnose_run(payload)


def test_semantic_questions_still_go_through_the_strict_explainer(
    scenarios,
) -> None:
    scenario = scenarios["soft_delete"]
    context = logic.build_explainer_context(scenario)
    for question in SEMANTIC_QUESTIONS:
        assert logic.answer_diagnostic(question, scenario) is None

    class Inventing:
        def _call(self, prompt, model):
            return LLMResponse(
                payload={
                    "supported": True,
                    "answer": "Because of billing/refunds.py.",
                    "citations": ["billing/refunds.py"],
                },
                model="fake",
            )

    answer = logic.ask_explainer(SEMANTIC_QUESTIONS[0], context, Inventing())
    assert answer.answer == logic.REFUSAL


def test_live_page_shows_a_compact_diagnosis_for_abstention(
    monkeypatch, tmp_path: Path
) -> None:
    testing = pytest.importorskip("streamlit.testing.v1")
    repo = tmp_path / "repo"
    subprocess.run(
        [
            "cp",
            "-r",
            str(Path(__file__).parent / "fixtures" / "cli_export_app"),
            str(repo),
        ],
        check=True,
    )
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    source = APP.read_text().replace("from __future__ import annotations", "")
    source = source.replace(
        'st.navigation(list(NAVIGATION), position="top").run()', "page_live()"
    )
    patch = (
        "from ripple.llm import OpenAILLM, ScriptedLLM\n"
        "OpenAILLM.from_env = classmethod(lambda cls: ScriptedLLM(\n"
        "    interpretations=[{'summary': 'export', 'change_kinds': ['api'],\n"
        "                      'search_terms': ['verification', 'markdown', 'latest']}],\n"
        "    decisions=[{'tool_name': 'inspect_symbol',\n"
        "                'arguments': {'target': 'src/app/cli.py'},\n"
        "                'reason': 'inspect'}] * 6,\n"
        "    candidate_decisions=[{'decisions': []}] * 6))\n"
    )
    at = testing.AppTest.from_string(
        f"import sys\nsys.path.insert(0, {str(APP.parent.parent)!r})\n"
        + patch
        + source,
        default_timeout=120,
    ).run()
    monkeypatch.setattr(logic, "LIVE_OUTPUT", tmp_path / "live")
    at.text_input[0].set_value(str(repo))
    at.text_input[1].set_value("Export the latest verification report as Markdown")
    at.button(key="live-analyze").click().run()
    assert not at.exception, [item.value for item in at.exception]
    rendered = " ".join(item.value for item in at.markdown)
    assert "RIPPLE ABSTAINED" in rendered
    assert "No candidate gathered enough validated evidence." in rendered
    assert at.metric[0].value == "no_progress"
    at.button(key="explain-run").click().run()
    rendered = " ".join(item.value for item in at.markdown)
    assert "RIPPLE abstained because it stopped with `no_progress`" in rendered


def test_v2_development_results_are_loaded_separately(tmp_path: Path) -> None:
    assert logic.load_v2_dev(tmp_path / "missing.json") is None
    row = {
        "case": "c",
        "status": "abstained",
        "confirmed": 0,
        "unsupported_accepted_claims": [],
        "tool_calls": 20,
        "model_calls": 30,
        "duplicate_calls": 9,
        "invalid_tool_targets": 3,
    }
    path = tmp_path / "results.json"
    path.write_text(
        json.dumps(
            [
                row | {"protocol": "v1", "protocol_revision": "v1"},
                row | {"protocol": "v2", "protocol_revision": "v2-r1"},
                row
                | {
                    "protocol": "v2",
                    "protocol_revision": "v2-r2",
                    "status": "completed",
                    "confirmed": 2,
                },
                row
                | {
                    "protocol": "v2",
                    "protocol_revision": "v2-r2",
                    "status": "failed",
                    "provider_error": "APITimeoutError",
                },
            ]
        )
    )
    dev = logic.load_v2_dev(path)
    assert list(dev["protocols"]) == ["v1", "v2-r1", "v2-r2"]  # never merged
    assert dev["protocols"]["v1"]["abstained"] == 1
    assert dev["protocols"]["v2-r2"]["with_confirmed"] == 1
    assert dev["protocols"]["v2-r2"]["provider_failures"] == 1
    assert dev["protocols"]["v2-r2"]["completed"] == 1
    assert dev["protocols"]["v2-r2"]["unsupported_accepted"] == 0
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(logic.DemoError):
        logic.load_v2_dev(tmp_path / "bad.json")
