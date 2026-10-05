"""Post-prediction steps for final-v1 (run from the repository root).

Derives symbol gold from the official diffs (refuses until every checkpoint exists),
records the post-run symbol-name audit, and writes the 15-task adjudication artifact
for the human. An artifact that already contains labels is never overwritten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ripple.evaluation import load_tasks
from ripple.evaluation_results import (
    _load_runs,
    _score_runs,
    build_adjudication_template,
)
from ripple.final_data import audit_gold_symbol_leaks, derive_symbol_gold

MANIFEST = Path("evaluation/data/final_fea_tasks.json")
RAW = Path("evaluation/raw/final-v1")
WORKSPACE = Path(".ripple/evaluation/final-v1")
SYMBOLS = Path("evaluation/gold/final_symbols.json")
AUDIT = Path("evaluation/audits/post_run_symbol_audit.json")
ADJUDICATION = Path("evaluation/adjudication/adjudication_template.json")
CONTEXT_LINES = 60


def _evidence(report_path: Path | None) -> dict[str, str]:
    if report_path is None or not report_path.is_file():
        return {}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    grouped: dict[str, list[str]] = {}
    for item in report.get("affected_components", []):
        path = item["target"].partition("::")[0]
        grouped.setdefault(path, []).append(
            f"{item['target']} ({item['confidence']} confidence): {item['reason']} "
            f"[evidence {', '.join(item['evidence'])}]"
        )
    return {path: " | ".join(values) for path, values in grouped.items()}


def _context(task_id: str, path: str) -> str:
    source = WORKSPACE / task_id / path
    if not source.is_file():
        return "File not present in the base checkout."
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[:CONTEXT_LINES])


def main() -> int:
    if ADJUDICATION.exists() and any(
        item.get("label") for item in json.loads(ADJUDICATION.read_text())["entries"]
    ):
        print("refusing to overwrite an adjudication file that contains labels")
        return 1
    tasks = load_tasks(MANIFEST).tasks
    gold = derive_symbol_gold(
        tasks, raw_root=RAW, workspace=WORKSPACE / "_gold", output_path=SYMBOLS
    )
    audit_gold_symbol_leaks(
        tasks, gold, prediction_workspace=WORKSPACE, output_path=AUDIT
    )
    runs = _load_runs(RAW)
    rows = _score_runs(runs, {task.id: task for task in tasks}, gold)
    reports = {
        (run.task_id, run.requested_seed): run.report_path
        for run in runs
        if run.system == "RIPPLE"
    }
    for row in rows:
        if row["system"] != "RIPPLE":
            continue
        row["prediction_evidence"] = _evidence(
            reports.get((row["task_id"], row["requested_seed"]))
        )
        row["base_code_context"] = {
            path: _context(row["task_id"], path) for path in row["false_positive_paths"]
        }
    payload = build_adjudication_template(tasks, rows, ADJUDICATION)
    print(
        json.dumps(
            {
                "selected_tasks": len(payload["selected_task_ids"]),
                "entries_to_label": len(payload["entries"]),
                "path": str(ADJUDICATION),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
