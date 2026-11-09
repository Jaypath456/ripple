"""Request-growth stress scenarios: original V2 context vs opt-in bounded context.

A scripted synthetic model drives the real controller; every request is measured
with the P2B-equivalent estimate in ``request_size.py`` (cap 16,000, allowance 4,096).
For bounded runs, each decision prompt is also checked for retained-evidence
correctness. No provider, credential or network is used.

Usage: python dev/accuracy/stress.py [--json PATH]
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness
import request_size

from ripple.agent import RIPPLE_V2, RIPPLE_V2_BOUNDED, AgentController
from ripple.agent_models import FeatureRequest
from ripple.llm import LLMResponse

BLOCK = re.compile(r"<repository_data>\n(.*)\n</repository_data>", re.DOTALL)

# Same bytes as the P2B stock development fixture (dev/p2b/fixtures/stock, Track A).
STOCK = {
    "warehouse/__init__.py": '"""Fresh local development fixture for the P2B pilot."""\n',
    "warehouse/api.py": (
        "from warehouse.status import stock_status\n\n\n"
        "def inventory_badge(on_hand: int, reserved: int) -> dict[str, str]:\n"
        '    return {"status": stock_status(on_hand, reserved)}\n'
    ),
    "warehouse/availability.py": (
        "def free_units(on_hand: int, reserved: int) -> int:\n"
        '    """Return unreserved units without changing inventory."""\n'
        "    return max(0, on_hand - reserved)\n"
    ),
    "warehouse/status.py": (
        "from warehouse.availability import free_units\n\n\n"
        "def stock_status(on_hand: int, reserved: int) -> str:\n"
        "    available = free_units(on_hand, reserved)\n"
        "    if available == 0:\n"
        '        return "out"\n'
        '    return "low" if available < 5 else "available"\n'
    ),
    "tests/test_status.py": (
        "from warehouse.status import stock_status\n\n\n"
        "def test_status():\n"
        '    assert stock_status(10, 0) == "available"\n'
    ),
}
STATUS = "warehouse/status.py"


def large_repo(unicode_text=False, depth=6, modules=12, symbols=8, body_lines=1):
    root = "/".join(f"segment_{n:02d}_inventory_watermark" for n in range(depth))
    if unicode_text:
        root = root.replace("segment_00", "sección_00")
    note = "Stock-level señal — ünïcödé ✓ 在庫 警告" if unicode_text else "Stock level"
    files = {f"{root}/__init__.py": ""}
    for number in range(modules):
        lines = [f'"""{note} module {number}."""', ""]
        if number:
            lines.insert(1, f"from {root.replace('/', '.')}.module_00 import *")
        for k in range(symbols):
            name = f"stock_watermark_policy_component_{number:02d}_{k}"
            lines += [
                f"def {name}(value, reserved=None, threshold=None, override=None):",
                f'    """{note}: {name} applies the low stock watermark."""',
                *[f"    value = value  # {note} step {i}" for i in range(body_lines)],
                "    return value",
                "",
            ]
        if number:
            lines += [
                f"stock_watermark_policy_component_00_{k}(1)" for k in range(symbols)
            ]
        files[f"{root}/module_{number:02d}.py"] = "\n".join(lines) + "\n"
    files["tests/test_watermark.py"] = (
        f"from {root.replace('/', '.')}.module_00 import *\n\n"
        + "".join(
            f"def test_component_{k}():\n"
            f"    assert stock_watermark_policy_component_00_{k}(1) == 1\n\n"
            for k in range(symbols)
        )
    )
    return root, files


class Scripted:
    """Synthetic responses: fixed plan, optional suspect proposals, fixed verdicts."""

    model = "stress-script"

    def __init__(self, scenario):
        self.scenario = scenario
        self.position = 0
        self.rows = []
        self.prompts = []

    def _record(self, operation, prompt):
        match = BLOCK.search(prompt)
        data = {}
        if match:
            try:
                data = json.loads(match.group(1))
            except ValueError:
                data = {}
        self.rows.append(
            {
                "operation": operation,
                "estimate": request_size.estimate(operation, prompt),
            }
        )
        self.prompts.append((operation, data))
        return data

    def interpret(self, prompt):
        self._record("interpret", prompt)
        return LLMResponse(
            payload={
                "summary": "stress scenario",
                "change_kinds": ["business_logic"],
                "search_terms": self.scenario["terms"],
            },
            model=self.model,
        )

    def choose_next_action(self, prompt):
        data = self._record("choose_next_action", prompt)
        plan = self.scenario["steps"]
        if self.position >= len(plan):
            if data.get("submission_ready") or not self.scenario.get("repeat", True):
                return LLMResponse(
                    payload={
                        "tool_name": "submit_report",
                        "arguments": {},
                        "reason": "done",
                    },
                    model=self.model,
                )
            step = plan[-1]
        else:
            step = plan[self.position]
        self.position += 1
        updates = []
        if self.scenario.get("suspect"):
            observations = [
                o
                for o in data.get("last_three_observations", [])
                if isinstance(o.get("result"), dict) and o["result"].get("ok")
            ]
            if observations:
                latest = observations[-1]["result"]
                known = {i["target"] for i in data.get("ledger", [])}
                found = []
                for key in ("hits", "references", "outline", "neighbors"):
                    for item in (latest.get("data") or {}).get(key, []):
                        target = (
                            item.get("symbol") or item.get("id") or item.get("path")
                        )
                        if target and target not in known and target not in found:
                            found.append(target)
                updates = [
                    {
                        "target": t,
                        "status": "suspected",
                        "reason": "touched by the latest observation",
                        "evidence_ids": [latest["evidence_id"]],
                    }
                    for t in found[: self.scenario.get("suspect_limit", 4)]
                ]
        return LLMResponse(
            payload={
                "tool_name": step[0],
                "arguments": step[1],
                "reason": "stress investigation",
                "ledger_updates": updates,
            },
            model=self.model,
        )

    def decide_candidates(self, prompt):
        data = self._record("decide_candidates", prompt)
        confirm = set(self.scenario.get("confirm", ()))
        reject = set(self.scenario.get("reject", ()))
        decisions = []
        for item in data.get("candidates", []):
            path = item["target"].partition("::")[0]
            strong = [e["evidence_id"] for e in item["evidence"] if e["strong"]]
            listed = [e["evidence_id"] for e in item["evidence"]]
            if path in confirm and strong:
                verdict = {"decision": "confirm", "evidence_ids": strong[:1]}
            elif path in reject:
                verdict = {"decision": "reject", "evidence_ids": listed[:1]}
            else:
                verdict = {"decision": "keep", "missing_evidence": "a caller or test"}
            decisions.append({"target": item["target"], "reason": "stress", **verdict})
        return LLMResponse(payload={"decisions": decisions}, model=self.model)

    def draft_report(self, prompt):
        data = self._record("draft_report", prompt)
        return LLMResponse(
            payload={
                "affected_components": [
                    {
                        "target": i["target"],
                        "change_type": "modify",
                        "change_kind": "business_logic",
                        "reason": "stress report",
                        "confidence": "low",
                        "evidence": i["evidence_ids"][-1:],
                    }
                    for i in data.get("ledger", [])
                    if i.get("status") == "confirmed"
                ]
            },
            model=self.model,
        )


class Checked(AgentController):
    """Records the controller's view at each decision prompt for correctness checks."""

    def _decision_prompt(self, intent):
        prompt = super()._decision_prompt(intent)
        match = BLOCK.search(prompt)
        data = json.loads(match.group(1)) if match else {}
        problems = []
        if self.observations:
            latest = self.observations[-1]
            shown = data["last_three_observations"][-1]
            for key in ("tool", "arguments", "controller", "duplicate", "error"):
                if latest.get(key) != shown.get(key):
                    problems.append(f"latest_{key}_changed")
            result, view = latest.get("result") or {}, shown.get("result") or {}
            for key in ("evidence_id", "ok", "error"):
                if result.get(key) != view.get(key):
                    problems.append(f"latest_result_{key}_changed")
        ledger = {i["target"]: i["status"] for i in data.get("ledger", [])}
        if ledger != {t: c.status for t, c in self.ledger.candidates.items()}:
            problems.append("ledger_targets_or_status_changed")
        cited = set(re.findall(r'"(e\d+)"', match.group(1) if match else ""))
        if cited - set(self.ledger.evidence):
            problems.append("unknown_evidence_id_shown")
        self.problems = getattr(self, "problems", []) + problems
        return prompt


