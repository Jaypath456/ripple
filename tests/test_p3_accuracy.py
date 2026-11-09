"""P3 opt-in proof-driven selection, diversified discovery, and evidence safety."""

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from ripple.agent import (
    MAX_TOOL_CALLS,
    PRESUBMIT_PROOF_STEPS,
    RIPPLE_V2,
    RIPPLE_V2_DIVERSE,
    RIPPLE_V2_P3,
    RIPPLE_V2_PROOF,
    SEED_LIMIT,
    AgentController,
)
from ripple.agent_models import AgentDecision, CandidateUpdate, FeatureRequest
from ripple.llm import LLMResponse
from ripple.proof import call_key, plan_proof_steps
from ripple.scanner import scan_repository
from ripple.tools import ToolResult, error_result

SHOP = {
    "shop/__init__.py": "",
    "shop/pricing.py": (
        "DISCOUNT_CODES = {}\n\n\ndef unit_price(item):\n    return item\n\n\n"
        "def apply_discount(total, code):\n    return total\n"
    ),
    "shop/tax.py": "def sales_tax(amount, region):\n    return amount\n",
    "shop/cart.py": (
        "from shop.pricing import apply_discount\nfrom shop.tax import sales_tax\n\n\n"
        "def cart_total(items, code):\n"
        "    return sales_tax(apply_discount(items, code), 'NY')\n"
    ),
    "shop/api.py": (
        "from shop.cart import cart_total\n\n\n"
        "def checkout_handler(request):\n    return cart_total(request, None)\n"
    ),
    "shop/empty.py": "VALUE = 1\n",
    "tests/test_pricing.py": (
        "from shop.pricing import apply_discount\n\n\n"
        "def test_discount():\n    assert apply_discount(1, None) == 1\n"
    ),
}
PRICING, TAX, CART, API = (
    "shop/pricing.py",
    "shop/tax.py",
    "shop/cart.py",
    "shop/api.py",
)


def build(tmp_path: Path, files: dict[str, str]):
    root = tmp_path / "repo"
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    root.mkdir(exist_ok=True)
    for args in (
        ("init", "-q"),
        ("add", "-A"),
        ("commit", "-qm", "fixture", "--allow-empty"),
    ):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=RIPPLE",
                "-c",
                "user.email=ripple@example.test",
                "-c",
                "commit.gpgsign=false",
                "-C",
                str(root),
                *args,
            ],
            check=True,
            capture_output=True,
        )
    return scan_repository(root)


@pytest.fixture
def shop(tmp_path):
    return build(tmp_path, SHOP)


def _data(prompt: str) -> dict:
    match = re.search(r"<repository_data>\n(.*)\n</repository_data>", prompt, re.DOTALL)
    return json.loads(match.group(1)) if match else {}


