"""Offline request-size stress measurement for the experimental UB profile.

Synthetic scripted responses drive the unchanged inherited AgentController against
local git fixtures. Every request is built by the real OpenAILLM Responses path and
measured with the experimental policy's own estimator (canonical request bytes plus
the 4,096 framing allowance) both as the agent renders it and after the experimental
lossless compaction. No SDK client, credential, network or admission journal is used.
Serialized bytes are a local estimate, not a verified tokenizer count.
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

from ripple import pilot
from ripple.agent import RIPPLE_V2, AgentController
from ripple.agent_models import FeatureRequest
from ripple.llm import OpenAILLM, RetryPolicy
from ripple.pilot_admission import Limits
from ripple.pilot_experimental import ExperimentalPolicy
from ripple.pilot_formatting import _BLOCK, compact_prompt
from ripple.scanner import scan_repository

SCHEMA_OPERATIONS = {
    "featureintent": "interpret",
    "agentdecision": "choose_next_action",
    "candidatedecisionset": "decide_candidates",
    "reportdraft": "draft_report",
}
CAP = Limits().input_tokens
POLICY = ExperimentalPolicy()


def bound(kwargs) -> int:
    """The experimental estimate for the request exactly as WireEndpoint sends it."""

    return POLICY.input_bound({**kwargs, "max_output_tokens": Limits().output_tokens})


def _escaped(value) -> int:
    """Canonical-request bytes of a compact JSON value embedded in the prompt."""

    return len(json.dumps(json.dumps(value, separators=(",", ":")))) - 2


# Proposal only: a deterministic bounded decision context, measured offline here and
# never applied to a live, captured or replayed request. Adopting it would change
# the inherited agent's prompt and therefore needs a new reviewed P2B base.
OBSERVATION_ITEM_BUDGET = 3000
EVIDENCE_TAIL = 6
OLDER_TAIL = 8
LEDGER_DETAIL = 10


def _digest(observation):
    """Earlier observations keep identity, status and size, not raw payloads."""

    result = observation.get("result")
    if not isinstance(result, dict):
        return observation
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return {
        "tool": observation.get("tool"),
        "arguments": observation.get("arguments"),
        "duplicate": observation.get("duplicate"),
        "result": {
            "evidence_id": result.get("evidence_id"),
            "ok": result.get("ok"),
            "truncated": result.get("truncated"),
            "error": result.get("error"),
            "items": {k: len(v) for k, v in data.items() if isinstance(v, list)},
        },
    }


def _select_items(observation, known):
    """Bound the latest payload by whole items, candidates' items first, in order."""

    result = observation.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        return observation
    data = dict(result["data"])
    for key, items in list(data.items()):
        if not isinstance(items, list) or _escaped(items) <= OBSERVATION_ITEM_BUDGET:
            continue

        def touches(item):
            values = item.values() if isinstance(item, dict) else ()
            return any(str(v).partition("::")[0] in known for v in values)

        ordered = [i for i in items if touches(i)] + [
            i for i in items if not touches(i)
        ]
        kept, used = [], 2
        for item in ordered:
            size = _escaped(item) + 1
            if used + size > OBSERVATION_ITEM_BUDGET:
                break
            kept.append(item)
            used += size
        data[key] = [item for item in items if item in kept]
        data[f"{key}_omitted"] = len(items) - len(kept)
    return {
        **observation,
        "result": {**result, "data": data, "truncated": True},
    }


