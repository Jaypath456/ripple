"""Build the V2 held-out manifest (run once, from the repository root).

    python evaluation/v2_heldout/build_manifest.py --scratch DIR

Selection rule (fixed before any RIPPLE run; never looks at model output):
- pool: every task in the pinned FEA-Bench table (Lite is nearly exhausted);
- excluded: every task ID used before (Phase 3-5 development, final-v1, V2
  development) and every repository used in V2 development;
- order: sha256("ripple-v2-heldout|" + instance_id), a fixed pseudo-random order;
- eligible: merged PR with a usable title/body, 2-4 primary Python source files
  (the final-v1 rule), base commit fetchable from GitHub;
- diversity: at most 3 tasks per repository; stop at 30 tasks.
Nothing is executed; only Git fetches and public PR metadata/diffs are read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ripple.evaluation import (
    EvaluationError,
    EvaluationManifest,
    build_fea_bench_task,
    classify_gold_files,
    extract_changed_files,
    load_tasks,
)
from ripple.final_evaluation import atomic_json

HERE = Path(__file__).resolve().parent
TABLE = (
    "https://huggingface.co/datasets/microsoft/FEA-Bench/resolve/"
    "55ee2f78126a3ecdeac6f595fa1ba6ae5c600bad/data/test-00000-of-00001.parquet"
)
LITE = (
    "https://raw.githubusercontent.com/microsoft/FEA-Bench/"
    "fb3c11274796f6057bf447410540fcf2fa1d90b1/instances_lite.json"
)
SOURCE = (
    "https://huggingface.co/datasets/microsoft/FEA-Bench/tree/"
    "55ee2f78126a3ecdeac6f595fa1ba6ae5c600bad"
)
TARGET = 30
PER_REPOSITORY = 3
ORDER_SALT = "ripple-v2-heldout|"
V2_DEV_REPOSITORIES = {
    "boto/boto3",
    "graphql-python/graphene",
    "prometheus/client_python",
    "falconry/falcon",
}


def _get(url: str, *, accept: str | None = None) -> bytes:
    headers = {"User-Agent": "ripple-eval"} | ({"Accept": accept} if accept else {})
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code in {403, 429} and attempt < 5:
                time.sleep(300)
                continue
            raise
    raise EvaluationError("unreachable")


def prior_task_ids() -> dict[str, str]:
    """Every task ID used before, mapped to where it was used."""

    used: dict[str, str] = {}
    for name in ("dev_tasks", "mvp_tasks", "final_fea_tasks"):
        for task in load_tasks(ROOT / "evaluation/data" / f"{name}.json").tasks:
            used.setdefault(task.id, name)
    for row in json.loads((ROOT / "evaluation/v2_dev/results.json").read_text()):
        used.setdefault(row["case"], "v2_dev")
    return used


def _base_fetchable(repository_url: str, commit: str) -> bool:
    repo = Path(tempfile.mkdtemp(prefix="heldout-probe-"))
    try:
        for args in (
            ["init", "-q"],
            ["fetch", "-q", "--depth=1", "--no-tags", repository_url, commit],
        ):
            result = subprocess.run(
                ["git", "-C", str(repo), *args], capture_output=True, check=False
            )
            if result.returncode:
                return False
        return True
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def main() -> int:
    import pyarrow.parquet as pq

    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    table_path = args.scratch / "fea.parquet"
    if not table_path.exists():
        table_path.write_bytes(_get(TABLE))
    lite = {
        item if isinstance(item, str) else item.get("instance_id")
        for item in json.loads(_get(LITE))
    }
    rows = pq.read_table(table_path).to_pylist()
    used = prior_task_ids()
    order = sorted(
        rows,
        key=lambda row: hashlib.sha256(
            (ORDER_SALT + row["instance_id"]).encode()
        ).hexdigest(),
    )
    screening: list[dict] = []
    counts: Counter[str] = Counter()
    chosen = []
    diffs: dict[str, str] = {}
    for row in order:
        if len(chosen) >= TARGET:
            break
        task_id, repo = row["instance_id"], row["repo"]
        entry = {"task_id": task_id, "repository": repo}
        if task_id in used:
            screening.append(entry | {"outcome": f"excluded: used in {used[task_id]}"})
            continue
        if repo in V2_DEV_REPOSITORIES:
            screening.append(entry | {"outcome": "excluded: V2 development repository"})
            continue
        if counts[repo] >= PER_REPOSITORY:
            screening.append(entry | {"outcome": "skipped: repository cap"})
            continue
        try:
            diff = _get(
                f"https://patch-diff.githubusercontent.com/raw/{repo}/pull/"
                f"{row['pull_number']}.diff"
            ).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            screening.append(entry | {"outcome": f"ineligible: diff HTTP {error.code}"})
            continue
        sources = classify_gold_files(extract_changed_files(diff)).source_python
        if not 2 <= len(sources) <= 4:
            screening.append(
                entry | {"outcome": f"ineligible: {len(sources)} source files"}
            )
            continue
        try:
            pr = json.loads(
                _get(f"https://api.github.com/repos/{repo}/pulls/{row['pull_number']}")
            )
            if not pr.get("merged"):
                raise EvaluationError("pull request not merged")
            task = build_fea_bench_task(row, pr, diff, benchmark_source=SOURCE)
        except (EvaluationError, urllib.error.HTTPError, ValueError) as error:
            screening.append(entry | {"outcome": f"ineligible: {str(error)[:120]}"})
            continue
        if not _base_fetchable(task.repository_url, task.base_commit):
            screening.append(entry | {"outcome": "ineligible: base not fetchable"})
            continue
        chosen.append(task)
        diffs[task_id] = diff
        counts[repo] += 1
        screening.append(
            entry
            | {
                "outcome": "selected",
                "lite": task_id in lite,
                "eligibility": f"merged PR, usable title/body, {len(sources)} "
                "source files, base fetchable",
            }
        )
        print(f"selected {len(chosen):>2} {task_id}", flush=True)
    tasks = tuple(sorted(chosen, key=lambda item: item.id))
    manifest = EvaluationManifest(
        benchmark_source=SOURCE,
        selection_method=(
            "v2-heldout: pinned FEA-Bench table; all previously used task IDs and V2 "
            "development repositories excluded; fixed sha256 order; merged PR with "
            "usable title/body; 2-4 primary Python source files; base fetchable; at "
            f"most {PER_REPOSITORY} per repository; first {TARGET} eligible tasks"
        ),
        tasks=tasks,
    )
    overlap = {task.id: used.get(task.id) for task in tasks if task.id in used}
    payload = manifest.model_dump(mode="json") | {
        "overlap_audit": {
            "prior_task_ids_checked": len(used),
            "prior_sources": sorted(set(used.values())),
            "excluded_repositories": sorted(V2_DEV_REPOSITORIES),
            "overlapping_tasks": overlap,
            "fixtures_checked": [
                "demo/fixtures/sample_app (synthetic, not an FEA-Bench task)",
                "tests/fixtures/soft_delete_app (synthetic)",
                "tests/fixtures/cli_export_app (synthetic)",
                "tests/fixtures/v1_live_cli_export.json (RIPPLE itself)",
            ],
            "passed": not overlap,
        },
        "provenance": {
            "table": TABLE,
            "table_sha256": hashlib.sha256(table_path.read_bytes()).hexdigest(),
            "lite_list": LITE,
            "order_salt": ORDER_SALT,
            "per_repository_cap": PER_REPOSITORY,
            "target": TARGET,
            "repositories": dict(sorted(Counter(t.repository for t in tasks).items())),
            "diff_sha256": {
                task_id: hashlib.sha256(text.encode()).hexdigest()
                for task_id, text in sorted(diffs.items())
            },
            "screening": screening,
        },
    }
    if overlap:
        raise EvaluationError(f"overlap with prior sets: {overlap}")
    atomic_json(HERE / "manifest.json", payload)
    print(json.dumps(payload["provenance"]["repositories"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
