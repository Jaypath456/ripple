"""Pre-run leak audit for the final-v1 manifest (run from the repository root).

Checks every task's evaluation checkout (exact base, clean, no future refs, no
gold-only paths, no gold paths in the masked request), then reconstructs the
official diff privately to confirm no symbol that exists only in the future diff
is visible in the base checkout or the masked request (informational). Models never see any of
the gold material used here.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from ripple.evaluation import (
    EvaluationError,
    load_tasks,
    prepare_repository,
    sanitize_request,
)
from ripple.final_data import _symbol_leaks, _task_symbol_gold
from ripple.final_evaluation import atomic_json

MANIFEST = Path("evaluation/data/final_fea_tasks.json")
PROVENANCE = Path("evaluation/data/final_fea_provenance.json")
DIFFS = Path("evaluation/gold/diffs")
WORKSPACE = Path(".ripple/evaluation/final-v1")
OUTPUT = Path("evaluation/audits/pre_run_leak_audit.json")


def main() -> int:
    tasks = load_tasks(MANIFEST).tasks
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    scratch = WORKSPACE / "_gold_scratch"
    audit: dict[str, dict] = {}
    for task in tasks:
        entry: dict = {"status": "passed", "problems": []}
        audit[task.id] = entry
        if task.masked_request != sanitize_request(task.original_request):
            entry["problems"].append("masked request is not deterministic")
        try:
            repo, checks = prepare_repository(task, WORKSPACE, history_depth=501)
            entry["checkout_checks"] = list(checks)
        except EvaluationError as error:
            entry["problems"].append(f"checkout: {error}")
            repo = None
        try:
            gold = _task_symbol_gold(task, DIFFS, provenance, scratch)
            names = gold["gold_only_symbol_names"]
            entry["gold_only_symbols_checked"] = len(names)
            entry["unmappable_gold_hunks"] = gold["unmappable_hunks"]
            if repo is not None:
                matches = _symbol_leaks(task, names, repo)
                entry["symbol_name_matches"] = matches
                entry["strict_symbol_rule_failure"] = bool(matches)
        except Exception as error:  # noqa: BLE001 - recorded as an audit failure
            entry["problems"].append(f"gold reconstruction: {error}")
        if entry["problems"]:
            entry["status"] = "failed"
        print(task.id, entry["status"], entry["problems"], flush=True)
    shutil.rmtree(scratch, ignore_errors=True)
    failed = sorted(key for key, value in audit.items() if value["status"] == "failed")
    strict = sorted(
        key for key, value in audit.items() if value.get("strict_symbol_rule_failure")
    )
    atomic_json(
        OUTPUT,
        {
            "tasks": len(tasks),
            "structural_failures": failed,
            "strict_symbol_rule_failures": strict,
            "note": (
                "Symbol-name matches are informational: the base checkout is verified "
                "exact, clean, and free of future refs, and a request may legitimately "
                "name the feature to add. Drop the strict failures for sensitivity."
            ),
            "audit": audit,
        },
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