def bounded_decision_context(data):
    """Deterministic bounded context for choose_next_action (proposal)."""

    known = {
        str(item.get("target", "")).partition("::")[0]
        for item in data.get("ledger", [])
    }
    recent = list(data.get("last_three_observations", []))
    if recent:
        recent = [_digest(item) for item in recent[:-1]] + [
            _select_items(recent[-1], known)
        ]
    older = list(data.get("older_observation_summaries", []))
    # Full entries for the candidates closest to a decision; the rest stay listed by
    # identity and status so nothing silently disappears from the model's view.
    rank = {"confirmed": 0, "suspected": 1, "rejected": 2}
    ordered = sorted(
        enumerate(data.get("ledger", [])),
        key=lambda pair: (
            rank.get(pair[1].get("status"), 3),
            not pair[1].get("non_lexical_evidence"),
            pair[0],
        ),
    )
    detailed = {
        position
        for position, item in ordered[:LEDGER_DETAIL]
        if item.get("status") != "rejected"
    }
    ledger = []
    for position, item in enumerate(data.get("ledger", [])):
        if position not in detailed:
            ledger.append(
                {
                    "target": item["target"],
                    "status": item.get("status"),
                    "non_lexical": bool(item.get("non_lexical_evidence")),
                }
            )
            continue
        entry = dict(item)
        for key in ("supporting_evidence", "non_lexical_evidence"):
            ids = entry.get(key) or []
            if len(ids) > EVIDENCE_TAIL:
                entry[key] = ids[-EVIDENCE_TAIL:]
                entry[f"{key}_count"] = len(ids)
        ledger.append(entry)
    return {
        **data,
        "ledger": ledger,
        "last_three_observations": recent,
        "older_observation_summaries": older[-OLDER_TAIL:],
        "older_observation_count": len(older),
    }


def bounded_checkpoint_context(data):
    """Checkpoint proposal: strong records first, newest first, per candidate."""

    candidates = []
    for item in data.get("candidates", []):
        evidence = list(item.get("evidence", []))
        ordered = sorted(
            range(len(evidence)), key=lambda i: (not evidence[i].get("strong"), -i)
        )
        keep = sorted(ordered[:EVIDENCE_TAIL])
        candidates.append(
            {
                **item,
                "evidence": [evidence[i] for i in keep],
                "evidence_omitted": len(evidence) - len(keep),
            }
        )
    return {**data, "candidates": candidates}


PROPOSALS = {
    "choose_next_action": bounded_decision_context,
    "decide_candidates": bounded_checkpoint_context,
}


def proposal_prompt(operation: str, prompt: str) -> str:
    match = _BLOCK.search(prompt)
    if not match or operation not in PROPOSALS:
        return prompt
    data = json.loads(match.group(1))
    body = json.dumps(PROPOSALS[operation](data), separators=(",", ":"))
    return prompt[: match.start(1)] + body + prompt[match.end(1) :]


def field_bytes(prompt: str) -> dict[str, int]:
    """Canonical-request bytes contributed by each top-level structured field."""

    match = _BLOCK.search(prompt)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: len(json.dumps(json.dumps({key: value}, separators=(",", ":")))) - 4
        for key, value in data.items()
    }


