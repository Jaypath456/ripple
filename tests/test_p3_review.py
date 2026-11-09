"""Opt-in review progression and precision guard."""

from dataclasses import replace

from test_p3_accuracy import (
    PRICING,
    SHOP,
    Script,
    build,
    controller_for,
    run,
    seed_files,
)

from ripple.agent import (
    KEEP_LIMIT,
    MAX_TOOL_CALLS,
    REVIEW_STEPS_MAX,
    RIPPLE_V2,
    RIPPLE_V2_GUARD,
    RIPPLE_V2_REVIEW,
)
from ripple.agent_models import FeatureRequest
from ripple.tools import error_result

REQUEST = FeatureRequest(text="synthetic request")


def events(controller, name):
    import json

    lines = controller.trace.path.read_text().splitlines()
    return [e for e in map(json.loads, lines) if e["event"] == name]


def inspected(index, tmp_path, variant=RIPPLE_V2_REVIEW, target=PRICING):
    controller = controller_for(index, tmp_path, variant)
    seed_files(controller, [target])
    controller._execute("inspect_symbol", {"target": target})
    return controller


def test_keep_with_missing_proof_runs_bounded_targeted_steps(tmp_path):
    controller = inspected(build(tmp_path, SHOP), tmp_path)
    llm = Script()  # keeps everything
    controller.llm = llm
    controller._checkpoint(REQUEST, stall=3, force=True)
    review = llm.checkpoints[0]["candidates"][0]["review"]
    assert review["remaining_proof"] and not review["final_review"]
    assert [e["outcome"] for e in events(controller, "review_outcome")] == [
        "needs_evidence"
    ]
    before = controller.tool_calls
    assert controller._review_progress()
    steps = events(controller, "review_proof_step")
    assert 1 <= len(steps) <= 2 and controller.tool_calls - before == len(steps)
    assert all(s["target"] == PRICING for s in steps)
    assert controller.ledger.candidates[PRICING].status == "suspected"


def test_repeated_keeps_become_final_then_retire_and_are_never_reoffered(tmp_path):
    controller = inspected(build(tmp_path, SHOP), tmp_path)
    llm = Script()
    controller.llm = llm
    for _ in range(KEEP_LIMIT + 1):
        controller.presented.clear()
        controller._checkpoint(REQUEST, stall=3, force=True)
        controller._review_progress()
    finals = [c["candidates"][0]["review"]["final_review"] for c in llm.checkpoints]
    assert finals[-1] is True and finals[0] is False
    assert controller.retired[PRICING] in {
        "unresolved_after_final_review",
        "no_remaining_proof",
    }
    offered = len(llm.checkpoints)
    controller.presented.clear()
    controller._checkpoint(REQUEST, stall=3, force=True)
    assert len(llm.checkpoints) == offered  # retired: not offered again
    assert all(s.target != PRICING for s in controller._proof_plan())
    assert controller.ledger.candidates[PRICING].status == "suspected"


def test_run_stops_when_every_candidate_is_retired_without_prediction(tmp_path):
    index = build(tmp_path, SHOP)
    controller, result, trace = run(
        index, Script(submit=False), tmp_path, RIPPLE_V2_REVIEW
    )
    assert result.report.run_stats.stop_reason in {"review_exhausted", "no_progress"}
    assert not result.report.affected_components and not controller.ledger.confirmed()
    assert controller.review_steps <= REVIEW_STEPS_MAX
    retired = [
        e for e in trace if e["event"] == "review_outcome" and e["outcome"] == "retired"
    ]
    assert {e["target"] for e in retired} == set(controller.retired)


def test_valid_proof_still_confirms_and_submits(tmp_path):
    index = build(tmp_path, SHOP)
    plan = [
        ("inspect_symbol", {"target": PRICING}),
        ("find_references", {"symbol_id": f"{PRICING}::apply_discount"}),
        ("find_tests", {"target": PRICING}),
    ]
    _, result, _ = run(
        index, Script(plan=plan, confirm={PRICING}), tmp_path, RIPPLE_V2_REVIEW
    )
    assert result.report.run_stats.stop_reason == "submitted"
    assert {c.target.partition("::")[0] for c in result.report.affected_components} == {
        PRICING
    }


def test_failed_review_steps_are_not_checks_and_not_retried(tmp_path, monkeypatch):
    controller = inspected(build(tmp_path, SHOP), tmp_path)
    original = controller.session.invoke

    def failing(name, arguments):
        if name in {"find_tests", "find_references"}:
            evidence = original("search_code", {"query": "x"}).evidence_id
            return error_result(evidence, "synthetic_failure", "tool failed")
        return original(name, arguments)

    monkeypatch.setattr(controller.session, "invoke", failing)
    controller.llm = Script()
    for _ in range(4):
        controller.presented.clear()
        controller._checkpoint(REQUEST, stall=3, force=True)
        controller._review_progress()
    candidate = controller.ledger.candidates[PRICING]
    assert not candidate.checked_tests and not candidate.checked_refs
    calls = [e["step"]["arguments"] for e in events(controller, "review_proof_step")]
    assert len(calls) == len({str(sorted(c.items())) for c in calls})  # no repeats
    assert PRICING in controller.retired


