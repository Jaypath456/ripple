"""Build the held-out final-v1 FEA-Bench manifest (run from the repository root).

Inputs are the pinned official FEA-Bench table and Lite list plus public PR
`.diff` files; no model is involved and no target code is executed.

    python evaluation/build_final_manifest.py --scratch DIR

DIR must hold lite_rows.json (Lite rows from the pinned parquet) and diffs/*.diff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from ripple.evaluation import (
    EvaluationError,
    EvaluationManifest,
    build_fea_bench_task,
    classify_gold_files,
    extract_changed_files,
    load_tasks,
)
from ripple.final_data import select_final_tasks
from ripple.final_evaluation import atomic_json, validate_final_selection

SOURCE = (
    "https://huggingface.co/datasets/microsoft/FEA-Bench/tree/"
    "55ee2f78126a3ecdeac6f595fa1ba6ae5c600bad"
)


def _api(repo: str, number: int) -> dict:
    url = f"https://api.github.com/repos/{repo}/pulls/{number}"
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ripple-eval"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in {403, 429} and attempt < 5:
                time.sleep(120)
                continue
            raise
    raise EvaluationError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/data/final_fea_tasks.json")
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=Path("evaluation/data/final_fea_provenance.json"),
    )
    args = parser.parse_args()
    development = {
        task.id
        for name in ("dev_tasks", "mvp_tasks")
        for task in load_tasks(Path("evaluation/data") / f"{name}.json").tasks
    }
    rows = {
        row["instance_id"]: row
        for row in json.loads((args.scratch / "lite_rows.json").read_text())
    }
    screened = Counter()
    stubs = []
    diffs: dict[str, str] = {}
    for task_id, row in sorted(rows.items()):
        if task_id in development:
            screened["development_excluded"] += 1
            continue
        diff = (args.scratch / "diffs" / f"{task_id}.diff").read_text(errors="replace")
        diffs[task_id] = diff
        count = len(classify_gold_files(extract_changed_files(diff)).source_python)
        stratum = (
            "1"
            if count < 2
            else "2-4"
            if count <= 4
            else "5-9"
            if count <= 9
            else "10-20"
            if count <= 20
            else ">20"
        )
        screened[f"source_files_{stratum}"] += 1
        if stratum in {"2-4", "5-9", "10-20"}:
            stubs.append(
                build_fea_bench_task(
                    row,
                    {
                        "number": int(row["pull_number"]),
                        "merged": True,
                        "base": {"sha": row["base_commit"]},
                        "head": {"sha": "0" * 40},
                        "title": "stub",
                    },
                    diff,
                    benchmark_source=SOURCE,
                )
            )
    excluded: dict[str, str] = {}
    chosen: tuple = ()
    final = []
    while True:
        pool = tuple(item for item in stubs if item.id not in excluded)
        chosen = select_final_tasks(pool, development_ids=development)
        final, failed = [], False
        for stub in chosen:
            row = rows[stub.id]
            try:
                pr = _api(row["repo"], int(row["pull_number"]))
                if not (
                    str(pr.get("title") or "").strip()
                    or str(pr.get("body") or "").strip()
                ):
                    raise EvaluationError("blank title and body")
                final.append(
                    (
                        build_fea_bench_task(
                            row, pr, diffs[stub.id], benchmark_source=SOURCE
                        ),
                        pr["html_url"],
                    )
                )
            except (EvaluationError, urllib.error.HTTPError, ValueError) as error:
                excluded[stub.id] = str(error)[:200]
                failed = True
        if not failed:
            break
    tasks = tuple(item for item, _ in final)
    composition = validate_final_selection(tasks, development)
    manifest = EvaluationManifest(
        benchmark_source=SOURCE,
        selection_method=(
            "final-v1: official Lite tasks; Phase 3/4/5 development tasks excluded; "
            "merged PR with usable title/body; 2-20 primary Python source files; "
            "deterministic round-robin over 2-4/5-9/10-20 strata sorted by "
            "(repository, id); at most five per repository; selection never inspected "
            "request quality or any model output"
        ),
        tasks=tasks,
    )
    atomic_json(args.output, manifest.model_dump(mode="json"))
    atomic_json(
        args.provenance,
        {
            "official_lite_candidates": len(rows),
            "screening": dict(sorted(screened.items())),
            "eligible_before_repository_cap": len(stubs),
            "post_selection_exclusions": excluded,
            "composition": composition,
            "tasks": {
                task.id: {
                    "pull_url": url,
                    "diff_sha256": hashlib.sha256(diffs[task.id].encode()).hexdigest(),
                }
                for task, url in final
            },
        },
    )
    print(json.dumps(composition, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