def scenarios():
    stock_terms = ["stock_status", "warning_threshold", "reserved"]
    wandering = [
        ["inspect_symbol", {"target": STATUS}],
        ["find_references", {"symbol_id": f"{STATUS}::stock_status"}],
        ["search_code", {"query": "stock status low watermark", "limit": 15}],
        ["find_references", {"symbol_id": STATUS}],
        ["inspect_symbol", {"target": "warehouse/api.py"}],
        ["get_dependencies", {"path": STATUS, "direction": "imported_by"}],
        ["find_tests", {"target": "warehouse/availability.py"}],
        ["inspect_symbol", {"target": "warehouse/missing.py"}],
        ["search_code", {"query": "free units reserved available", "limit": 15}],
        ["co_changed", {"path": STATUS}],
        ["repo_facts", {"kind": "settings"}],
        ["inspect_symbol", {"target": STATUS}],
        ["find_tests", {"target": STATUS}],
    ]
    root, _ = large_repo()
    module = f"{root}/module_00.py"
    symbol = f"{module}::stock_watermark_policy_component_00_0"
    big = [
        ["search_code", {"query": "stock watermark policy component", "limit": 15}],
        ["find_references", {"symbol_id": symbol, "limit": 40}],
        ["inspect_symbol", {"target": module}],
        ["get_dependencies", {"path": module, "direction": "imported_by", "depth": 2}],
        ["find_tests", {"target": module}],
        ["search_code", {"query": "low stock watermark reserved", "limit": 15}],
        ["find_references", {"symbol_id": module, "limit": 40}],
        ["inspect_symbol", {"target": f"{root}/module_99.py"}],
        *[
            ["inspect_symbol", {"target": f"{root}/module_{n:02d}.py"}]
            for n in range(1, 12)
        ],
    ]
    failures = [
        ["inspect_symbol", {"target": f"{root}/missing_{n}.py"}] for n in range(6)
    ]
    big_terms = ["stock", "watermark", "policy", "component", "reserved"]
    return {
        "small-single-file": (
            STOCK,
            {
                "terms": stock_terms,
                "steps": wandering[:3] + [["find_tests", {"target": STATUS}]],
                "confirm": [STATUS],
                "repeat": False,
            },
        ),
        "small-long-horizon": (
            STOCK,
            {"terms": stock_terms, "steps": wandering, "suspect": True},
        ),
        "small-conflicting-evidence": (
            STOCK,
            {
                "terms": stock_terms,
                "steps": wandering,
                "confirm": [STATUS],
                "reject": ["warehouse/api.py"],
            },
        ),
        "small-repeated-inspection": (
            STOCK,
            {
                "terms": stock_terms,
                "steps": [["inspect_symbol", {"target": STATUS}]] * 12,
            },
        ),
        "small-empty-results": (
            STOCK,
            {
                "terms": ["kubernetes", "helm"],
                "steps": [["search_code", {"query": "kubernetes helm ingress"}]] * 6,
            },
        ),
        "large-max-window": ("large", {"terms": big_terms, "steps": big}),
        "large-max-candidates": (
            "large",
            {"terms": big_terms, "steps": big, "suspect": True, "suspect_limit": 6},
        ),
        "large-checkpoint-history": (
            "large",
            {"terms": big_terms, "steps": big, "suspect": True, "confirm": [module]},
        ),
        "large-multi-file-confirm": (
            "large",
            {
                "terms": big_terms,
                "steps": big,
                "suspect": True,
                "confirm": [module, f"{root}/module_01.py", f"{root}/module_02.py"],
            },
        ),
        "large-failed-tools": (
            "large",
            {"terms": big_terms, "steps": big[:2] + failures},
        ),
        "large-symbols-long-outputs": (
            "large-bodies",
            {"terms": big_terms, "steps": big},
        ),
        "unicode-paths-and-text": (
            "unicode",
            {
                "terms": big_terms,
                "steps": [
                    [
                        tool,
                        {
                            k: v.replace("segment_00", "sección_00")
                            if isinstance(v, str)
                            else v
                            for k, v in args.items()
                        },
                    ]
                    for tool, args in big
                ],
                "suspect": True,
            },
        ),
    }