class Script:
    """Scripted model: a fixed action plan and a fixed per-path checkpoint verdict."""

    model = "p3-script"

    def __init__(
        self,
        terms=("discount", "apply_discount", "cart_total", "sales_tax"),
        plan=(),
        confirm=(),
        reject=(),
        cite=None,
        submit=True,
    ) -> None:
        self.terms = list(terms)
        self.plan = list(plan)
        self.confirm, self.reject = set(confirm), set(reject)
        self.cite = cite
        self.submit = submit
        self.prompts: list[dict] = []
        self.checkpoints: list[dict] = []

    def _reply(self, payload):
        return LLMResponse(payload=payload, model=self.model)

    def interpret(self, prompt):
        return self._reply(
            {
                "summary": "s",
                "change_kinds": ["business_logic"],
                "search_terms": self.terms,
            }
        )

    def choose_next_action(self, prompt):
        data = _data(prompt)
        self.prompts.append(data)
        if self.plan:
            tool, arguments = self.plan.pop(0)
            return self._reply(
                {"tool_name": tool, "arguments": arguments, "reason": "scripted"}
            )
        if self.submit or data.get("submission_ready"):
            return self._reply(
                {"tool_name": "submit_report", "arguments": {}, "reason": "scripted"}
            )
        return self._reply(
            {
                "tool_name": "search_code",
                "arguments": {"query": "discount"},
                "reason": "scripted",
            }
        )

    def decide_candidates(self, prompt):
        data = _data(prompt)
        self.checkpoints.append(data)
        decisions = []
        for item in data["candidates"]:
            path = item["target"].partition("::")[0]
            listed = [e["evidence_id"] for e in item["evidence"]]
            strong = [e["evidence_id"] for e in item["evidence"] if e["strong"]]
            if path in self.confirm:
                cited = self.cite(item) if self.cite else strong[:1]
                decisions.append(
                    {
                        "target": item["target"],
                        "decision": "confirm",
                        "evidence_ids": cited,
                        "reason": "relevant",
                    }
                )
            elif path in self.reject:
                decisions.append(
                    {
                        "target": item["target"],
                        "decision": "reject",
                        "evidence_ids": listed[:1],
                        "reason": "unrelated",
                    }
                )
            else:
                decisions.append(
                    {"target": item["target"], "decision": "keep", "reason": "unclear"}
                )
        return self._reply({"decisions": decisions})

    def draft_report(self, prompt):
        confirmed = [i for i in _data(prompt)["ledger"] if i["status"] == "confirmed"]
        return self._reply(
            {
                "affected_components": [
                    {
                        "target": i["target"],
                        "change_type": "modify",
                        "change_kind": "business_logic",
                        "reason": "scripted",
                        "confidence": "medium",
                        "evidence": i["evidence_ids"][-1:],
                    }
                    for i in confirmed
                ]
            }
        )


def run(index, llm, tmp_path, variant, request="Add a loyalty discount to the cart."):
    controller = AgentController(
        index, llm, output_root=tmp_path / variant.name, variant=variant
    )
    result = controller.run(FeatureRequest(text=request))
    trace = [json.loads(line) for line in result.trace_path.read_text().splitlines()]
    return controller, result, trace


def controller_for(index, tmp_path, variant=RIPPLE_V2_PROOF):
    return AgentController(index, Script(), output_root=tmp_path / "c", variant=variant)


def plan(controller):
    return [(s.proof, s.tool, s.arguments) for s in controller._proof_plan()]


def seed_files(controller, targets):
    words = " ".join(t.replace("/", " ").replace("::", " ") for t in targets)
    result, _ = controller._execute("search_code", {"query": words, "limit": 15})
    for target in targets:
        assert controller.ledger.seed(target, "seed", result.evidence_id)


# ------------------------------------------------------------ B2 proof planning


def test_planner_orders_inspection_then_references_then_tests(shop, tmp_path):
    controller = controller_for(shop, tmp_path)
    seed_files(controller, [PRICING])
    assert plan(controller)[0] == ("inspection", "inspect_symbol", {"target": PRICING})
    controller._execute("inspect_symbol", {"target": PRICING})
    first = plan(controller)[0]
    assert first[:2] == ("references", "find_references")
    # A file target is checked through its first top-level symbol.
    assert first[2]["symbol_id"] == f"{PRICING}::unit_price"
    controller._execute(first[1], first[2])
    assert plan(controller) == [("tests", "find_tests", {"target": PRICING})]
    controller._execute("find_tests", {"target": PRICING})
    assert plan(controller) == []


def test_symbol_target_references_itself(shop, tmp_path):
    controller = controller_for(shop, tmp_path)
    symbol = f"{PRICING}::apply_discount"
    seed_files(controller, [symbol])
    controller._execute("inspect_symbol", {"target": symbol})
    assert plan(controller)[0][2] == {"symbol_id": symbol, "limit": 40}


def test_breadth_first_over_multiple_plausible_candidates(shop, tmp_path):
    controller = controller_for(shop, tmp_path)
    seed_files(controller, [PRICING, TAX, CART])
    steps = plan(controller)
    assert [s[0] for s in steps[:3]] == ["inspection"] * 3
    assert [s[2]["target"] for s in steps[:3]] == [PRICING, TAX, CART]


