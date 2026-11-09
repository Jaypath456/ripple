"""Opt-in bounded request context: selection, fail-closed behaviour, provenance."""

import json
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_p3_accuracy import PRICING, SHOP, Script, build, run

from ripple import context_budget
from ripple.agent import RIPPLE_V2, RIPPLE_V2_BOUNDED, AgentController
from ripple.agent_models import FeatureRequest
from ripple.context_budget import (
    ContextBudgetExceeded,
    bound_decision_context,
    prompt_bytes,
)

sys.path.insert(0, str(Path(__file__).parents[1] / "dev/accuracy"))
import request_size
import stress

BLOCK = re.compile(r"<repository_data>\n(.*)\n</repository_data>", re.DOTALL)


def render(data):
    return (
        "INSTRUCTIONS\n<repository_data>\n"
        + json.dumps(data)
        + "\n</repository_data>\n"
    )


def observation(evidence_id, hits=0, error=None):
    data = {"hits": [{"path": f"pkg/m{n}.py", "symbol": None} for n in range(hits)]}
    return {
        "tool": "search_code",
        "arguments": {"query": "q"},
        "result": {
            "ok": error is None,
            "data": data if error is None else None,
            "error": error,
            "evidence_id": evidence_id,
            "truncated": False,
        },
        "duplicate": False,
    }


def context(candidates=3, hits=200):
    return {
        "intent": {"summary": "s", "search_terms": ["a", "b", "c"]},
        "ledger": [
            {
                "target": f"pkg/m{n}.py",
                "status": "confirmed" if n == 0 else "suspected",
                "reason": "seed " * 20,
                "supporting_evidence": [f"e{i}" for i in range(1, 12)],
                "non_lexical_evidence": ["e2"] if n < 2 else [],
                "needs": [f"find_tests(target=pkg/m{n}.py)"],
            }
            for n in range(candidates)
        ],
        "last_three_observations": [
            observation("e8", hits=50),
            {"controller": "Submission refused: run find_tests first."},
            observation("e9", hits=hits),
        ],
        "older_observation_summaries": [
            {"tool": "search_code", "evidence_id": f"e{n}", "ok": n % 2 == 0}
            for n in range(1, 8)
        ],
        "budgets": {"tools_used": 9, "tools_max": 25},
        "submission_ready": False,
    }


def test_level_zero_is_the_unchanged_context_when_it_fits():
    data = context(hits=2)
    prompt, record = bound_decision_context(data, render, focus=set(), protected=set())
    assert prompt == render(data) and record["level"] == 0 and not record["omitted"]


def test_selection_keeps_identity_errors_guidance_and_all_targets():
    data = context(candidates=12, hits=400)
    budget = prompt_bytes(render(data)) // 3
    prompt, record = bound_decision_context(
        data, render, focus={"pkg/m399.py"}, protected={"pkg/m0.py"}, budget=budget
    )
    shown = json.loads(BLOCK.search(prompt).group(1))
    assert prompt_bytes(prompt) <= budget and record["fits"] and record["level"] > 0
    latest = shown["last_three_observations"][-1]
    assert latest["result"]["evidence_id"] == "e9" and latest["result"]["truncated"]
    hits = latest["result"]["data"]["hits"]
    # Whole items only; the focused (candidate-touching) item is kept first.
    assert {"path": "pkg/m399.py", "symbol": None} in hits
    assert all(
        item in data["last_three_observations"][2]["result"]["data"]["hits"]
        for item in hits
    )
    assert latest["result"]["data"]["hits_omitted"] == 400 - len(hits)
    # Controller refusal guidance is verbatim; earlier payload is a digest with its ID.
    assert shown["last_three_observations"][1] == data["last_three_observations"][1]
    assert shown["last_three_observations"][0]["result"]["evidence_id"] == "e8"
    assert shown["last_three_observations"][0]["result"]["payload_omitted"]
    # Every candidate identifier and status is preserved; protected keeps details.
    assert [(i["target"], i["status"]) for i in shown["ledger"]] == [
        (i["target"], i["status"]) for i in data["ledger"]
    ]
    assert "needs" in shown["ledger"][0]
    assert shown["older_observation_count"] == 7
    assert shown["context_selection"]["note"] == context_budget.OMISSION_NOTE
    assert record["omitted"] == shown["context_selection"]["omitted"]
    # Instrumentation carries sizes only.
    assert all(isinstance(v, int) for v in record["fields"].values())


def test_required_core_that_cannot_fit_fails_closed():
    with pytest.raises(ContextBudgetExceeded, match="decision_context_exceeds_budget"):
        bound_decision_context(
            context(), render, focus=set(), protected=set(), budget=200
        )