def test_contradictory_or_irrelevant_candidates_are_rejected_not_retired(tmp_path):
    controller = inspected(build(tmp_path, SHOP), tmp_path)
    controller.llm = Script(reject={PRICING})
    controller._checkpoint(REQUEST, stall=3, force=True)
    assert controller.ledger.candidates[PRICING].status == "rejected"
    assert PRICING not in controller.retired and not controller.review_queue


def test_exhausted_budget_makes_review_final(tmp_path):
    controller = inspected(build(tmp_path, SHOP), tmp_path)
    controller.tool_calls = MAX_TOOL_CALLS
    controller.llm = llm = Script()
    controller._checkpoint(REQUEST, stall=3, force=True)
    assert llm.checkpoints[0]["candidates"][0]["review"]["final_review"] is True
    assert controller.retired[PRICING] == "budget_exhausted"


def test_empty_gold_request_has_no_review_activity(tmp_path):
    index = build(tmp_path, SHOP)
    controller, result, trace = run(
        index,
        Script(terms=["kubernetes", "helm"], submit=False),
        tmp_path,
        RIPPLE_V2_REVIEW,
        "Upgrade the Kubernetes Helm chart.",
    )
    assert result.report.status == "abstained" and not controller.ledger.candidates
    assert not any(e["event"].startswith("review_") for e in trace)


def test_review_evidence_carries_recorded_docstrings_and_signatures(tmp_path):
    files = dict(SHOP)
    files["shop/pricing.py"] = (
        "def apply_discount(total, code):\n"
        '    """Apply a percentage discount code to an order total."""\n'
        "    return total\n"
    )
    index = build(tmp_path, files)
    controller = inspected(index, tmp_path)
    controller._execute("inspect_symbol", {"target": f"{PRICING}::apply_discount"})
    controller.llm = llm = Script()
    controller._checkpoint(REQUEST, stall=3, force=True)
    candidate = llm.checkpoints[0]["candidates"][0]
    shows = [e["shows"] for e in candidate["evidence"]]
    assert any("apply_discount(total, code)" in text for text in shows)
    details = candidate["symbol_details"]
    assert details["citable"] is False
    assert any(
        "Apply a percentage discount code" in i["shows"] for i in details["items"]
    )
    detail_ids = {i["evidence_id"] for i in details["items"]}
    assert not detail_ids & controller.presented[PRICING]  # never offered for citation
    # The baseline summary is unchanged.
    base = inspected(index, tmp_path, RIPPLE_V2)
    base.llm = base_llm = Script()
    base._checkpoint(REQUEST, stall=3, force=True)
    assert "review" not in base_llm.checkpoints[0]["candidates"][0]
    assert not any(
        "Apply a percentage" in e["shows"]
        for e in base_llm.checkpoints[0]["candidates"][0]["evidence"]
    )


def test_precision_guard_requires_evidence_aimed_at_the_target(tmp_path):
    index = build(tmp_path, SHOP)
    controller = controller_for(index, tmp_path, RIPPLE_V2_GUARD)
    seed_files(controller, ["shop/tax.py"])
    # A references call aimed at pricing touches tax only indirectly, if at all;
    # use cart's references to sales_tax: aimed at tax.py, so it is direct.
    indirect, _ = controller._execute(
        "get_dependencies", {"path": "shop/cart.py", "direction": "imports"}
    )
    assert "shop/tax.py" in indirect.data["neighbors"][0]["path"] or any(
        n["path"] == "shop/tax.py" for n in indirect.data["neighbors"]
    )
    controller.llm = Script(
        confirm={"shop/tax.py"}, cite=lambda item: [indirect.evidence_id]
    )
    controller._checkpoint(REQUEST, stall=3, force=True)
    assert controller.ledger.candidates["shop/tax.py"].status == "suspected"
    assert events(controller, "candidate_decision")[-1]["reason_code"] == (
        "direct_evidence_required"
    )
    direct, _ = controller._execute("inspect_symbol", {"target": "shop/tax.py"})
    controller.llm.cite = lambda item: [direct.evidence_id]
    controller.presented.clear()
    controller._checkpoint(REQUEST, stall=3, force=True)
    assert controller.ledger.candidates["shop/tax.py"].status == "confirmed"
    # Without the guard the same indirect citation is accepted by unchanged rules.
    other = controller_for(
        index, tmp_path, replace(RIPPLE_V2_GUARD, precision_guard=False)
    )
    seed_files(other, ["shop/tax.py"])
    record, _ = other._execute(
        "get_dependencies", {"path": "shop/cart.py", "direction": "imports"}
    )
    other.llm = Script(confirm={"shop/tax.py"}, cite=lambda item: [record.evidence_id])
    other._checkpoint(REQUEST, stall=3, force=True)
    assert other.ledger.candidates["shop/tax.py"].status == "confirmed"


# ------------------------------------------------------- confidence tiers

from ripple.agent import RIPPLE_V2_PROOF, RIPPLE_V2_TIERS
from ripple.agent_models import ReportDraft
from ripple.validate import validate_report_draft