def test_attempted_and_failed_calls_are_never_replanned(shop, tmp_path, monkeypatch):
    controller = controller_for(shop, tmp_path)
    seed_files(controller, [PRICING])
    controller._execute("inspect_symbol", {"target": PRICING})
    step = controller._proof_plan()[0]
    original = controller.session.invoke

    def failing(name, arguments):
        if name == "find_references":
            evidence = original("search_code", {"query": "x"}).evidence_id
            return error_result(evidence, "synthetic_failure", "tool failed")
        return original(name, arguments)

    monkeypatch.setattr(controller.session, "invoke", failing)
    result, ok = controller._execute(step.tool, step.arguments)
    assert result is not None and not result.ok and not ok
    candidate = controller.ledger.candidates[PRICING]
    assert not candidate.checked_refs  # a failed check is not a check
    assert step.key() not in {s.key() for s in controller._proof_plan()}
    assert plan(controller) == [("tests", "find_tests", {"target": PRICING})]


def test_rejected_test_and_irrelevant_candidates_get_no_proof(shop, tmp_path):
    controller = controller_for(shop, tmp_path)
    seed_files(controller, [PRICING, "tests/test_pricing.py"])
    controller._execute("inspect_symbol", {"target": PRICING})
    record = next(iter(controller.ledger.support(PRICING)[-1:]))
    assert controller.ledger.decide_checked(
        PRICING,
        "reject",
        (record.evidence_id,),
        "unrelated",
        frozenset({record.evidence_id}),
    ).changed
    assert plan(controller) == []


def test_no_candidates_means_no_plan_and_abstention(shop, tmp_path):
    llm = Script(terms=["kubernetes", "helm", "ingress"], submit=False)
    controller, result, trace = run(
        shop, llm, tmp_path, RIPPLE_V2_PROOF, "Upgrade the Kubernetes Helm chart."
    )
    assert not controller.ledger.candidates and controller._proof_plan() == []
    assert result.report.status == "abstained" and not result.report.affected_components
    assert not any(e["event"] == "proof_step_selected" for e in trace)


def test_exhausted_tool_budget_stops_proof(shop, tmp_path):
    controller = controller_for(shop, tmp_path)
    seed_files(controller, [PRICING])
    controller.tool_calls = MAX_TOOL_CALLS
    assert controller._proof_plan()  # work remains, but no budget to do it
    assert controller._proof_replacement("duplicate_call") is None
    decision = AgentDecision(tool_name="submit_report", arguments={}, reason="scripted")
    assert (
        controller._presubmit_proof(FeatureRequest(text="synthetic request"), decision)
        is None
    )


# ---------------------------------------------------------- B2 controller policy


def test_wasted_choices_are_replaced_and_marked(shop, tmp_path):
    plan_ = [
        ("find_references", {"symbol_id": PRICING}),  # file path: refused target
        ("search_code", {"query": "discount apply_discount cart_total sales_tax"}),
    ]
    llm = Script(plan=plan_, submit=False)
    controller, _, trace = run(shop, llm, tmp_path, RIPPLE_V2_PROOF)
    selected = [e for e in trace if e["event"] == "proof_step_selected"]
    assert selected[0]["reason_code"] == "invalid_target"
    assert selected[0]["model_choice"]["arguments"] == {"symbol_id": PRICING}
    assert {e["reason_code"] for e in selected} >= {"invalid_target"}
    assert not any(e["event"] == "tool_target_rejected" for e in trace[:20])
    marked = [o for o in controller.observations if o.get("selected_by")]
    assert len(marked) == controller.proof_steps == len(selected)
    assert controller.tool_calls <= MAX_TOOL_CALLS