REPOS = {
    "large": lambda: large_repo()[1],
    "large-bodies": lambda: large_repo(body_lines=30, symbols=12)[1],
    "unicode": lambda: large_repo(unicode_text=True)[1],
}


def run(name, files, scenario, variant, workdir):
    index = harness.build_repository(
        files, workdir / "repos" / f"{name}-{variant.name}"
    )
    model = Scripted(scenario)
    controller = Checked(
        index, model, output_root=workdir / "out" / name, variant=variant
    )
    result = controller.run(
        FeatureRequest(text="Add a configurable low stock watermark.")
    )
    trace = [json.loads(x) for x in result.trace_path.read_text().splitlines()]
    contexts = [e for e in trace if e["event"] == "request_context"]
    omitted = {}
    for event in contexts:
        for key, count in (event.get("omitted") or {}).items():
            omitted[key] = omitted.get(key, 0) + count
        if event.get("candidates_deferred"):
            omitted["checkpoint_candidates_deferred"] = (
                omitted.get("checkpoint_candidates_deferred", 0)
                + event["candidates_deferred"]
            )
        if event.get("evidence_omitted"):
            omitted["checkpoint_evidence"] = (
                omitted.get("checkpoint_evidence", 0) + event["evidence_omitted"]
            )
    by_operation = {}
    for row in model.rows:
        by_operation[row["operation"]] = max(
            by_operation.get(row["operation"], 0), row["estimate"]
        )
    worst = max(row["estimate"] for row in model.rows)
    return {
        "requests": len(model.rows),
        "stop_reason": result.report.run_stats.stop_reason,
        "status": result.report.status,
        "candidates": len(controller.ledger.candidates),
        "max_estimate": worst,
        "headroom": request_size.CAP - worst,
        "over_cap_requests": sum(r["estimate"] > request_size.CAP for r in model.rows),
        "max_by_operation": by_operation,
        "max_level": max((e.get("level", 0) for e in contexts), default=0),
        "omitted": dict(sorted(omitted.items())),
        "context_refusals": sum(e["event"] == "request_context_refused" for e in trace),
        "checkpoints_deferred_for_budget": sum(
            e["event"] == "checkpoint_deferred"
            and e.get("reason_code") == "context_budget"
            for e in trace
        ),
        "correctness_problems": sorted(set(getattr(controller, "problems", []))),
    }


def measure(workdir: Path):
    rows = []
    for name, (repo, scenario) in scenarios().items():
        files = repo if isinstance(repo, dict) else REPOS[repo]()
        original = run(name, files, scenario, RIPPLE_V2, workdir)
        bounded = run(name, files, scenario, RIPPLE_V2_BOUNDED, workdir)
        rows.append({"scenario": name, "original": original, "bounded": bounded})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="ripple-p3-stress-") as folder:
        rows = measure(Path(folder))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    print(
        f"{'scenario':30} {'orig max':>8} {'bounded':>8} {'headroom':>8} lvl  stop / omitted"
    )
    for row in rows:
        o, b = row["original"], row["bounded"]
        print(
            f"{row['scenario']:30} {o['max_estimate']:>8} {b['max_estimate']:>8} "
            f"{b['headroom']:>8} {b['max_level']:>3}  {b['stop_reason']} "
            f"{b['omitted']} {b['correctness_problems']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
