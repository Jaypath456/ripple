"""Held-out V1 vs V2.1.1 evaluation (run from the repository root).

    python evaluation/v2_heldout/run_heldout.py freeze    # once, before any run
    python evaluation/v2_heldout/run_heldout.py run       # resumable
    python evaluation/v2_heldout/run_heldout.py analyze   # deterministic

Frozen methodology (recorded in config.json and hashed before the first model call):
- both protocols on every task, same model/provider/request/base/budgets;
- 3 repeats per task per system, interleaved V1 then V2.1.1 on the same clone;
- request = masked PR title+body, frozen prefix limit (final-v1 rule);
- a run's ranking = its report's source components in order; set metrics at k equal
  to that count; abstention scores zero; repeats averaged within task first;
- a provider-failed run gets exactly one retry of the same slot; both are kept;
- repository-cluster paired bootstrap, 1000 samples, seed 1729.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ripple import agent as agent_module
from ripple.agent import AGENT_VARIANTS, RIPPLE_V2, AgentController
from ripple.agent_models import (
    FULL_REPORT_CONFIG_VERSION,
    FULL_REPORT_V2_CONFIG_VERSION,
    FeatureRequest,
)
from ripple.evaluation import (
    load_tasks,
    prepare_evaluation_request,
    prepare_repository,
    score_ranking,
)
from ripple.evaluation_results import cluster_bootstrap_values, paired_cluster_bootstrap
from ripple.llm import LLMError, OpenAILLM
from ripple.scanner import scan_repository

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
CONFIG = HERE / "config.json"
RAW = HERE / "raw"
WORKSPACE = ROOT / ".ripple" / "evaluation" / "v2-heldout"
FROZEN_COMMIT = "f381d1c7c6f55e504cee3f9e6beb425d2387ec0d"
SYSTEMS = {"V1": AGENT_VARIANTS["RIPPLE"], "V2.1.1": RIPPLE_V2}
REPEATS = 3
BOOTSTRAP = {"samples": 1000, "seed": 1729, "cluster": "repository"}
METRICS = (
    "precision",
    "recall",
    "f1",
    "recall_at_5",
    "recall_at_10",
    "mrr",
    "false_positives",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def freeze() -> int:
    if CONFIG.exists():
        print("config.json already frozen; refusing to overwrite", file=sys.stderr)
        return 1
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    src_tree = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", f"{FROZEN_COMMIT}:src"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    llm = OpenAILLM.from_env()
    import os

    payload = {
        "evaluation": "v2-heldout",
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "frozen_commit": FROZEN_COMMIT,
        "head_at_freeze": head,
        "src_tree_sha1": src_tree,
        "manifest_sha256": _sha(MANIFEST),
        "task_count": len(load_tasks(MANIFEST).tasks),
        "systems": {
            "V1": {
                "variant": "AGENT_VARIANTS['RIPPLE']",
                "protocol": "v1",
                "config_version": FULL_REPORT_CONFIG_VERSION,
            },
            "V2.1.1": {
                "variant": "RIPPLE_V2",
                "protocol": "v2",
                "config_version": FULL_REPORT_V2_CONFIG_VERSION,
            },
        },
        "provider": {
            "model": llm.model,
            "base_url_host": (os.environ.get("RIPPLE_LLM_BASE_URL") or "default")
            .split("//")[-1]
            .split("/")[0],
            "api_key": "excluded",
            "seed_control": False,
            "retry_policy": "OpenAILLM bounded retries per call; one run-level "
            "retry per provider-failed slot",
        },
        "repeats_per_task_per_system": REPEATS,
        "order": "per task: repeat 1..3, V1 then V2.1.1, same clone",
        "budgets": {
            "max_tool_calls": agent_module.MAX_TOOL_CALLS,
            "max_no_progress": agent_module.MAX_NO_PROGRESS,
            "max_candidates": agent_module.MAX_CANDIDATES,
            "checkpoint_interval": agent_module.CHECKPOINT_INTERVAL,
            "max_stall": agent_module.MAX_STALL,
            "max_checkpoints": agent_module.MAX_CHECKPOINTS,
        },
        "history_depth": 501,
        "request": "masked PR title+body, prepare_evaluation_request prefix limit",
        "metrics": list(METRICS)
        + ["abstention_rate", "completed/partial/failed rates", "operational costs"],
        "scoring": "report source components in order; k = their count; abstention "
        "scores zero; repeats averaged within task before aggregation",
        "bootstrap": BOOTSTRAP,
        "python": platform.python_version(),
    }
    payload["config_sha256"] = _canonical_hash(payload)
    CONFIG.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"frozen config {payload['config_sha256']}")
    return 0


def _check_frozen() -> dict:
    config = json.loads(CONFIG.read_text())
    expected = config.pop("config_sha256")
    if _canonical_hash(config) != expected:
        raise SystemExit("config.json changed after freezing")
    if _sha(MANIFEST) != config["manifest_sha256"]:
        raise SystemExit("manifest.json changed after freezing")
    tree = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD:src"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain", "src"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if tree != config["src_tree_sha1"] or dirty:
        raise SystemExit("src/ differs from the frozen commit")
    return config | {"config_sha256": expected}


def _unsupported(report, controller, index) -> list[str]:
    confirmed = {item.target for item in controller.ledger.confirmed()}
    valid = {item.path.as_posix() for item in index.files} | {
        item.id for item in index.symbols
    }
    return [
        item.target
        for item in report.affected_components
        if not item.target.endswith("<proposed migration>")
        and (
            item.target not in confirmed
            or item.target not in valid
            or not item.evidence
            or any(e not in controller.ledger.evidence for e in item.evidence)
        )
    ]


def _run_one(task, system, repeat, attempt, index, request, llm, out: Path) -> dict:
    record = {
        "task_id": task.id,
        "repository": task.repository,
        "system": system,
        "repeat": repeat,
        "attempt": attempt,
    }
    try:
        controller = AgentController(
            index, llm, output_root=out, variant=SYSTEMS[system]
        )
        run = controller.run(FeatureRequest(text=request))
    except Exception as error:  # noqa: BLE001 - recorded, never silent
        return record | {
            "status": "failed",
            "provider_error": f"{type(error).__name__}: {error}"[:300]
            if isinstance(error, LLMError)
            else None,
            "harness_error": None
            if isinstance(error, LLMError)
            else f"{type(error).__name__}: {error}"[:300],
        }
    report = run.report
    stats = report.run_stats
    trace = [json.loads(line) for line in run.trace_path.read_text().splitlines()]
    sources = {item.path.as_posix() for item in index.files if not item.is_test}
    ranking = list(
        dict.fromkeys(
            item.target.partition("::")[0]
            for item in report.affected_components
            if item.target.partition("::")[0] in sources
        )
    )
    gold = [path.as_posix() for path in task.gold_files.source_python]
    metrics = score_ranking(ranking, gold, prediction_k=len(ranking))
    states = Counter(item.status for item in controller.ledger.candidates.values())
    provider = next(
        (str(e.get("error"))[:300] for e in trace if e["event"] == "provider_error"),
        None,
    )
    return record | {
        "status": report.status,
        "stop_reason": stats.stop_reason,
        "config_version": stats.config_version,
        "provider_error": provider,
        "harness_error": None,
        "predicted_source_files": ranking,
        "gold_source_files": gold,
        **{name: getattr(metrics, name) for name in METRICS},
        "confirmed": states["confirmed"],
        "suspected": states["suspected"],
        "rejected": states["rejected"],
        "tool_calls": stats.tool_calls,
        "duplicate_calls": stats.duplicate_calls,
        "model_calls": stats.llm_calls,
        "input_tokens": stats.input_tokens,
        "output_tokens": stats.output_tokens,
        "total_tokens": stats.total_tokens,
        "runtime_seconds": round(stats.runtime_seconds, 2),
        "decision_checkpoints": stats.decision_checkpoints,
        "report_repairs": stats.report_repairs,
        "invalid_tool_targets": sum(
            event["event"] == "tool_target_rejected"
            or (
                event["event"] == "validation_error"
                and event.get("operation") == "tool"
            )
            or (
                event["event"] == "tool_result"
                and not event.get("ok")
                and event.get("tool") == "find_references"
            )
            for event in trace
        ),
        "validator_drops": len(report.dropped_claims),
        "dropped_claims": list(report.dropped_claims),
        "unsupported_accepted_claims": _unsupported(report, controller, index),
        "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def run() -> int:
    config = _check_frozen()
    llm = OpenAILLM.from_env()
    if llm.model != config["provider"]["model"]:
        raise SystemExit("configured model differs from the frozen config")
    RAW.mkdir(exist_ok=True)
    tasks = load_tasks(MANIFEST).tasks
    for number, task in enumerate(tasks, start=1):
        slots = [(r, s) for r in range(1, REPEATS + 1) for s in SYSTEMS]

        def path(repeat, system, attempt, task_id=task.id):
            return RAW / f"{task_id}__{system}__r{repeat}__a{attempt}.json"

        def pending(repeat, system):
            first = path(repeat, system, 1)
            if not first.exists():
                return 1
            if (
                json.loads(first.read_text()).get("provider_error")
                and not path(repeat, system, 2).exists()
            ):
                return 2  # the single predetermined retry
            return None

        todo = [(r, s, pending(r, s)) for r, s in slots if pending(r, s)]
        if not todo:
            continue
        workspace = WORKSPACE / "repos"
        repo, checks = prepare_repository(task, workspace, history_depth=501)
        try:
            index = scan_repository(repo)
            request = prepare_evaluation_request(task.masked_request).text
            for repeat, system, attempt in todo:
                record = _run_one(
                    task,
                    system,
                    repeat,
                    attempt,
                    index,
                    request,
                    llm,
                    WORKSPACE / "artifacts" / task.id,
                )
                record["leak_checks"] = list(checks)
                record["request_sha256"] = hashlib.sha256(request.encode()).hexdigest()
                path(repeat, system, attempt).write_text(json.dumps(record, indent=2))
                if record["provider_error"] and attempt == 1:
                    retry = _run_one(
                        task,
                        system,
                        repeat,
                        2,
                        index,
                        request,
                        llm,
                        WORKSPACE / "artifacts" / task.id,
                    )
                    retry["leak_checks"] = list(checks)
                    retry["request_sha256"] = record["request_sha256"]
                    path(repeat, system, 2).write_text(json.dumps(retry, indent=2))
                    record = retry
                print(
                    f"[{number:>2}/{len(tasks)}] {task.id:<38} r{repeat} {system:<6} "
                    f"{record['status']:<10} {record.get('stop_reason', '-'):<18} "
                    f"P={record.get('precision', 0):.2f} R={record.get('recall', 0):.2f}"
                    + (" PROVIDER" if record["provider_error"] else ""),
                    flush=True,
                )
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    return 0


# --------------------------------------------------------------------------- analysis


def _final_records() -> tuple[list[dict], list[dict]]:
    """(scored slot outcomes, all attempts). A slot's outcome is its last attempt."""

    attempts = [json.loads(path.read_text()) for path in sorted(RAW.glob("*.json"))]
    slots: dict[tuple, dict] = {}
    for record in sorted(attempts, key=lambda r: r["attempt"]):
        slots[(record["task_id"], record["system"], record["repeat"])] = record
    return list(slots.values()), attempts


