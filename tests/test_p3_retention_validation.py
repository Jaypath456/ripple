"""Independent end-to-end validation of the existing P2A report retention.

Each scenario runs the same deterministic script twice through the unchanged V2
controller, once with retention OFF and once ON, and asserts the exact OFF/ON
difference. P2A itself is not modified; retaining a confirmation proves report
consistency with the ledger, not correctness against any gold label.
"""

import json

import pytest
from test_p3_accuracy import CART, PRICING, SHOP, Script, build

from ripple.agent import RIPPLE_V2, AgentController
from ripple.agent_models import FeatureRequest, ReportDraft
from ripple.validate import validate_report_draft

REQUEST = "Add a loyalty discount to the cart total."
FULL_PROOF = [
    ("inspect_symbol", {"target": PRICING}),
    ("find_references", {"symbol_id": f"{PRICING}::apply_discount"}),
    ("find_tests", {"target": PRICING}),
    ("inspect_symbol", {"target": CART}),
    ("find_references", {"symbol_id": f"{CART}::cart_total"}),
    ("find_tests", {"target": CART}),
]


class Drafting(Script):
    """Script whose report keeps only components accepted by ``keep``."""

    def __init__(self, keep, extra=(), **kwargs):
        super().__init__(**kwargs)
        self.keep, self.extra = keep, list(extra)

    def draft_report(self, prompt):
        response = super().draft_report(prompt)
        components = [
            c for c in response.payload["affected_components"] if self.keep(c["target"])
        ]
        components += self.extra
        response.payload["affected_components"] = components
        return response


def pair(tmp_path, make_llm):
    index = build(tmp_path, SHOP)
    runs = []
    for enabled in (False, True):
        controller = AgentController(
            index,
            make_llm(),
            output_root=tmp_path / f"retain-{enabled}",
            variant=RIPPLE_V2,
            retain_confirmed_report=enabled,
        )
        runs.append((controller, controller.run(FeatureRequest(text=REQUEST))))
    (off_controller, off), (on_controller, on) = runs
    # Identical scripted trajectories: identical ledgers and presented evidence.
    assert {
        t: (c.status, c.evidence_ids)
        for t, c in off_controller.ledger.candidates.items()
    } == {
        t: (c.status, c.evidence_ids)
        for t, c in on_controller.ledger.candidates.items()
    }
    assert off_controller.presented == on_controller.presented
    off_targets = {c.target: c for c in off.report.affected_components}
    on_targets = {c.target: c for c in on.report.affected_components}
    # ON never changes an existing component or reorders earlier refusals.
    assert all(on_targets[t] == c for t, c in off_targets.items())
    assert on.report.dropped_claims[: len(off.report.dropped_claims)] == (
        off.report.dropped_claims
    )
    return on_controller, off, on, set(on_targets) - set(off_targets)


def path_of(target):
    return target.partition("::")[0]


def test_grounded_confirmation_lost_by_the_report_is_retained(tmp_path):
    controller, off, on, added = pair(
        tmp_path,
        lambda: Drafting(
            keep=lambda t: path_of(t) == PRICING,
            plan=list(FULL_PROOF),
            confirm={PRICING, CART},
        ),
    )
    confirmed = {c.target for c in controller.ledger.confirmed()}
    lost = confirmed - {c.target for c in off.report.affected_components}
    assert lost and {path_of(t) for t in lost} == {CART}
    assert added == lost  # exactly the lost grounded confirmations
    for target in added:
        component = next(c for c in on.report.affected_components if c.target == target)
        assert component.confidence == "low" and component.change_type == "modify"
        for evidence_id in component.evidence:
            record = controller.ledger.evidence[evidence_id]
            assert record.result.ok and target in record.touched_targets
            assert evidence_id in controller.presented[target]
    # The ON report still passes the ordinary validator unchanged.
    checked = validate_report_draft(
        ReportDraft(affected_components=on.report.affected_components),
        controller.index,
        controller.ledger,
    )
    assert checked.components == on.report.affected_components
    assert on.report.status == "partial" and off.report.status == "completed"


@pytest.mark.parametrize(
    ("name", "make_llm"),
    [
        (
            "report_already_complete",
            lambda: Drafting(
                keep=lambda t: True, plan=list(FULL_PROOF), confirm={PRICING, CART}
            ),
        ),
        (
            "unconfirmed_component_claimed",
            lambda: Drafting(
                keep=lambda t: True,
                plan=list(FULL_PROOF),
                confirm={PRICING},
                extra=[
                    {
                        "target": "shop/tax.py",
                        "change_type": "modify",
                        "change_kind": "business_logic",
                        "reason": "unsupported claim",
                        "confidence": "high",
                        "evidence": ["e1"],
                    }
                ],
            ),
        ),
        (
            "explicitly_rejected",
            lambda: Drafting(
                keep=lambda t: path_of(t) == PRICING,
                plan=list(FULL_PROOF),
                confirm={PRICING},
                reject={CART},
            ),
        ),
        (
            "unpresented_or_forged_evidence",
            lambda: Drafting(
                keep=lambda t: path_of(t) == PRICING,
                plan=list(FULL_PROOF),
                confirm={PRICING, CART},
                cite=lambda item: (
                    ["e999"]
                    if path_of(item["target"]) == CART
                    else [e["evidence_id"] for e in item["evidence"] if e["strong"]][:1]
                ),
            ),
        ),
        (
            "different_target_evidence",
            lambda: Drafting(
                keep=lambda t: path_of(t) == PRICING,
                plan=list(FULL_PROOF),
                confirm={PRICING, CART},
                cite=lambda item: (
                    ["e2"]
                    if path_of(item["target"]) == CART
                    else [e["evidence_id"] for e in item["evidence"] if e["strong"]][:1]
                ),
            ),
        ),
        (
            "lexical_only_support",
            lambda: Drafting(
                keep=lambda t: path_of(t) == PRICING,
                plan=list(FULL_PROOF),
                confirm={PRICING, CART},
                cite=lambda item: (
                    ["e1"]
                    if path_of(item["target"]) == CART
                    else [e["evidence_id"] for e in item["evidence"] if e["strong"]][:1]
                ),
            ),
        ),
    ],
)
def test_retention_adds_nothing_without_a_grounded_lost_confirmation(
    tmp_path, name, make_llm
):
    controller, off, on, added = pair(tmp_path, make_llm)
    assert added == set(), name
    assert on.report.affected_components == off.report.affected_components
    assert (
        CART not in {path_of(c.target) for c in controller.ledger.confirmed()}
        or name == "report_already_complete"
    )
    expected = {
        "unpresented_or_forged_evidence": "not_presented",
        # Another target's evidence is never presented for this one.
        "different_target_evidence": "not_presented",
        "lexical_only_support": "strong_evidence_required",
    }.get(name)
    if expected:
        trace = [json.loads(line) for line in on.trace_path.read_text().splitlines()]
        codes = {
            e["reason_code"]
            for e in trace
            if e["event"] == "candidate_decision"
            and path_of(e["decision"]["target"]) == CART
            and e["decision"]["decision"] == "confirm"
        }
        assert codes == {expected}, (name, codes)
    # Unsupported claims stay dropped in both arms.
    if name == "unconfirmed_component_claimed":
        assert any("shop/tax.py" in claim for claim in off.report.dropped_claims)
        assert any("shop/tax.py" in claim for claim in on.report.dropped_claims)