def test_useful_model_choice_is_never_replaced(shop, tmp_path):
    llm = Script(plan=[("inspect_symbol", {"target": API})])
    _, _, trace = run(shop, llm, tmp_path, RIPPLE_V2_PROOF)
    calls = [e for e in trace if e["event"] == "tool_call"]
    assert calls[1]["arguments"] == {"target": API}
    first_choice = next(e for e in trace if e["event"] == "decision")
    assert not any(
        e["event"] == "proof_step_selected"
        for e in trace[trace.index(first_choice) : trace.index(calls[1])]
    )


def test_baseline_v2_is_unchanged_by_the_new_flags(shop, tmp_path):
    for variant in (
        RIPPLE_V2,
        replace(RIPPLE_V2_P3, name="X", proof_selection=False, diverse_seeding=False),
    ):
        llm = Script(plan=[("find_references", {"symbol_id": PRICING})])
        controller, _, trace = run(shop, llm, tmp_path, variant)
        assert all("next_proof_step" not in p for p in llm.prompts)
        assert not any(
            e["event"] in {"proof_step_selected", "submission_deferred"} for e in trace
        )
        assert "accuracy_policies" not in trace[0]
        assert any(e["event"] == "tool_target_rejected" for e in trace)
        assert controller.proof_steps == 0


def test_prompt_exposes_the_executable_next_step(shop, tmp_path):
    llm = Script()
    run(shop, llm, tmp_path, RIPPLE_V2_PROOF)
    step = llm.prompts[0]["next_proof_step"]
    assert step["tool"] == "inspect_symbol" and step["proof"] == "inspection"
    assert step["target"] in {c["target"] for c in llm.prompts[0]["ledger"]}


def test_refused_submission_runs_the_blocking_check(shop, tmp_path):
    # Confirm pricing, then try to submit before references and tests exist.
    llm = Script(plan=[("inspect_symbol", {"target": PRICING})] * 1, confirm={PRICING})
    controller, result, trace = run(shop, llm, tmp_path, RIPPLE_V2_PROOF)
    reasons = [e["reason_code"] for e in trace if e["event"] == "proof_step_selected"]
    assert "submission_refused" in reasons or "presubmission_proof" in reasons
    candidate = next(c for c in controller.ledger.confirmed())
    assert candidate.checked_refs and candidate.checked_tests
    assert result.report.run_stats.stop_reason == "submitted"


def test_presubmission_proof_is_bounded_and_never_confirms(shop, tmp_path):
    # Only pricing is ever confirmed: the extra proof must not add confirmations.
    llm = Script(confirm={PRICING})
    controller, result, trace = run(shop, llm, tmp_path, RIPPLE_V2_PROOF)
    assert controller.presubmit_steps <= PRESUBMIT_PROOF_STEPS
    assert {c.target.partition("::")[0] for c in controller.ledger.confirmed()} == {
        PRICING
    }
    deferred = [e for e in trace if e["event"] == "submission_deferred"]
    assert deferred and sum(e["steps"] for e in deferred) == controller.presubmit_steps
    assert result.report.run_stats.stop_reason == "submitted"
    assert {c.target.partition("::")[0] for c in result.report.affected_components} == {
        PRICING
    }


def test_presubmission_proof_reaches_a_second_confirmable_file(shop, tmp_path):
    base = Script(confirm={PRICING, CART})
    _, base_run, _ = run(shop, base, tmp_path, RIPPLE_V2)
    proof = Script(confirm={PRICING, CART})
    _, proof_run, _ = run(shop, proof, tmp_path, RIPPLE_V2_PROOF)
    before = {c.target.partition("::")[0] for c in base_run.report.affected_components}
    after = {c.target.partition("::")[0] for c in proof_run.report.affected_components}
    assert before < after and CART in after - before


def test_checks_recorded_before_admission_are_credited_only_with_proof(shop, tmp_path):
    for variant, expected in ((RIPPLE_V2, False), (RIPPLE_V2_PROOF, True)):
        controller = controller_for(shop, tmp_path, variant)
        controller._execute("find_tests", {"target": TAX})
        result, _ = controller._execute("search_code", {"query": "sales_tax"})
        controller.ledger.seed(f"{TAX}::sales_tax", "seed", result.evidence_id)
        controller._admitted()
        assert (
            controller.ledger.candidates[f"{TAX}::sales_tax"].checked_tests is expected
        )