def _task_rows(records: list[dict], *, behaviour_only: bool) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for record in records:
        if behaviour_only and (record["provider_error"] or record["harness_error"]):
            continue
        grouped[(record["task_id"], record["system"])].append(record)
    rows = []
    for (task_id, system), items in sorted(grouped.items()):
        row = {
            "task_id": task_id,
            "system": system,
            "repository": items[0]["repository"],
            "runs": len(items),
        }
        for name in METRICS:
            row[name] = fmean(float(item.get(name) or 0.0) for item in items)
        for status in ("completed", "partial", "abstained", "failed"):
            row[f"{status}_rate"] = fmean(item["status"] == status for item in items)
        rows.append(row)
    return rows


def _aggregate(rows: list[dict], system: str) -> dict:
    chosen = [row for row in rows if row["system"] == system]
    keys = (*METRICS, "completed_rate", "partial_rate", "abstained_rate", "failed_rate")
    return {"tasks": len(chosen)} | {
        key: fmean(row[key] for row in chosen) if chosen else None for key in keys
    }


def _operations(records: list[dict], system: str) -> dict:
    chosen = [r for r in records if r["system"] == system and not r["harness_error"]]
    keys = (
        "tool_calls",
        "model_calls",
        "duplicate_calls",
        "invalid_tool_targets",
        "total_tokens",
        "runtime_seconds",
        "decision_checkpoints",
        "report_repairs",
        "validator_drops",
    )
    output = {}
    for key in keys:
        values = [r[key] for r in chosen if r.get(key) is not None]
        output[f"mean_{key}"] = fmean(values) if values else None
        output[f"total_{key}"] = sum(values) if values else 0
    return output