class SizingProvider:
    """Deterministic synthetic responses; records every estimated request bound."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.position = 0
        self.rows = []
        self.prompts = []

    def _data(self, prompt):
        match = _BLOCK.search(prompt)
        try:
            return json.loads(match.group(1)) if match else {}
        except ValueError:
            return {}

    def create(self, **kwargs):
        schema = kwargs["text"]["format"]["name"]
        operation = SCHEMA_OPERATIONS[schema]
        prompt = kwargs["input"]
        self.prompts.append((operation, prompt))
        compact = compact_prompt(operation, prompt)
        proposed = proposal_prompt(operation, compact)
        self.rows.append(
            {
                "request": len(self.rows) + 1,
                "operation": operation,
                "estimate": bound(kwargs),
                "compact_estimate": bound({**kwargs, "input": compact}),
                "proposal_estimate": bound({**kwargs, "input": proposed}),
                "fields": field_bytes(compact),
            }
        )
        data = self._data(prompt) if operation != "interpret" else {}
        payload = getattr(self, operation)(data)
        return SimpleNamespace(
            model=kwargs["model"],
            output_text=json.dumps(payload),
            status="completed",
            usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
            model_dump=lambda **_: {},
        )

    def interpret(self, data):
        return {
            "summary": "Synthetic request-size stress scenario",
            "change_kinds": ["business_logic"],
            "search_terms": self.scenario["search_terms"],
        }

    def choose_next_action(self, data):
        plan = self.scenario["steps"]
        if self.position >= len(plan):
            if data.get("submission_ready") and self.scenario.get("submit", True):
                return {"tool_name": "submit_report", "arguments": {}, "reason": "done"}
            step = plan[-1] if self.scenario.get("repeat_last", True) else None
            if step is None:
                return {"tool_name": "submit_report", "arguments": {}, "reason": "end"}
        else:
            step = plan[self.position]
        self.position += 1
        tool, arguments = step[0], step[1]
        updates = []
        if self.scenario.get("suspect_touched"):
            updates = self._suspect_touched(data)
        return {
            "tool_name": tool,
            "arguments": arguments,
            "reason": "synthetic stress investigation",
            "ledger_updates": updates,
        }

    def _suspect_touched(self, data):
        """Propose suspected targets from the latest successful observation."""

        recent = [
            item
            for item in data.get("last_three_observations", [])
            if isinstance(item.get("result"), dict) and item["result"].get("ok")
        ]
        if not recent:
            return []
        result = recent[-1]["result"]
        known = {item["target"] for item in data.get("ledger", [])}
        found = []
        payload = result.get("data") or {}
        for key in ("hits", "references", "outline", "neighbors"):
            for item in payload.get(key, []):
                target = item.get("symbol") or item.get("id") or item.get("path")
                if target and target not in known and target not in found:
                    found.append(target)
        return [
            {
                "target": target,
                "status": "suspected",
                "reason": "touched by the latest synthetic observation",
                "evidence_ids": [result["evidence_id"]],
            }
            for target in found[: self.scenario.get("suspect_limit", 4)]
        ]

    def decide_candidates(self, data):
        confirm = set(self.scenario.get("confirm", ()))
        decisions = []
        for item in data.get("candidates", []):
            strong = [e["evidence_id"] for e in item["evidence"] if e["strong"]]
            target = item["target"]
            if (target in confirm or target.partition("::")[0] in confirm) and strong:
                decisions.append(
                    {
                        "target": target,
                        "decision": "confirm",
                        "evidence_ids": strong[:1],
                        "reason": "synthetic confirmation",
                    }
                )
            else:
                decisions.append(
                    {
                        "target": target,
                        "decision": "keep",
                        "reason": "synthetic keep",
                        "missing_evidence": "a caller or test relationship",
                    }
                )
        return {"decisions": decisions}

    def draft_report(self, data):
        return {
            "affected_components": [
                {
                    "target": item["target"],
                    "change_type": "modify",
                    "change_kind": "business_logic",
                    "reason": "synthetic report",
                    "confidence": "low",
                    "evidence": item["evidence_ids"][-1:],
                }
                for item in data.get("ledger", [])
                if item.get("status") == "confirmed"
            ]
        }


def sizing_client(provider):
    client = object.__new__(OpenAILLM)
    client.model = pilot.MODEL
    client.retry_policy = RetryPolicy(allow_request_replay=False)
    client._request_state = ContextVar("pilot_request_size", default=None)
    client._client = SimpleNamespace(
        responses=provider, chat=SimpleNamespace(completions=provider)
    )
    return client


def commit_fixture(source: Path | None, files: dict[str, str] | None, dest: Path):
    if source is not None:
        shutil.copytree(source, dest)
    for name, text in (files or {}).items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    }
    for args in (("init", "-q"), ("add", "."), ("commit", "-qm", "stress fixture")):
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=RIPPLE Stress",
                "-c",
                "user.email=ripple@example.test",
                "-C",
                str(dest),
                *args,
            ],
            env=env,
            check=True,
            capture_output=True,
        )
    return scan_repository(dest)


def run_scenario(name, scenario, index, output: Path):
    provider = SizingProvider(scenario)
    controller = AgentController(
        index,
        sizing_client(provider),
        variant=RIPPLE_V2,
        max_tokens=scenario.get("token_allocation", 110000),
        output_root=output / name,
    )
    with pilot.offline():
        run = controller.run(FeatureRequest(text=scenario["request"]))
    rows = provider.rows
    worst = max(rows, key=lambda row: row["compact_estimate"])
    return {
        "scenario": name,
        "requests": len(rows),
        "stop_reason": run.report.run_stats.stop_reason,
        "tool_calls": run.report.run_stats.tool_calls,
        "candidates": len(controller.ledger.candidates),
        "observations": len(controller.observations),
        "max_estimate": max(row["estimate"] for row in rows),
        "max_compact_estimate": worst["compact_estimate"],
        "compact_headroom": CAP - worst["compact_estimate"],
        "first_request_over_cap": next(
            (row["request"] for row in rows if row["estimate"] > CAP), None
        ),
        "first_compact_request_over_cap": next(
            (row["request"] for row in rows if row["compact_estimate"] > CAP), None
        ),
        "worst_request": {
            key: worst[key] for key in ("request", "operation", "compact_estimate")
        },
        "max_proposal_estimate": max(row["proposal_estimate"] for row in rows),
        "max_proposal_by_operation": {
            op: max(row["proposal_estimate"] for row in rows if row["operation"] == op)
            for op in sorted({row["operation"] for row in rows})
        },
        "max_by_operation": {
            op: max(row["compact_estimate"] for row in rows if row["operation"] == op)
            for op in sorted({row["operation"] for row in rows})
        },
        "worst_fields": sorted(
            worst["fields"].items(), key=lambda item: (-item[1], item[0])
        ),
        "rows": rows,
    }


STOCK = Path(__file__).resolve().parents[2] / "dev/p2b/fixtures/stock"
STOCK_REQUEST = (
    "Show a low-stock warning when free units fall below the configured low "
    "watermark, keeping reserved units out of the available count."
)
STATUS = "warehouse/status.py"


def stock_scenarios():
    task = {
        "request": STOCK_REQUEST,
        "search_terms": ["stock_status", "warning_threshold", "reserved"],
    }
    focused = [
        ["inspect_symbol", {"target": STATUS}],
        ["find_references", {"symbol_id": f"{STATUS}::stock_status"}],
        ["find_tests", {"target": STATUS}],
    ]
    wandering = [
        ["inspect_symbol", {"target": STATUS}],
        ["find_references", {"symbol_id": f"{STATUS}::stock_status"}],
        ["search_code", {"query": "stock status low watermark", "limit": 15}],
        ["find_references", {"symbol_id": STATUS}],  # refused: file path
        ["inspect_symbol", {"target": "warehouse/api.py"}],
        ["get_dependencies", {"path": STATUS, "direction": "imported_by"}],
        ["find_tests", {"target": "warehouse/availability.py"}],
        ["inspect_symbol", {"target": "warehouse/missing.py"}],  # tool failure
        ["search_code", {"query": "free units reserved available", "limit": 15}],
        ["co_changed", {"path": STATUS}],
        ["repo_facts", {"kind": "settings"}],
        ["inspect_symbol", {"target": STATUS}],  # duplicate
        ["find_tests", {"target": STATUS}],
    ]
    return {
        "stock-focused-submit": {**task, "steps": focused, "confirm": [STATUS]},
        "stock-long-horizon-keep": {**task, "steps": wandering, "confirm": []},
        "stock-long-horizon-suspect": {
            **task,
            "steps": wandering,
            "confirm": [],
            "suspect_touched": True,
        },
        "stock-long-horizon-confirm-late": {
            **task,
            "steps": wandering,
            "confirm": [STATUS, "warehouse/availability.py"],
            "suspect_touched": True,
        },
    }


def stress_files(depth=6, modules=12, unicode_text=False):
    """A synthetic repository with long paths, many symbols and dense references."""

    root = "/".join(
        f"segment_{level:02d}_inventory_watermark" for level in range(depth)
    )
    note = "Stock-level señal — ünïcödé ✓ 在庫 警告" if unicode_text else "Stock level"
    files = {}
    for number in range(modules):
        names = [f"stock_watermark_policy_component_{number:02d}_{k}" for k in range(8)]
        body = [f'"""{note} module {number}."""', "from typing import Any", ""]
        if number:
            body.insert(1, f"from {root.replace('/', '.')}.module_00 import *")
        for name in names:
            body += [
                f"def {name}(value: Any, reserved: Any = None) -> Any:",
                f'    """{note}: {name} applies the low stock watermark."""',
                f'    label = "{note} {name}"',
                "    return value",
                "",
            ]
        if number:
            for k in range(8):
                body.append(f"stock_watermark_policy_component_00_{k}(1)")
        files[f"{root}/module_{number:02d}.py"] = "\n".join(body) + "\n"
    files[f"{root}/__init__.py"] = ""
    files["tests/test_watermark.py"] = (
        f"from {root.replace('/', '.')}.module_00 import *\n\n"
        + "".join(
            f"def test_component_{k}():\n"
            f"    assert stock_watermark_policy_component_00_{k}(1) == 1\n\n"
            for k in range(8)
        )
    )
    return root, files


def stress_scenarios(root):
    module = f"{root}/module_00.py"
    symbol = f"{module}::stock_watermark_policy_component_00_0"
    big = [
        ["search_code", {"query": "stock watermark policy component", "limit": 15}],
        ["find_references", {"symbol_id": symbol, "limit": 40}],
        ["inspect_symbol", {"target": module}],
        ["get_dependencies", {"path": module, "direction": "imported_by", "depth": 2}],
        ["find_tests", {"target": module}],
        ["search_code", {"query": "low stock watermark reserved", "limit": 15}],
        ["find_references", {"symbol_id": module, "limit": 40}],  # refused
        ["inspect_symbol", {"target": f"{root}/module_99.py"}],  # failure
    ] + [
        ["inspect_symbol", {"target": f"{root}/module_{n:02d}.py"}]
        for n in range(1, 12)
    ]
    base = {
        "request": "Add a configurable low stock watermark to every policy component.",
        "search_terms": ["stock", "watermark", "policy", "component", "reserved"],
    }
    return {
        "stress-max-window": {**base, "steps": big, "confirm": []},
        "stress-max-candidates": {
            **base,
            "steps": big,
            "confirm": [],
            "suspect_touched": True,
            "suspect_limit": 6,
        },
        "stress-checkpoint-and-report": {
            **base,
            "steps": big,
            "confirm": [module, f"{root}/module_01.py", f"{root}/module_02.py"],
            "suspect_touched": True,
        },
    }


def measure(output: Path):
    results = []
    workspace = output / "fixtures"
    stock = commit_fixture(STOCK, None, workspace / "stock")
    for name, scenario in stock_scenarios().items():
        results.append(run_scenario(name, scenario, stock, output / "runs"))
    for label, unicode_text in (("ascii", False), ("unicode", True)):
        root, files = stress_files(unicode_text=unicode_text)
        index = commit_fixture(None, files, workspace / f"stress-{label}")
        for name, scenario in stress_scenarios(root).items():
            results.append(
                run_scenario(f"{name}-{label}", scenario, index, output / "runs")
            )
    return results


def summary(results):
    return [
        {
            key: row[key]
            for key in (
                "scenario",
                "requests",
                "stop_reason",
                "tool_calls",
                "candidates",
                "max_estimate",
                "max_compact_estimate",
                "compact_headroom",
                "first_request_over_cap",
                "first_compact_request_over_cap",
                "worst_request",
                "worst_fields",
                "max_proposal_estimate",
                "max_proposal_by_operation",
                "max_by_operation",
            )
        }
        for row in results
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="write full per-request rows here")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="ripple-request-size-") as folder:
        results = measure(Path(folder))
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary(results), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