# --------------------------------------------------------- B3 discovery


CONCENTRATED = {
    "pkg/__init__.py": "",
    "pkg/watermark.py": "".join(
        f"def watermark_level_{n}(value):\n    return value\n\n\n" for n in range(10)
    ),
    "pkg/alerts.py": "def watermark_alert(value):\n    return value\n",
    "pkg/config.py": "WATERMARK_LEVEL = 3\n\n\ndef watermark_setting():\n    return 3\n",
    "tests/test_watermark.py": "def test_watermark_level():\n    assert True\n",
}


def seeded(index, tmp_path, variant, terms):
    controller = AgentController(
        index, Script(terms=terms), output_root=tmp_path / variant.name, variant=variant
    )
    controller.intent = None
    from ripple.agent import _fallback_intent

    intent = _fallback_intent(FeatureRequest(text="synthetic request")).model_copy(
        update={"search_terms": tuple(terms)}
    )
    controller._seed(intent)
    return controller


def test_diverse_seeding_spreads_slots_across_files(tmp_path):
    index = build(tmp_path, CONCENTRATED)
    terms = ["watermark", "level"]
    base = seeded(index, tmp_path, RIPPLE_V2, terms)
    diverse = seeded(index, tmp_path, RIPPLE_V2_DIVERSE, terms)

    def files(c):
        return {t.partition("::")[0] for t in c.ledger.candidates}

    # Baseline spends its slots on one file's symbols; the same hits now reach
    # every matching source file, and the test file is left to find_tests.
    assert files(base) <= {"pkg/watermark.py", "tests/test_watermark.py"}
    assert {"pkg/watermark.py", "pkg/alerts.py", "pkg/config.py"} <= files(diverse)
    assert "tests/test_watermark.py" not in files(diverse)
    assert len(diverse.ledger.candidates) <= SEED_LIMIT
    # The first slot is still the top-ranked source hit.
    assert next(iter(diverse.ledger.candidates)) == next(
        t for t in base.ledger.candidates if not t.startswith("tests/")
    )


def test_similar_symbol_names_in_different_files_are_both_seeded(tmp_path):
    files = {
        "users/display.py": "def format_name(user):\n    return user\n",
        "reports/display.py": "def format_name(report):\n    return report\n",
    }
    index = build(tmp_path, files)
    diverse = seeded(index, tmp_path, RIPPLE_V2_DIVERSE, ["format_name", "display"])
    assert {t.partition("::")[0] for t in diverse.ledger.candidates} == set(files)


@pytest.mark.parametrize("files", [{}, {"README.md": "docs only\n"}])
def test_empty_or_non_python_repository_abstains(tmp_path, files):
    index = build(tmp_path, files)
    controller, result, _ = run(
        index, Script(submit=False), tmp_path, RIPPLE_V2_P3, "Fix the README."
    )
    assert not controller.ledger.candidates and result.report.status == "abstained"


GRAPH = {
    "core/__init__.py": "",
    "core/a.py": "from core import b\n\n\ndef retry_call():\n    return b.delay()\n",
    "core/b.py": "from core import a\n\n\ndef delay():\n    return 1\n",
    **{
        f"core/user_{n}.py": f"from core.a import retry_call\n\n\ndef u{n}():\n"
        "    return retry_call()\n"
        for n in range(8)
    },
    "tests/test_a.py": "from core.a import retry_call\n\n\ndef test_a():\n    assert retry_call()\n",
}


def confirm_file(controller, target):
    result, _ = controller._execute("search_code", {"query": "retry_call"})
    controller.ledger.seed(target, "seed", result.evidence_id)
    inspected, _ = controller._execute("inspect_symbol", {"target": target})
    assert controller.ledger.decide_checked(
        target,
        "confirm",
        (inspected.evidence_id,),
        "scripted",
        frozenset({inspected.evidence_id}),
    ).changed