def analyze() -> int:
    config = _check_frozen()
    records, attempts = _final_records()
    tasks = load_tasks(MANIFEST).tasks
    expected = len(tasks) * len(SYSTEMS) * REPEATS
    summary: dict = {
        "label": "v2-heldout: new untouched held-out set; V1 vs V2.1.1 same-run",
        "config_sha256": config["config_sha256"],
        "manifest_sha256": config["manifest_sha256"],
        "frozen_commit": config["frozen_commit"],
        "model": config["provider"]["model"],
        "tasks": len(tasks),
        "repositories": len({task.repository for task in tasks}),
        "expected_slots": expected,
        "scored_slots": len(records),
        "attempts": len(attempts),
        "provider_failed_attempts": sum(bool(r["provider_error"]) for r in attempts),
        "retries": sum(r["attempt"] == 2 for r in attempts),
        "slots_failed_after_retry": sum(
            bool(r["provider_error"] or r["harness_error"]) for r in records
        ),
        "harness_errors": sorted(
            {r["harness_error"] for r in attempts if r["harness_error"]}
        ),
    }
    for label, behaviour_only in (("all_runs", False), ("behaviour_only", True)):
        rows = _task_rows(records, behaviour_only=behaviour_only)
        block: dict = {system: _aggregate(rows, system) for system in SYSTEMS}
        block["deltas_v2_minus_v1"] = {
            key: (
                block["V2.1.1"][key] - block["V1"][key]
                if block["V1"][key] is not None and block["V2.1.1"][key] is not None
                else None
            )
            for key in block["V1"]
            if key != "tasks"
        }
        block["relative_deltas"] = {
            key: (value / block["V1"][key] if block["V1"][key] else None)
            for key, value in block["deltas_v2_minus_v1"].items()
            if value is not None
        }
        paired_rows = [
            row
            for row in rows
            if {r["system"] for r in rows if r["task_id"] == row["task_id"]}
            >= set(SYSTEMS)
        ]
        block["paired_bootstrap_v2_minus_v1"] = {
            metric: paired_cluster_bootstrap(
                paired_rows,
                "V2.1.1",
                "V1",
                metric=metric,
                samples=BOOTSTRAP["samples"],
                seed=BOOTSTRAP["seed"],
            )
            for metric in (*METRICS, "abstained_rate", "completed_rate")
        }
        for value in block["paired_bootstrap_v2_minus_v1"].values():
            value["ci_excludes_zero"] = value["ci_low"] > 0 or value["ci_high"] < 0
        block["intervals"] = {
            system: {
                metric: cluster_bootstrap_values(
                    [row for row in rows if row["system"] == system],
                    lambda row, key=metric: float(row[key]),
                    samples=BOOTSTRAP["samples"],
                    seed=BOOTSTRAP["seed"],
                )
                for metric in ("precision", "recall", "f1", "abstained_rate")
            }
            for system in SYSTEMS
        }
        summary[label] = block
        if label == "all_runs":
            per_task = rows
    summary["operations"] = {system: _operations(records, system) for system in SYSTEMS}
    summary["safety"] = {
        system: {
            "unsupported_accepted_claims": sum(
                len(r.get("unsupported_accepted_claims", []))
                for r in records
                if r["system"] == system
            ),
            "runs_with_unsupported_claims": sum(
                bool(r.get("unsupported_accepted_claims"))
                for r in records
                if r["system"] == system
            ),
            "validator_drops": sum(
                r.get("validator_drops", 0) for r in records if r["system"] == system
            ),
            "new_file_classification_drops": sum(
                "invalid migration new-file dropped" in claim
                for r in records
                if r["system"] == system
                for claim in r.get("dropped_claims", [])
            ),
            "runs_with_report_repair": sum(
                bool(r.get("report_repairs")) for r in records if r["system"] == system
            ),
        }
        for system in SYSTEMS
    }
    summary["status_mix"] = {
        system: dict(
            sorted(
                Counter(r["status"] for r in records if r["system"] == system).items()
            )
        )
        for system in SYSTEMS
    }
    (HERE / "results.json").write_text(
        json.dumps({"per_task": per_task, "runs": records}, indent=2) + "\n"
    )
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("scored_slots", "attempts")}, indent=2))
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    sys.exit({"freeze": freeze, "run": run, "analyze": analyze}[command]())
