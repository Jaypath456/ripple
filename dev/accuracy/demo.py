"""Offline walkthrough of one RIPPLE run per case, with no provider access.

Runs three synthetic development cases (single-file, multi-file, abstain) through the
real controller, tools, ledger, validator and report, using the deterministic
simulated model from ``harness.py``. Prints each stage from the run trace and
compares the final prediction with the task's gold files.

Usage: python dev/accuracy/demo.py [--config E-combined] [--policy observed]
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness
from fixtures import REPOSITORIES, TASKS

from ripple.tools import validate_tool_arguments

CASES = (
    "P3-DEV-01-LOYALTY-DISCOUNT",
    "P3-DEV-02-TAX-ON-SHIPPING",
    "P3-DEV-09-IAM-ROTATION",
)


def describe(task, config, policy, workdir: Path) -> list[str]:
    index = harness.build_repository(
        REPOSITORIES[task["repository"]], workdir / "repo" / task["task_id"]
    )
    row = harness.run_task(
        task, config, policy, index, workdir / "run" / task["task_id"]
    )
    trace_path = next((workdir / "run" / task["task_id"] / "runs").glob("*.jsonl"))
    trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
    lines = [
        f"== {task['task_id']} ({task['kind']})",
        f"1. Request: {task['request']}",
        (
            f"2. Indexed: {len(index.files)} Python files, {len(index.symbols)} symbols, "
            f"{len(index.test_mappings)} test mappings at {index.commit[:12]}"
        ),
        f"3. Seeded candidates (files): {', '.join(row['seed_files']) or 'none'}",
        "4. Tool calls:",
    ]
    proof, model_choice, phase, expand_calls = {}, None, "explore", 0
    for event in trace:
        kind = event["event"]
        if kind == "decision":
            try:
                canonical = validate_tool_arguments(
                    event["decision"]["tool_name"], event["decision"]["arguments"]
                )
            except ValueError:
                canonical = None
            model_choice = json.dumps(canonical, sort_keys=True)
        elif kind == "proof_step_selected":
            proof[json.dumps(event["step"]["arguments"], sort_keys=True)] = event[
                "reason_code"
            ]
        elif kind == "expand_started":
            phase = "expand"
        elif kind == "tool_result" and not event.get("duplicate"):
            if phase == "expand":
                expand_calls += 1
                continue
            key = json.dumps(event["arguments"], sort_keys=True)
            if key in proof:
                chooser = f"controller: proof policy ({proof.pop(key)})"
            elif event["evidence_id"] == "e1":
                chooser = "controller: seed search"
            elif key == model_choice:
                chooser = "model"
            else:
                chooser = "controller: structural expansion"
            arguments = ", ".join(f"{k}={v}" for k, v in event["arguments"].items())
            lines.append(
                f"   {event['evidence_id']:>4} {event['tool']}({arguments}) "
                f"ok={event['ok']} <- {chooser}"
            )
    lines.append(
        f"   + {expand_calls} deterministic report-Expand calls after the loop "
        "(regression areas, tests; not candidate evidence for decisions)"
    )
    lines.append("5-6. Checkpoint decisions (Python-verified):")
    decisions = [e for e in trace if e["event"] == "candidate_decision"]
    for event in decisions:
        decision = event["decision"]
        verdict = (
            "accepted"
            if event["accepted"]
            else (
                f"refused: {event['reason_code']}"
                if event["reason_code"]
                else "no change"
            )
        )
        lines.append(
            f"   {decision['decision']:>7} {decision['target']} "
            f"cites {decision.get('evidence_ids') or []} -> {verdict}"
        )
    if not decisions:
        lines.append("   none (no candidate had non-lexical evidence to decide)")
    lines += [
        (
            f"7. Report: status={row['status']} stop={row['stop_reason']} "
            f"predicted={row['predicted_files'] or 'nothing (abstained)'}"
        ),
        (
            f"8. Gold: {task['gold'] or 'none (abstention expected)'} -> "
            f"TP={row['true_positives']} FP={row['false_positives']} "
            f"FN={row['false_negatives']}"
        ),
        "",
    ]
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", choices=sorted(harness.CONFIGS), default="E-combined"
    )
    parser.add_argument("--policy", choices=harness.POLICIES, default="observed")
    args = parser.parse_args(argv)
    for key in [k for k in os.environ if "API_KEY" in k.upper()]:
        del os.environ[key]
    print(
        f"RIPPLE offline demo: config={args.config}, simulated policy={args.policy}. "
        "No provider is contacted; the model is a deterministic simulation.\n"
    )
    tasks = {task["task_id"]: task for task in TASKS}
    with tempfile.TemporaryDirectory(prefix="ripple-p3-demo-") as folder:
        for case in CASES:
            print(
                "\n".join(describe(tasks[case], args.config, args.policy, Path(folder)))
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