def test_expansion_is_bounded_on_large_cyclic_graphs(tmp_path):
    index = build(tmp_path, GRAPH)
    controller = controller_for(index, tmp_path, RIPPLE_V2_P3)
    confirm_file(controller, "core/a.py")
    assert controller._structural_expansion()
    admitted = controller.structural
    assert "core/b.py" in admitted  # imports and imported_by of a cycle: once
    assert len(admitted) <= 6 and not any(t.startswith("tests/") for t in admitted)
    by_direction = [
        e
        for e in controller.trace.path.read_text().splitlines()
        if '"structural_expansion"' in e
    ]
    assert all(json.loads(e)["admitted"] <= 3 for e in by_direction)
    assert all(controller.ledger.candidates[t].status == "suspected" for t in admitted)
    # Already-expanded files are never expanded again.
    before = controller.tool_calls
    assert not controller._structural_expansion()
    assert controller.tool_calls == before


def test_structural_neighbour_cannot_be_confirmed_on_dependency_evidence(tmp_path):
    index = build(tmp_path, GRAPH)
    controller = controller_for(index, tmp_path, RIPPLE_V2_P3)
    confirm_file(controller, "core/a.py")
    controller._structural_expansion()
    neighbour = "core/b.py"
    structural = [r for r in controller.ledger.support(neighbour) if r.strong]
    assert structural and all(r.tool_name == "get_dependencies" for r in structural)
    # Not offered at a checkpoint until it has direct evidence of its own.
    llm = Script(
        confirm={neighbour},
        cite=lambda item: [
            e["evidence_id"]
            for e in item["evidence"]
            if e["tool"] == "get_dependencies"
        ][:1],
    )
    controller.llm = llm
    controller._checkpoint(
        FeatureRequest(text="synthetic request"), stall=3, force=True
    )
    offered = {c["target"] for cp in llm.checkpoints for c in cp["candidates"]}
    assert neighbour not in offered
    controller._execute("inspect_symbol", {"target": neighbour})
    controller._checkpoint(
        FeatureRequest(text="synthetic request"), stall=3, force=True
    )
    assert controller.ledger.candidates[neighbour].status == "suspected"
    trace = [json.loads(e) for e in controller.trace.path.read_text().splitlines()]
    refusals = [
        e
        for e in trace
        if e["event"] == "candidate_decision" and e["decision"]["target"] == neighbour
    ]
    assert refusals[-1]["reason_code"] == "direct_evidence_required"
    # Citing its own inspection is accepted by the unchanged ledger rules.
    llm.cite = lambda item: [
        e["evidence_id"] for e in item["evidence"] if e["tool"] == "inspect_symbol"
    ][:1]
    controller.presented.pop(neighbour, None)
    controller._checkpoint(
        FeatureRequest(text="synthetic request"), stall=3, force=True
    )
    assert controller.ledger.candidates[neighbour].status == "confirmed"


def test_file_without_symbols_plans_no_reference_check(shop, tmp_path):
    controller = controller_for(shop, tmp_path)
    seed_files(controller, ["shop/__init__.py"])
    tools = [s[1] for s in plan(controller)]
    assert "find_references" not in tools and "find_tests" in tools


# ------------------------------------------------------- B4 evidence safety


def test_disabled_tools_are_never_planned_or_executed(shop, tmp_path):
    variant = replace(RIPPLE_V2_P3, name="no-refs", references=False)
    llm = Script(
        plan=[("find_references", {"symbol_id": f"{PRICING}::apply_discount"})]
    )
    controller, _, trace = run(shop, llm, tmp_path, variant)
    assert not any(
        e["event"] == "tool_call" and e["tool"] == "find_references" for e in trace
    )
    assert all(s.tool != "find_references" for s in controller._proof_plan())