def test_controller_stops_cleanly_when_context_cannot_fit(tmp_path, monkeypatch):
    index = build(tmp_path, SHOP)
    monkeypatch.setitem(context_budget.PROMPT_BUDGETS, "choose_next_action", 300)
    monkeypatch.setattr(
        "ripple.agent.bound_decision_context",
        lambda *a, **k: bound_decision_context(*a, **{**k, "budget": 300}),
    )
    llm = Script(confirm={PRICING})
    controller, result, trace = run(index, llm, tmp_path, RIPPLE_V2_BOUNDED)
    assert result.report.run_stats.stop_reason == "context_budget"
    assert not llm.prompts  # nothing oversized was sent
    assert not controller.ledger.confirmed()
    assert result.report.status in {"abstained", "partial"}
    assert any(e["event"] == "request_context_refused" for e in trace)


def test_flags_off_prompts_are_unchanged(tmp_path):
    index = build(tmp_path, SHOP)
    base, bounded = Script(confirm={PRICING}), Script(confirm={PRICING})
    run(index, base, tmp_path, RIPPLE_V2)
    _, _, trace = run(index, bounded, tmp_path, RIPPLE_V2_BOUNDED)
    # A small repository fits at level 0: the bounded run sends identical context.
    assert base.prompts == bounded.prompts
    assert {
        e["level"] for e in trace if e["event"] == "request_context" and "level" in e
    } == {0}


def test_checkpoint_offers_only_shown_evidence(tmp_path, monkeypatch):
    index = build(tmp_path, SHOP)
    controller = AgentController(
        index, Script(), output_root=tmp_path / "c", variant=RIPPLE_V2_BOUNDED
    )
    result, _ = controller._execute(
        "search_code", {"query": "shop pricing", "limit": 15}
    )
    controller.ledger.seed(PRICING, "seed", result.evidence_id)
    for tool, arguments in [
        ("inspect_symbol", {"target": PRICING}),
        ("find_tests", {"target": PRICING}),
        ("find_references", {"symbol_id": f"{PRICING}::apply_discount"}),
        ("find_references", {"symbol_id": f"{PRICING}::unit_price"}),
        ("get_dependencies", {"path": PRICING, "direction": "imported_by"}),
        ("co_changed", {"path": PRICING}),
        ("inspect_symbol", {"target": f"{PRICING}::apply_discount"}),
    ]:
        controller._execute(tool, arguments)
    monkeypatch.setattr(context_budget, "EVIDENCE_TAIL", 3)
    support = controller.ledger.support(PRICING)
    assert len(support) > context_budget.EVIDENCE_TAIL
    omitted = support[0].evidence_id  # the lexical seed record is selected out first
    llm = Script(confirm={PRICING}, cite=lambda item: [omitted])
    controller.llm = llm
    controller._checkpoint(
        FeatureRequest(text="synthetic request"), stall=3, force=True
    )
    shown = {e["evidence_id"] for e in llm.checkpoints[0]["candidates"][0]["evidence"]}
    assert omitted not in shown and controller.presented[PRICING] == shown
    assert llm.checkpoints[0]["candidates"][0]["evidence_omitted"] == len(
        support
    ) - len(shown)
    assert controller.ledger.candidates[PRICING].status == "suspected"  # not_presented
    # When nothing fits, the checkpoint is deferred without a model call.
    monkeypatch.setitem(context_budget.PROMPT_BUDGETS, "decide_candidates", 100)
    controller.presented.clear()
    before = len(llm.checkpoints)
    assert not controller._checkpoint(
        FeatureRequest(text="synthetic request"), stall=3, force=True
    )
    assert len(llm.checkpoints) == before


def test_report_prompt_lists_only_confirmed_and_falls_back_when_too_large(
    tmp_path, monkeypatch
):
    index = build(tmp_path, SHOP)
    plan = [
        ("inspect_symbol", {"target": PRICING}),
        ("find_references", {"symbol_id": f"{PRICING}::apply_discount"}),
        ("find_tests", {"target": PRICING}),
    ]
    seen = []

    class Recording(Script):
        def draft_report(self, prompt):
            seen.append(json.loads(BLOCK.search(prompt).group(1)))
            return super().draft_report(prompt)

    _, result, _ = run(
        index,
        Recording(plan=list(plan), confirm={PRICING}),
        tmp_path,
        RIPPLE_V2_BOUNDED,
    )
    assert seen and {i["status"] for i in seen[0]["ledger"]} == {"confirmed"}
    assert seen[0]["ledger_omitted"]["unconfirmed_candidates"] >= 1
    monkeypatch.setitem(context_budget.PROMPT_BUDGETS, "draft_report", 50)
    variant = replace(RIPPLE_V2_BOUNDED, name="tiny-report")
    _, result, _ = run(
        index, Script(plan=list(plan), confirm={PRICING}), tmp_path, variant
    )
    assert result.report.run_stats.stop_reason == "context_budget"
    assert {
        path
        for c in result.report.affected_components
        for path in [c.target.partition("::")[0]]
    } == {PRICING}
    assert result.report.status == "partial"


