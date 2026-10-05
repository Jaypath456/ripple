"""Stage B planted-anomaly evaluation (run from the repository root, after Stage A).

For each of the 15 pre-selected tasks the official PR diff is applied to a depth-limited
base checkout (nothing is executed) and four implementations are committed:

* control       - the official diff unchanged;
* unrelated     - control plus a new, unreferenced source file;
* drop_tests    - the official diff without its Python test files;
* stale_caller  - control plus an incompatible signature change to a function that has
                  an unchanged caller (inapplicable when no such function exists).

Each is verified against (a) an oracle Stage A report that predicts every gold source
file present at base, isolating Stage B, and (b) the actual seed-17 RIPPLE report.
Stage B runs without a model: model verdicts cannot change deterministic categories.
Files added by the implementation cannot be predicted by Stage A, so they are excluded
from the control false-alarm definition (any unexpected or stale_caller finding).
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from ripple.agent_models import (
    AffectedComponent,
    ChangeImpactReport,
    ChangeKind,
    RunStats,
)
from ripple.evaluation import EvaluationTask, load_tasks
from ripple.evaluation_results import select_adjudication_tasks
from ripple.final_data import _git
from ripple.final_evaluation import atomic_json
from ripple.scanner import scan_repository
from ripple.verification import verify_repository

MANIFEST = Path("evaluation/data/final_fea_tasks.json")
DIFFS = Path("evaluation/gold/diffs")
WORKSPACE = Path(".ripple/evaluation/final-v1")
RAW = Path("evaluation/raw/final-v1")
OUTPUT = Path("evaluation/results/stage_b_anomalies.json")
RIPPLE_OUTPUT = Path("evaluation/results/stage_b_ripple_reports.json")
UNRELATED = "ripple_planted_unrelated.py"
COMMIT = ("-c", "user.name=ripple", "-c", "user.email=ripple@invalid")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        *COMMIT,
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        message,
    )
    head = _git(repo, "rev-parse", "HEAD").strip()
    # A local verification clone only carries branch tips.
    _git(repo, "branch", "-f", f"variant-{message.replace(' ', '-')}", head)
    return head


def _reset(repo: Path, base: str) -> None:
    _git(repo, "checkout", "-q", "--detach", base)
    _git(repo, "reset", "-q", "--hard", base)
    _git(repo, "clean", "-qfdx")


def _apply(repo: Path, task: EvaluationTask, exclude: tuple[Path, ...] = ()) -> None:
    arguments = [f"--exclude={path.as_posix()}" for path in exclude]
    _git(
        repo,
        "apply",
        "--whitespace=nowarn",
        "--binary",
        *arguments,
        str((DIFFS / f"{task.id}.diff").resolve()),
    )


def _oracle(task: EvaluationTask, base: str, existing: set[str], root: Path) -> Path:
    report = ChangeImpactReport(
        report_id=f"oracle-{task.id}",
        request=task.masked_request,
        commit=base,
        status="completed",
        affected_components=tuple(
            AffectedComponent(
                target=path.as_posix(),
                change_type="modify",
                change_kind=ChangeKind.BUSINESS_LOGIC,
                reason="Oracle: official gold source file.",
                confidence="high",
                evidence=("oracle",),
            )
            for path in task.gold_files.source_python
            if path.as_posix() in existing
        ),
        run_stats=RunStats(
            model="oracle",
            tool_calls=0,
            duplicate_calls=0,
            llm_calls=0,
            runtime_seconds=0,
            stop_reason="submitted",
            dirty=False,
        ),
    )
    path = root / f"oracle-{task.id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def _plant_stale_caller(repo: Path, index, gold: set[str]) -> str | None:
    """Add a required first parameter to a top-level function with an outside caller."""

    candidates = sorted(
        {
            item.target_symbol
            for item in index.references
            if item.target_symbol
            and "::" in item.target_symbol
            and "." not in item.target_symbol.partition("::")[2]
            and item.source_path.as_posix() != item.target_symbol.partition("::")[0]
            and item.source_path.as_posix() not in gold
            and item.target_symbol.partition("::")[0] not in gold
            and not item.target_symbol.partition("::")[0].startswith(("test", "tests"))
        }
    )
    for symbol in candidates:
        path, _, name = symbol.partition("::")
        source_path = repo / path
        text = source_path.read_text(encoding="utf-8", errors="replace")
        pattern = re.compile(rf"^(def {re.escape(name)}\()(\s*\))?", re.MULTILINE)
        match = pattern.search(text)
        if match is None:
            continue
        replacement = (
            match.group(1) + "ripple_required" + (")" if match.group(2) else ", ")
        )
        edited = text[: match.start()] + replacement + text[match.end() :]
        try:
            ast.parse(edited)
        except SyntaxError:
            continue
        source_path.write_text(edited, encoding="utf-8")
        return symbol
    return None


def _categories(run, *, path: str | None = None) -> set[str]:
    return {
        item.category
        for item in run.analysis.findings
        if path is None or item.path == path
    }


def _verify(repo, report: Path, base: str, head: str, out: Path):
    return verify_repository(
        repo,
        report_value=str(report),
        requested_range=f"{base}..{head}",
        output_root=out,
    )


def _task_records(
    task: EvaluationTask,
    source: str,
    report: Path,
    repo: Path,
    base: str,
    heads: dict,
    planted: str | None,
    out: Path,
) -> list[dict]:
    gold_sources = {path.as_posix() for path in task.gold_files.source_python}
    records = []
    control = _verify(repo, report, base, heads["control"], out)
    added = {item.path for item in control.analysis.changes if item.status == "A"}
    unexpected = {
        item.path
        for item in control.analysis.findings
        if item.category == "unexpected" and item.path not in added
    }
    stale = _categories(control) & {"stale_caller"}
    records.append(
        {
            "task_id": task.id,
            "variant": "control",
            "report_source": source,
            "applicable": True,
            "false_alarm": bool(unexpected or stale),
            "control_unexpected_paths": sorted(unexpected),
            "detected_categories": sorted(_categories(control)),
        }
    )
    unrelated = _verify(repo, report, base, heads["unrelated"], out)
    records.append(
        {
            "task_id": task.id,
            "variant": "unrelated",
            "report_source": source,
            "applicable": True,
            "detected_categories": sorted(_categories(unrelated, path=UNRELATED)),
        }
    )
    drop = None
    if task.gold_files.test_python:
        drop = _verify(repo, report, base, heads["drop_tests"], out)
    records.append(
        {
            "task_id": task.id,
            "variant": "drop_tests",
            "report_source": source,
            "applicable": drop is not None,
            "detected_categories": sorted(
                {
                    item.category
                    for item in drop.analysis.findings
                    if item.path in gold_sources and item.category == "missing_test"
                }
            )
            if drop
            else [],
        }
    )
    stale_run = (
        _verify(repo, report, base, heads["stale_caller"], out) if planted else None
    )
    records.append(
        {
            "task_id": task.id,
            "variant": "stale_caller",
            "report_source": source,
            "applicable": stale_run is not None,
            "planted_symbol": planted,
            "detected_categories": sorted(
                {
                    item.category
                    for item in stale_run.analysis.findings
                    if item.category == "stale_caller"
                    and planted in " ".join(item.evidence)
                }
            )
            if stale_run
            else [],
        }
    )
    return records


def _process(task_id: str, sources: tuple[str, ...], scratch_root: str) -> dict:
    """Run every variant for one task; returns records or a skip reason."""

    task = {item.id: item for item in load_tasks(MANIFEST).tasks}[task_id]
    scratch = Path(tempfile.mkdtemp(prefix=f"{task_id}-", dir=scratch_root))
    repo = scratch / "repo"
    repo.mkdir()
    result: dict = {"oracle": [], "ripple": [], "skipped": None}
    try:
        _git(repo, "init", "-q")
        _git(repo, "remote", "add", "origin", task.repository_url)
        _git(
            repo, "fetch", "-q", "--depth=501", "--no-tags", "origin", task.base_commit
        )
        _git(repo, "checkout", "-q", "--detach", "FETCH_HEAD")
        base = task.base_commit
        _git(repo, "branch", "-f", "base", base)
        index = scan_repository(repo)
        existing = {item.path.as_posix() for item in index.files}
        gold = {path.as_posix() for path in task.gold_files.source_python} | {
            path.as_posix() for path in task.gold_files.test_python
        }
        heads: dict[str, str] = {}
        _reset(repo, base)
        _apply(repo, task)
        heads["control"] = _commit(repo, "control")
        (repo / UNRELATED).write_text("def planted_unrelated():\n    return 1\n")
        heads["unrelated"] = _commit(repo, "unrelated")
        _reset(repo, base)
        _apply(repo, task, exclude=task.gold_files.test_python)
        heads["drop_tests"] = _commit(repo, "drop tests")
        _reset(repo, base)
        _apply(repo, task)
        planted = _plant_stale_caller(repo, index, gold)
        heads["stale_caller"] = _commit(repo, "stale caller")
        out = scratch / "out"
        if "oracle" in sources:
            report = _oracle(task, base, existing, scratch)
            result["oracle"] = _task_records(
                task, "oracle", report, repo, base, heads, planted, out
            )
        if "ripple" in sources:
            checkpoint = RAW / f"{task_id}__RIPPLE__17.json"
            raw = json.loads(checkpoint.read_text(encoding="utf-8"))
            if raw["status"] != "provider_failed" and raw.get("report_path"):
                result["ripple"] = _task_records(
                    task,
                    "ripple_seed_17",
                    Path(raw["report_path"]),
                    repo,
                    base,
                    heads,
                    planted,
                    out,
                )
            else:
                result["skipped"] = (
                    f"RIPPLE seed 17 has no usable report ({raw['status']})"
                )
    except Exception as error:  # noqa: BLE001 - recorded, never silent
        result["skipped"] = f"{type(error).__name__}: {error}"[:300]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def main() -> int:
    """Usage: run_stage_b.py [oracle|ripple|oracle,ripple] [LIMIT OUTPUT_DIR]"""

    from concurrent.futures import ProcessPoolExecutor

    from ripple.evaluation_results import stage_b_metrics

    sources = tuple((sys.argv[1] if len(sys.argv) > 1 else "oracle,ripple").split(","))
    tasks = load_tasks(MANIFEST).tasks
    selected = select_adjudication_tasks(tuple(tasks))
    output, ripple_output = OUTPUT, RIPPLE_OUTPUT
    if len(sys.argv) > 3:  # smoke test
        selected = selected[: int(sys.argv[2])]
        output = Path(sys.argv[3]) / OUTPUT.name
        ripple_output = Path(sys.argv[3]) / RIPPLE_OUTPUT.name
    with ProcessPoolExecutor(max_workers=6) as pool:
        futures = {
            task_id: pool.submit(_process, task_id, sources, str(WORKSPACE))
            for task_id in selected
        }
        results = {task_id: future.result() for task_id, future in futures.items()}
    skipped = {
        key: value["skipped"] for key, value in results.items() if value["skipped"]
    }
    oracle_records = [row for task_id in selected for row in results[task_id]["oracle"]]
    ripple_records = [row for task_id in selected for row in results[task_id]["ripple"]]
    if "oracle" in sources:
        atomic_json(output, oracle_records)
    if "ripple" in sources:
        atomic_json(
            ripple_output,
            {
                "records": ripple_records,
                "metrics": stage_b_metrics(ripple_records) if ripple_records else {},
                "skipped": skipped,
            },
        )
    print(
        json.dumps(
            {
                "sources": sources,
                "oracle": stage_b_metrics(oracle_records) if oracle_records else {},
                "ripple": stage_b_metrics(ripple_records) if ripple_records else {},
                "skipped": skipped,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