def test_proof_steps_never_change_status_or_bypass_validation(shop, tmp_path):
    llm = Script(plan=[("find_references", {"symbol_id": PRICING})] * 3, submit=False)
    controller, result, _ = run(shop, llm, tmp_path, RIPPLE_V2_PROOF)
    assert not controller.ledger.confirmed() and not result.report.affected_components
    statuses = {c.status for c in controller.ledger.candidates.values()}
    assert statuses <= {"suspected", "rejected"}
    assert controller.proof_steps > 0


def test_stale_forged_failed_and_cross_target_citations_stay_refused(shop, tmp_path):
    controller = controller_for(shop, tmp_path, RIPPLE_V2_P3)
    seed_files(controller, [PRICING, TAX])
    inspected, _ = controller._execute("inspect_symbol", {"target": PRICING})
    controller._execute("inspect_symbol", {"target": TAX})
    latest = controller._latest_successful_evidence()
    stale = CandidateUpdate(
        target=CART,
        status="suspected",
        reason="scripted",
        evidence_ids=(inspected.evidence_id,),
    )
    assert controller.ledger.apply_checked(stale, latest).reason_code in {
        "latest_evidence_required",
        "untouched_target",
    }
    ledger = controller.ledger
    presented = frozenset({inspected.evidence_id, "e999"})
    assert (
        ledger.decide_checked(
            PRICING, "confirm", ("e999",), "scripted", presented
        ).reason_code
        == "untouched_target"
    )
    assert (
        ledger.decide_checked(
            TAX,
            "confirm",
            (inspected.evidence_id,),
            "scripted",
            frozenset({inspected.evidence_id}),
        ).reason_code
        == "untouched_target"
    )
    search = next(r for r in ledger.evidence.values() if r.tool_name == "search_code")
    assert ledger.decide_checked(
        PRICING,
        "confirm",
        (search.evidence_id,),
        "scripted",
        frozenset({search.evidence_id}),
    ).reason_code in {"strong_evidence_required", "untouched_target"}
    assert not ledger.confirmed()


def test_call_keys_match_the_controller_cache(shop, tmp_path):
    controller = controller_for(shop, tmp_path)
    controller._execute("find_tests", {"target": PRICING})
    assert call_key("find_tests", {"target": PRICING}) in controller.cache
    steps = plan_proof_steps(
        controller.ledger,
        attempted=controller.cache.keys(),
        tests=controller._tests,
        require_refs=True,
        symbols_in=controller._symbols_in,
    )
    assert steps == []
    assert isinstance(ToolResult, type)


def test_limit_only_repeat_of_a_failed_call_is_not_replanned(
    shop, tmp_path, monkeypatch
):
    controller = controller_for(shop, tmp_path)
    seed_files(controller, [PRICING])
    controller._execute("inspect_symbol", {"target": PRICING})
    original = controller.session.invoke

    def failing(name, arguments):
        if name == "find_references":
            evidence = original("search_code", {"query": "x"}).evidence_id
            return error_result(evidence, "synthetic_failure", "tool failed")
        return original(name, arguments)

    monkeypatch.setattr(controller.session, "invoke", failing)
    symbol = f"{PRICING}::unit_price"
    controller._execute("find_references", {"symbol_id": symbol, "limit": 10})
    # The planner would ask for limit=40; the controller would serve the cached
    # failure, so the step must not be planned again.
    assert all(s.tool != "find_references" for s in controller._proof_plan())


def test_unicode_paths_are_seeded_and_planned(tmp_path):
    files = {
        "paquete/__init__.py": "",
        "paquete/módulo_señal.py": "def señal_umbral(valor):\n    return valor\n",
        "paquete/otro.py": "def umbral_otro():\n    return 1\n",
    }
    index = build(tmp_path, files)
    controller = seeded(index, tmp_path, RIPPLE_V2_P3, ["umbral", "señal"])
    paths = {t.partition("::")[0] for t in controller.ledger.candidates}
    assert "paquete/módulo_señal.py" in paths
    steps = controller._proof_plan()
    assert any(
        s.arguments.get("target", "").startswith("paquete/módulo") for s in steps
    )
    for step in steps:
        result, _ = controller._execute(step.tool, step.arguments)
        assert result is not None and result.ok