def test_instrumentation_never_records_prompt_or_source_text(tmp_path):
    index = build(tmp_path, SHOP)
    _, _, trace = run(index, Script(confirm={PRICING}), tmp_path, RIPPLE_V2_BOUNDED)
    events = [e for e in trace if e["event"] in {"request_context", "request_size"}]
    assert events
    text = json.dumps(events)
    assert "def apply_discount" not in text and "loyalty" not in text.lower()


def test_large_stress_scenario_stays_under_cap_with_correct_evidence(tmp_path):
    repo, scenario = stress.scenarios()["large-max-window"]
    files = stress.REPOS[repo]()
    original = stress.run("s", files, scenario, RIPPLE_V2, tmp_path)
    bounded = stress.run("s", files, scenario, RIPPLE_V2_BOUNDED, tmp_path)
    assert original["over_cap_requests"] > 0
    assert bounded["over_cap_requests"] == 0
    assert bounded["max_estimate"] <= request_size.CAP - 1500
    assert bounded["correctness_problems"] == []


# ------------------------------------------------- adversarial item selection


def hit(path, pad=0, text="x"):
    return {"path": path, "symbol": None, "snippet": text * pad}


def latest_with(items):
    return {
        "tool": "search_code",
        "arguments": {"query": "q"},
        "result": {
            "ok": True,
            "data": {"hits": items, "kind": "any"},
            "error": None,
            "evidence_id": "e42",
            "truncated": False,
        },
        "duplicate": False,
    }


def select(items, budget, focus=()):
    selection = context_budget.Selection("choose_next_action", 10_000)
    out = context_budget._select_items(
        latest_with(items), budget, set(focus), selection
    )
    return out, selection


def test_oversized_item_does_not_crowd_out_smaller_relevant_items():
    items = [
        hit("pkg/huge.py", 5000),
        hit("pkg/a.py"),
        hit("pkg/b.py"),
        hit("pkg/c.py"),
    ]
    out, selection = select(items, 600, focus={"pkg/huge.py", "pkg/b.py"})
    data = out["result"]["data"]
    assert data["hits"] == [items[1], items[2], items[3]]  # original order, whole items
    assert data["hits_omitted"] == 1 and data["hits_oversized"] == 1
    assert selection.omitted == {"latest_observation_hits": 1}
    assert out["result"]["truncated"] is True


def test_focused_items_win_when_only_some_fit():
    items = [hit(f"pkg/m{n}.py", 40) for n in range(10)]
    focus = {"pkg/m8.py", "pkg/m9.py"}
    budget = context_budget.value_bytes([items[8], items[9]]) + 8
    out, _ = select(items, budget, focus)
    kept = out["result"]["data"]["hits"]
    assert items[8] in kept and items[9] in kept
    assert kept == [item for item in items if item in kept]  # original order


def test_all_items_oversized_keeps_identity_and_counts():
    items = [hit(f"pkg/m{n}.py", 2000) for n in range(3)]
    out, _ = select(items, 300)
    data, result = out["result"]["data"], out["result"]
    assert (
        data["hits"] == [] and data["hits_omitted"] == 3 and data["hits_oversized"] == 3
    )
    assert result["evidence_id"] == "e42" and result["ok"] is True
    assert result["error"] is None and data["kind"] == "any"
    assert out["tool"] == "search_code" and out["arguments"] == {"query": "q"}


def test_item_that_exactly_fits_is_kept():
    item = hit("pkg/a.py", 10)
    budget = context_budget.value_bytes(item) + 4
    out, _ = select([item, hit("pkg/b.py", 500)], budget)
    assert out["result"]["data"]["hits"] == [item]


@pytest.mark.parametrize("text", ["x", "é", "在", "\\", '"'])
def test_kept_items_never_exceed_budget_by_escaped_size(text):
    items = [hit(f"pkg/m{n}.py", (n * 37) % 300, text) for n in range(40)]
    for budget in (50, 200, 777, 1500, 3000):
        out, _ = select(items, budget, focus={"pkg/m5.py"})
        kept = out["result"]["data"]["hits"]
        assert context_budget.value_bytes(kept) <= budget
        assert all(item in items for item in kept)  # never edited
        assert len(kept) + out["result"]["data"]["hits_omitted"] == len(items)
        again, _ = select(items, budget, focus={"pkg/m5.py"})
        assert again == out  # deterministic


def test_selection_never_rescues_an_irreducible_core():
    data = context(candidates=40, hits=5)
    with pytest.raises(ContextBudgetExceeded):
        bound_decision_context(data, render, focus=set(), protected=set(), budget=2500)
