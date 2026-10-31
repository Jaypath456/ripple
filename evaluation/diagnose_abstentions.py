"""Audit exact final checkpoint traces without extraction, inference, or gold edits.

Run: python evaluation/diagnose_abstentions.py --output docs/evidence/abstentions.json
Only checkpoint-referenced archive members count; superseded retries are excluded.
"""

import argparse
import hashlib
import json
import tarfile
from collections import Counter
from pathlib import Path
from statistics import fmean

from ripple.evaluation import load_tasks, score_ranking
from ripple.evaluation_results import average_seeds


def diagnose(root: Path) -> dict:
    archive_path = root / "evaluation/raw/final-v1-reports-and-traces.tar.gz"
    raw = root / "evaluation/raw/final-v1"
    tasks = {
        t.id: t for t in load_tasks(root / "evaluation/data/final_fea_tasks.json").tasks
    }
    cases = []
    totals, stops, submissions, validation = Counter(), Counter(), Counter(), Counter()
    metric_rows = []
    with tarfile.open(archive_path) as archive:
        members = {m.name: m for m in archive if m.isfile()}
        for path in sorted(raw.glob("*__RIPPLE__*.json")):
            row = json.loads(path.read_text())
            trace_name = "_artifacts/" + row["trace_path"].split("/_artifacts/", 1)[1]
            report_name = "_artifacts/" + row["report_path"].split("/_artifacts/", 1)[1]
            events = [
                json.loads(line) for line in archive.extractfile(members[trace_name])
            ]
            report = json.load(archive.extractfile(members[report_name]))
            decisions = [e["decision"] for e in events if e["event"] == "decision"]
            updates = [u for d in decisions for u in d.get("ledger_updates", [])]
            accepted = [
                e for e in events if e["event"] == "ledger_update" and e.get("accepted")
            ]
            finish = next(e for e in events if e["event"] == "run_finished")
            rejected = [
                e
                for e in events
                if e["event"] == "ledger_update" and e.get("accepted") is False
            ]
            local_validation = Counter(
                e["operation"] for e in events if e["event"] == "validation_error"
            )
            validation.update(local_validation)
            stops[finish["stop_reason"]] += 1
            submissions.update(
                e["reason"] for e in events if e["event"] == "submit_rejected"
            )
            tools = [
                e
                for e in events
                if e["event"] == "tool_result" and not e.get("duplicate")
            ]
            known_ids = {e["evidence_id"] for e in tools if e.get("ok")}
            components = report["affected_components"]
            valid_components = sum(
                all(i in known_ids for i in c["evidence"]) for c in components
            )
            totals.update(
                {
                    "runs": 1,
                    "runs_without_ledger_updates": int(not updates),
                    "runs_without_accepted_updates": int(not accepted),
                    "proposed_updates": len(updates),
                    "accepted_updates": len(accepted),
                    "rejected_updates": len(rejected),
                    "decisions": len(decisions),
                    "components": len(components),
                    "components_with_successful_evidence_ids": valid_components,
                    "requests_truncated": int(row["request_truncated"]),
                }
            )
            # Historical traces do not contain tool payloads. Provenance is checkable;
            # semantic support and retrieval recall cannot be recovered from these IDs.
            taxonomy = []
            if not updates:
                taxonomy.append("no_ledger_updates")
            elif not accepted:
                taxonomy.append("all_ledger_updates_rejected")
            if local_validation:
                taxonomy.append("schema_or_tool_argument_errors")
            if finish["stop_reason"] == "submitted" and not components:
                taxonomy.append("submitted_but_no_validated_components")
            task = tasks[row["task_id"]]
            metrics = score_ranking(
                row["source_ranking"],
                task.gold_files.source_python,
                prediction_k=len(row["source_ranking"]),
            )
            metric_rows.append(
                {
                    "task_id": task.id,
                    "repository": task.repository,
                    "system": "RIPPLE",
                    **metrics.model_dump(),
                }
            )
            cases.append(
                {
                    "run_id": row["run_id"],
                    "checkpoint_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "trace_member": trace_name,
                    "status": row["status"],
                    "stop_reason": finish["stop_reason"],
                    "decisions": len(decisions),
                    "proposed_updates": len(updates),
                    "accepted_updates": len(accepted),
                    "validation_errors": dict(local_validation),
                    "taxonomy": taxonomy,
                    "components": len(components),
                    "dropped_claims": report["dropped_claims"],
                    "metrics": metrics.model_dump(),
                    "model_calls": row["model_calls"],
                    "total_tokens": row["total_tokens"],
                    "runtime_seconds": row["runtime_seconds"],
                    "request_truncated": row["request_truncated"],
                    "rejected_update_samples": [
                        {
                            "target": e["update"]["target"],
                            "status": e["update"]["status"],
                            "evidence_ids": e["update"]["evidence_ids"][:8],
                            "evidence_id_count": len(e["update"]["evidence_ids"]),
                        }
                        for e in rejected[:2]
                    ],
                }
            )
    averaged = average_seeds(metric_rows)
    return {
        "schema_version": 1,
        "scope": "Frozen final-v1 only; diagnostic inspection, no new inference",
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "totals": dict(totals),
        "stop_reasons": dict(stops),
        "submit_rejection_reasons": dict(submissions),
        "validation_errors": dict(validation),
        "metrics_seed_then_task": {
            k: fmean(r[k] for r in averaged) for k in ("precision", "recall", "f1")
        },
        "cost": {
            "model_calls": sum(c["model_calls"] for c in cases),
            "total_tokens": sum(c["total_tokens"] for c in cases),
            "mean_runtime_seconds": fmean(c["runtime_seconds"] for c in cases),
            "new_model_calls": 0,
            "new_inference_cost": 0,
        },
        "evidence_id_provenance_rate": totals["components_with_successful_evidence_ids"]
        / totals["components"]
        if totals["components"]
        else None,
        "limitations": [
            "Historical traces omit tool payloads; semantic evidence validity and retrieval recall are unmeasured.",
            "Rejection reasons were not recorded historically; invalid targets vs stale/unknown IDs cannot always be separated.",
            "Prefix-truncated requests and historical PR gold do not establish that every gold target is inferable.",
            "Current full-report-v2 has no live benchmark measurement; frozen metrics are not current-model results.",
        ],
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))


if __name__ == "__main__":
    main()