CART = "shop/cart.py"


def paths(report):
    return {c.target.partition("::")[0]: c for c in report.affected_components}


def test_late_confirmations_are_lowered_never_removed(tmp_path):
    index = build(tmp_path, SHOP)
    tiers = replace(RIPPLE_V2_PROOF, name="proof-tiers", confidence_tiers=True)
    _, plain, _ = run(index, Script(confirm={PRICING, CART}), tmp_path, RIPPLE_V2_PROOF)
    controller, tiered, trace = run(
        index, Script(confirm={PRICING, CART}), tmp_path, tiers
    )
    before, after = paths(plain.report), paths(tiered.report)
    assert set(before) == set(after) and {PRICING, CART} <= set(after)
    late = set(controller._late_paths())
    assert CART in late and PRICING not in late
    for path, component in after.items():
        assert component.evidence == before[path].evidence  # provenance unchanged
        assert component.target == before[path].target
        expected = "low" if path in late else before[path].confidence
        assert component.confidence == expected
    note = tiered.report.blind_spots[-1]
    assert "Lower-confidence tier" in note.description and CART in note.description
    assert set(note.evidence) <= set(controller.ledger.evidence)
    checked = validate_report_draft(
        ReportDraft(affected_components=tiered.report.affected_components),
        index,
        controller.ledger,
    )
    assert {c.target for c in checked.components} == {
        c.target for c in tiered.report.affected_components
    }
    event = next(e for e in trace if e["event"] == "confidence_tiers")
    assert set(event["late_paths"]) == late and set(event["observations"]) == late


def test_tiers_change_nothing_without_a_deferred_submission(tmp_path):
    index = build(tmp_path, SHOP)
    plan = [
        ("inspect_symbol", {"target": PRICING}),
        ("find_references", {"symbol_id": f"{PRICING}::apply_discount"}),
        ("find_tests", {"target": PRICING}),
    ]
    _, plain, _ = run(
        index, Script(plan=list(plan), confirm={PRICING}), tmp_path, RIPPLE_V2
    )
    controller, tiered, trace = run(
        index, Script(plan=list(plan), confirm={PRICING}), tmp_path, RIPPLE_V2_TIERS
    )
    assert not controller.deferred and controller._late_paths() == []
    assert tiered.report.affected_components == plain.report.affected_components
    assert tiered.report.blind_spots == plain.report.blind_spots
    assert next(e for e in trace if e["event"] == "confidence_tiers")["downgraded"] == 0


def test_tiers_off_by_default_emit_nothing(tmp_path):
    index = build(tmp_path, SHOP)
    _, _, trace = run(index, Script(confirm={PRICING, CART}), tmp_path, RIPPLE_V2_PROOF)
    assert not any(e["event"] == "confidence_tiers" for e in trace)


def test_tier_note_lists_whole_targets_within_the_blind_spot_limit():
    from ripple.agent import _tier_note
    from ripple.agent_models import BlindSpot

    targets = [f"{'deep_segment/' * 15}module_{n}.py" for n in range(12)]
    note = _tier_note(targets)
    assert len(note) <= 1000 and note.endswith(" more)")
    listed = note.split("relying on: ", 1)[1].rsplit(" (+", 1)[0].split(", ")
    assert listed == targets[: len(listed)]  # whole names, original order
    assert BlindSpot(description=note).description == note
    assert _tier_note(targets[:1]).endswith(targets[0])


def test_every_p3_flag_disabled_is_identical_to_plain_v2(tmp_path):
    import dataclasses
    import json

    flags = {
        f.name: False
        for f in dataclasses.fields(RIPPLE_V2)
        if f.type in {bool, "bool"}
        and f.name
        in {
            "proof_selection",
            "diverse_seeding",
            "bounded_context",
            "review_progression",
            "precision_guard",
            "confidence_tiers",
        }
    }
    assert len(flags) == 6 and all(getattr(RIPPLE_V2, k) is False for k in flags)
    off = replace(RIPPLE_V2, name="RIPPLE-v2", **flags)
    index = build(tmp_path, SHOP)
    runs = []
    for variant in (RIPPLE_V2, off):
        llm = Script(
            plan=[("find_references", {"symbol_id": PRICING})],
            confirm={PRICING, "shop/cart.py"},
        )
        _, result, trace = run(
            index, llm, tmp_path / variant.name / str(len(runs)), variant
        )
        runs.append((llm, result, trace))
    (a, ra, ta), (b, rb, tb) = runs
    assert a.prompts == b.prompts and a.checkpoints == b.checkpoints
    strip = lambda r: json.loads(
        r.report.model_dump_json(exclude={"report_id", "run_stats"})
    )
    assert strip(ra) == strip(rb)
    p3_events = {
        "proof_step_selected",
        "submission_deferred",
        "structural_expansion",
        "request_context",
        "request_size",
        "request_context_refused",
        "review_outcome",
        "review_proof_step",
        "confidence_tiers",
    }
    for trace in (ta, tb):
        assert not p3_events & {e["event"] for e in trace}
        assert "accuracy_policies" not in trace[0]
        assert "proof_steps" not in trace[-1]
