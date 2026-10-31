"""Deterministic Phase 7 statistics and research-report generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from statistics import fmean
from typing import Any

from ripple.evaluation import EvaluationError, EvaluationTask, load_tasks, score_ranking
from ripple.final_evaluation import (
    ALL_SYSTEMS,
    FINAL_BOOTSTRAP_SAMPLES,
    FINAL_BOOTSTRAP_SEED,
    RawRun,
    atomic_json,
)

HEADLINE_METRICS = ("precision", "recall", "f1", "recall_at_5", "recall_at_10", "mrr")


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    point = (len(ordered) - 1) * probability
    low = int(point)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def average_seeds(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce independent model runs within task before task aggregation."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["task_id"], row["system"])].append(row)
    output: list[dict[str, Any]] = []
    numeric = (
        *HEADLINE_METRICS,
        "false_positives",
        "test_precision",
        "test_recall",
        "symbol_recall",
        "tool_calls",
        "model_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "runtime_seconds",
    )
    for key in sorted(grouped):
        items = grouped[key]
        row = {
            "task_id": key[0],
            "system": key[1],
            "repository": items[0]["repository"],
            "seed_runs": len(items),
        }
        for field in numeric:
            present = [
                float(item[field]) for item in items if item.get(field) is not None
            ]
            row[field] = fmean(present) if present else None
        output.append(row)
    return output


def cluster_bootstrap_values(
    rows: Sequence[dict[str, Any]],
    accessor: Callable[[dict[str, Any]], float],
    *,
    samples: int = FINAL_BOOTSTRAP_SAMPLES,
    seed: int = FINAL_BOOTSTRAP_SEED,
) -> dict[str, float]:
    """Bootstrap repository clusters, retaining all tasks in sampled clusters."""

    if not rows or samples < 1:
        raise EvaluationError("cluster bootstrap requires rows and positive samples")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["repository"]].append(row)
    repositories = sorted(groups)
    generator = random.Random(seed)
    values: list[float] = []
    for _ in range(samples):
        sampled: list[dict[str, Any]] = []
        for _ in repositories:
            sampled.extend(groups[generator.choice(repositories)])
        values.append(fmean(accessor(item) for item in sampled))
    return {"low": _percentile(values, 0.025), "high": _percentile(values, 0.975)}


def paired_cluster_bootstrap(
    rows: Sequence[dict[str, Any]],
    left: str,
    right: str,
    *,
    metric: str = "f1",
    samples: int = FINAL_BOOTSTRAP_SAMPLES,
    seed: int = FINAL_BOOTSTRAP_SEED,
) -> dict[str, float | int]:
    """Paired task differences resampled by repository cluster."""

    by_task: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_task[row["task_id"]][row["system"]] = row
    pairs = [
        (values[left], values[right])
        for values in by_task.values()
        if left in values and right in values
    ]
    if not pairs:
        raise EvaluationError(f"no paired observations for {left} and {right}")
    clusters: dict[str, list[float]] = defaultdict(list)
    for left_row, right_row in pairs:
        clusters[left_row["repository"]].append(
            float(left_row[metric]) - float(right_row[metric])
        )
    repositories = sorted(clusters)
    generator = random.Random(seed)
    distribution = []
    for _ in range(samples):
        sample: list[float] = []
        for _ in repositories:
            sample.extend(clusters[generator.choice(repositories)])
        distribution.append(fmean(sample))
    observed = [item for values in clusters.values() for item in values]
    return {
        "task_count": len(observed),
        "mean_difference": fmean(observed),
        "ci_low": _percentile(distribution, 0.025),
        "ci_high": _percentile(distribution, 0.975),
    }


def stage_b_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Score applicable planted anomalies and untouched controls."""

    categories = {
        "unrelated": {"unexpected", "unexplained"},
        "drop_tests": {"missing_test"},
        "stale_caller": {"stale_caller"},
    }
    output: dict[str, Any] = {}
    for variant, accepted in categories.items():
        applicable = [
            item
            for item in records
            if item["variant"] == variant and item.get("applicable", True)
        ]
        detected = sum(
            bool(set(item.get("detected_categories", ())) & accepted)
            for item in applicable
        )
        output[f"{variant}_detection_recall"] = (
            detected / len(applicable) if applicable else None
        )
        output[f"{variant}_applicable"] = len(applicable)
        output[f"{variant}_inapplicable"] = sum(
            item["variant"] == variant for item in records
        ) - len(applicable)
    controls = [
        item
        for item in records
        if item["variant"] == "control" and item.get("applicable", True)
    ]
    alarms = sum(bool(item.get("false_alarm", False)) for item in controls)
    output["control_false_alarm_rate"] = alarms / len(controls) if controls else None
    output["control_runs"] = len(controls)
    return output


def adjudicated_precision(
    true_positives: int, false_positive_labels: Sequence[str]
) -> float | None:
    if any(
        label not in {"plausible_alternative", "wrong"}
        for label in false_positive_labels
    ):
        return None
    plausible = sum(label == "plausible_alternative" for label in false_positive_labels)
    denominator = true_positives + len(false_positive_labels)
    return (true_positives + plausible) / denominator if denominator else 0.0


def adjudicated_task_precision(
    rows: Sequence[dict[str, Any]],
    labels: dict[tuple[str, str], str],
    selected: set[str],
) -> float | None:
    """Mean over tasks of the seed-mean per-run adjudicated precision.

    Each RIPPLE run scores ``(TP + plausible false positives) / (TP + false
    positives)``; labels are per (task, file) and shared across seeds. ``None`` means
    a needed label is missing or invalid.
    """

    per_task: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row["system"] != "RIPPLE" or row["task_id"] not in selected:
            continue
        false_positives = row["false_positive_paths"]
        value = adjudicated_precision(
            row["predicted_count"] - len(false_positives),
            [labels.get((row["task_id"], path), "") for path in false_positives],
        )
        if value is None:
            return None
        per_task[row["task_id"]].append(value)
    if not per_task:
        return None
    return fmean(fmean(values) for values in per_task.values())


def select_adjudication_tasks(
    tasks: Sequence[EvaluationTask], count: int = 15
) -> tuple[str, ...]:
    """Select before predictions by cycling sorted source-count strata."""

    buckets: dict[str, list[EvaluationTask]] = defaultdict(list)
    for task in sorted(tasks, key=lambda item: item.id):
        buckets[task.selection_bucket].append(task)
    selected: list[str] = []
    names = ("2-4", "5-9", "10-20")
    position = 0
    while len(selected) < min(count, len(tasks)):
        progressed = False
        for name in names:
            if position < len(buckets[name]) and len(selected) < count:
                selected.append(buckets[name][position].id)
                progressed = True
        if not progressed:
            break
        position += 1
    return tuple(selected)


def build_adjudication_template(
    tasks: Sequence[EvaluationTask], rows: Sequence[dict[str, Any]], output: Path
) -> dict[str, Any]:
    selected = set(select_adjudication_tasks(tasks))
    task_map = {item.id: item for item in tasks}
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row["system"] != "RIPPLE" or row["task_id"] not in selected:
            continue
        for path in row.get("false_positive_paths", []):
            entry = entries.setdefault(
                (row["task_id"], path),
                {
                    "task_id": row["task_id"],
                    "request": task_map[row["task_id"]].masked_request,
                    "predicted_false_positive_file": path,
                    "seeds": [],
                    "evidence_reason": row.get("prediction_evidence", {}).get(
                        path, "See saved report and trace."
                    ),
                    "base_code_context": row.get("base_code_context", {}).get(
                        path, "See leak-safe base checkout."
                    ),
                    "label": "",
                    "note": "",
                },
            )
            entry["seeds"].append(row["requested_seed"])
    payload = {
        "instructions": (
            "Human: set each entry's label to plausible_alternative or wrong. "
            "A label applies to every seed that predicted the file. "
            "Do not change benchmark gold."
        ),
        "selected_task_ids": sorted(selected),
        "entries": [entries[key] for key in sorted(entries)],
    }
    atomic_json(output, payload)
    return payload


def _test_metrics(
    predicted: Sequence[Path], gold: Sequence[Path]
) -> tuple[float, float]:
    predictions = set(predicted)
    truth = set(gold)
    overlap = len(predictions & truth)
    return (
        overlap / len(predictions) if predictions else 0.0,
        overlap / len(truth) if truth else 0.0,
    )


def _load_runs(raw_root: Path) -> list[RawRun]:
    return [
        RawRun.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(raw_root.glob("*.json"))
    ]


def _score_runs(
    runs: Sequence[RawRun],
    tasks: dict[str, EvaluationTask],
    symbol_gold: dict[str, Any],
) -> list[dict[str, Any]]:
    ripple_k = {
        (run.task_id, run.requested_seed): len(run.source_ranking)
        for run in runs
        if run.system == "RIPPLE"
    }
    rows = []
    for run in runs:
        task = tasks[run.task_id]
        if run.system in {"B0", "B1", "B2"}:
            seeds = sorted(
                seed
                for task_id, seed in ripple_k
                if task_id == run.task_id and seed is not None
            ) or [None]
        else:
            seeds = [run.requested_seed]
        for matched_seed in seeds:
            k = ripple_k.get((run.task_id, matched_seed), len(run.source_ranking))
            metric = score_ranking(
                run.source_ranking,
                task.gold_files.source_python,
                prediction_k=k
                if run.system in {"B0", "B1", "B2"}
                else len(run.source_ranking),
            )
            test_p, test_r = _test_metrics(
                run.test_ranking, task.gold_files.test_python
            )
            gold_symbols = set(symbol_gold.get(run.task_id, {}).get("symbols", ()))
            predicted_symbols = set(run.predicted_symbols)
            rows.append(
                {
                    "task_id": run.task_id,
                    "repository": run.repository,
                    "system": run.system,
                    "requested_seed": matched_seed,
                    **metric.model_dump(),
                    "test_precision": test_p,
                    "test_recall": test_r,
                    "symbol_recall": len(predicted_symbols & gold_symbols)
                    / len(gold_symbols)
                    if gold_symbols
                    else None,
                    "unmappable_gold_hunks": symbol_gold.get(run.task_id, {}).get(
                        "unmappable_hunks", 0
                    ),
                    "predicted_count": len(set(run.source_ranking[:k])),
                    "tool_calls": run.tool_calls,
                    "model_calls": run.model_calls,
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                    "total_tokens": run.total_tokens,
                    "runtime_seconds": run.runtime_seconds,
                    "status": run.status,
                    "failure_kind": run.failure_kind,
                    "false_positive_paths": [
                        path.as_posix()
                        for path in run.source_ranking[:k]
                        if path not in set(task.gold_files.source_python)
                    ],
                }
            )
    return rows


def _aggregate(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    task_rows = average_seeds(rows)
    aggregates = []
    intervals: dict[str, Any] = {}
    for system in ALL_SYSTEMS:
        selected = [item for item in task_rows if item["system"] == system]
        if not selected:
            continue
        aggregate = {"system": system, "task_count": len(selected)}
        for metric in (
            *HEADLINE_METRICS,
            "false_positives",
            "test_precision",
            "test_recall",
            "symbol_recall",
            "runtime_seconds",
            "tool_calls",
            "model_calls",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        ):
            values = [
                float(item[metric]) for item in selected if item.get(metric) is not None
            ]
            aggregate[metric] = fmean(values) if values else None
        aggregates.append(aggregate)
        intervals[system] = {
            metric: cluster_bootstrap_values(
                [item for item in selected if item.get(metric) is not None],
                lambda row, key=metric: float(row[key]),
            )
            for metric in HEADLINE_METRICS
            if any(item.get(metric) is not None for item in selected)
        }
    return aggregates, intervals


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _results_table(aggregates: Sequence[dict[str, Any]]) -> str:
    headers = (
        "System",
        "P",
        "R",
        "F1",
        "R@5",
        "R@10",
        "MRR",
        "FP/task",
        "Test P",
        "Test R",
        "Symbol R",
        "Runtime (s)",
        "Tools",
        "Calls",
        "Tokens",
    )
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in aggregates:
        values = [row["system"]] + [
            _fmt(row.get(key))
            for key in (
                "precision",
                "recall",
                "f1",
                "recall_at_5",
                "recall_at_10",
                "mrr",
                "false_positives",
                "test_precision",
                "test_recall",
                "symbol_recall",
                "runtime_seconds",
                "tool_calls",
                "model_calls",
                "total_tokens",
            )
        ]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


PAIRED_METRICS = (*HEADLINE_METRICS, "false_positives")
ROOT_MARKERS = ("<!-- final-results:start -->", "<!-- final-results:end -->")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines.extend(
        "| "
        + " | ".join(
            value
            if isinstance(value, str)
            else str(value)
            if isinstance(value, int)
            else _fmt(value)
            for value in row
        )
        + " |"
        for row in rows
    )
    return "\n".join(lines)


def _interval_table(summary: dict[str, Any]) -> str:
    rows = []
    for item in summary["aggregates"]:
        system = item["system"]
        intervals = summary["confidence_intervals"].get(system, {})
        rows.append(
            [system]
            + [
                f"{_fmt(item[metric])} [{_fmt(intervals[metric]['low'])}, "
                f"{_fmt(intervals[metric]['high'])}]"
                if metric in intervals
                else "n/a"
                for metric in HEADLINE_METRICS
            ]
        )
    return _table(("System", "P", "R", "F1", "R@5", "R@10", "MRR"), rows)


def _paired_table(summary: dict[str, Any]) -> str:
    rows = []
    for name, metrics in summary["paired_differences"].items():
        for metric, value in metrics.items():
            rows.append(
                (
                    name.replace("_minus_", " − "),
                    metric,
                    value["task_count"],
                    value["mean_difference"],
                    f"[{_fmt(value['ci_low'])}, {_fmt(value['ci_high'])}]",
                    "yes" if value["ci_excludes_zero"] else "no",
                )
            )
    return _table(
        ("Comparison", "Metric", "Tasks", "Mean diff", "95% CI", "CI excludes 0"),
        rows,
    )


def _status_table(summary: dict[str, Any]) -> str:
    statuses = sorted(
        {key for value in summary["status_mix_by_system"].values() for key in value}
    )
    return _table(
        ("System", *statuses),
        [
            (
                system,
                *(
                    summary["status_mix_by_system"][system].get(key, 0)
                    for key in statuses
                ),
            )
            for system in summary["status_mix_by_system"]
        ],
    )


def _stage_b_table(summary: dict[str, Any]) -> str:
    rows = []
    for source, label in (
        ("stage_b", "Oracle Stage A"),
        ("stage_b_ripple", "RIPPLE seed-17 report"),
    ):
        metrics = summary[source].get("metrics", {})
        if not metrics:
            continue
        for variant, name in (
            ("unrelated", "unrelated file → unexpected/unexplained"),
            ("drop_tests", "dropped tests → missing_test"),
            ("stale_caller", "stale caller → stale_caller"),
        ):
            rows.append(
                (
                    label,
                    name,
                    metrics[f"{variant}_applicable"],
                    metrics[f"{variant}_inapplicable"],
                    metrics[f"{variant}_detection_recall"],
                )
            )
        rows.append(
            (
                label,
                "control false-alarm rate",
                metrics["control_runs"],
                0,
                metrics["control_false_alarm_rate"],
            )
        )
    return _table(("Report", "Anomaly", "Applicable", "Inapplicable", "Rate"), rows)


def _provider_text(summary: dict[str, Any]) -> str:
    lines = []
    for archive in summary["provider_failure_reconciliation"]:
        outcome = ", ".join(
            f"{key}: {value}" for key, value in archive["current_status"].items()
        )
        lines.append(
            f"- `{archive['archive']}`: {archive['archived_failures']} original "
            f"provider-failed checkpoints preserved ({', '.join(f'{key}: {value}' for key, value in archive['original_failure_kinds'].items())}); "
            f"after one exact-checkpoint retry the current statuses are {outcome}."
        )
    remaining = summary["status_mix"].get("provider_failed", 0)
    lines.append(
        f"- Provider failures remaining in the scored denominator: {remaining} "
        f"({', '.join(summary['remaining_provider_failures']) or 'none'})."
    )
    return "\n".join(lines)


def _headline(summary: dict[str, Any]) -> str:
    """Compact generated block shared by the results README and the root README."""

    totals = summary["totals"]
    adjudication = summary["adjudication"]
    return f"""Generated by `ripple build-results` from `evaluation/raw/{summary["config_version"]}` ({summary["run_count"]} checkpoints, {summary["task_count"]} tasks, {summary["repository_count"]} repositories). Model systems are averaged over seeds within task, then over tasks.

{_results_table(summary["aggregates"])}

Status mix by system (runs):

{_status_table(summary)}

Paired repository-cluster bootstrap (RIPPLE minus baseline, F1 and recall):

{_table(("Comparison", "Metric", "Mean diff", "95% CI"), [(name.replace("_minus_", " − "), metric, value["mean_difference"], f"[{_fmt(value['ci_low'])}, {_fmt(value['ci_high'])}]") for name, metrics in summary["paired_differences"].items() for metric, value in metrics.items() if metric in {"f1", "recall"}])}

Stage B planted anomalies ({summary["stage_b"].get("task_count", 0)} tasks):

{_stage_b_table(summary)}

Secondary human-adjudicated RIPPLE precision on the {adjudication["selected_tasks"]} pre-selected tasks: `{_fmt(adjudication["precision"])}` ({adjudication["entries"]} false-positive judgments; {adjudication["note"]}).

Totals across all {summary["run_count"]} runs: {totals["model_calls"]} model calls, {totals["total_tokens"]} tokens ({totals["input_tokens"]} input, {totals["output_tokens"]} output), {totals["tool_calls"]} tool calls, {_fmt(totals["runtime_seconds"] / 3600)} hours of recorded runtime. Provider failures still in the denominator: {summary["status_mix"].get("provider_failed", 0)}."""


def _render(summary: dict[str, Any]) -> str:
    return f"""# RIPPLE Final Evaluation (`{summary["config_version"]}`)

This file is generated by `ripple build-results`; do not edit it by hand.

## Experimental Setup

Configuration `{summary["config_version"]}` (`{summary["config_hash"]}`) used model `{summary["model"]}` through the configured BullsAI OpenAI-compatible gateway. Primary requests were deterministically path-masked and truncated to the frozen character limit. The agent saw only an exact, clean base checkout. Model configurations used requested seeds {summary["seeds"]}; provider deterministic seed control was unavailable. Confidence intervals use {summary["bootstrap_samples"]} repository-cluster bootstrap samples with seed {summary["bootstrap_seed"]}.

Deterministic B0–B2 set precision uses a matched *k* equal to RIPPLE's confirmed source-file count for that task and seed; *k*=0 remains zero. Ranked recall and MRR use each method's own ranking. Model seeds are averaged within each task before aggregation. An abstaining run predicts nothing and scores zero set precision, recall, and F1; it is never removed from the denominator, and neither is a provider failure.

## Headline Results

{_headline(summary)}

## Confidence Intervals

Point estimate with 95% repository-cluster bootstrap percentile interval.

{_interval_table(summary)}

## Paired Differences

RIPPLE minus each baseline over paired tasks, resampling repositories. "CI excludes 0" is descriptive; with {summary["repository_count"]} clusters no multiplicity correction was applied.

{_paired_table(summary)}

## Ablations

A1 removes `co_changed`; A2 removes `get_dependencies` and `find_references`; A3 removes report validation. All other frozen settings are shared. Their measurements appear in the main table.

## Recent PR Results

{summary["recent_pr_note"]}

## Stage B Verification

Deterministic post-change verification against planted anomalies; no model participates, so no model can change a category. Oracle Stage A predicts every gold source file present at base, isolating Stage B. The RIPPLE row uses the actual seed-17 report, which usually abstained (an empty prediction makes every changed file unexpected). Skipped tasks: `{json.dumps(summary["stage_b_ripple"].get("skipped", {}), sort_keys=True)}`.

{_stage_b_table(summary)}

## Provider Failures and Retries

{_provider_text(summary)}

Failure taxonomy of current checkpoints: `{json.dumps(summary["failure_taxonomy"], sort_keys=True)}`.

## Cost / Runtime

Calls, tokens, and runtime are in the main table (per-task means). Dollar cost is not reported because no explicit per-token end-user price was available for the university gateway.

## Manual Adjudication

Primary exact-diff precision is unchanged. Secondary adjudicated precision is `{_fmt(summary["adjudication"]["precision"])}` over {summary["adjudication"]["entries"]} judgments. {summary["adjudication"]["note"]}

## Artifact Hashes

SHA-256 of the frozen inputs and derived artifacts; per-checkpoint hashes are in `final_summary.json`.

{_table(("Artifact", "SHA-256"), [(f"`{path}`", f"`{digest}`") for path, digest in summary["artifact_sha256"].items()])}

## Limitations

A historical PR is one implementation, not the only plausible one. Static analysis is limited by dynamic Python behavior. Results depend on the selected model and provider. No recent-PR contamination split was evaluated because an authoritative training cutoff for the model was unavailable. The benchmark is finite and confidence intervals remain wide. Symbol truth excludes unmappable syntax failures. Changed-test localization is not runtime coverage. RIPPLE does not execute target code or tests.
"""


def _provider_reconciliation(
    archives: Sequence[Path], runs: Sequence[RawRun]
) -> list[dict[str, Any]]:
    current = {f"{run.run_id}.json": run.status for run in runs}
    output = []
    for archive in archives:
        originals = [
            RawRun.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(archive.glob("*.json"))
        ]
        output.append(
            {
                "archive": archive.as_posix(),
                "archived_failures": len(originals),
                "original_failure_kinds": dict(
                    sorted(
                        Counter(item.failure_kind or "" for item in originals).items()
                    )
                ),
                "current_status": dict(
                    sorted(
                        Counter(
                            current.get(f"{item.run_id}.json", "missing")
                            for item in originals
                        ).items()
                    )
                ),
                "run_ids": [item.run_id for item in originals],
            }
        )
    return output


def _adjudication(path: Path | None, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "precision": None,
            "entries": 0,
            "selected_tasks": 0,
            "note": "Human labels have not been supplied.",
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries", [])
    selected = set(payload.get("selected_task_ids", []))
    labels = {
        (item["task_id"], item["predicted_false_positive_file"]): item.get("label", "")
        for item in entries
    }
    value = adjudicated_task_precision(rows, labels, selected)
    if value is None:
        note = "Human labels remain incomplete."
    elif not entries:
        note = (
            "RIPPLE made no false-positive predictions on the selected tasks, so no "
            "human judgment was required and the secondary value equals primary "
            "precision on this subset."
        )
    else:
        note = "All human labels are complete."
    return {
        "precision": value,
        "entries": len(entries),
        "selected_tasks": len(selected),
        "label_counts": dict(sorted(Counter(labels.values()).items())),
        "note": note,
    }


def build_results(
    *,
    manifest_path: Path,
    raw_root: Path,
    config_path: Path,
    output_readme: Path,
    output_summary: Path,
    symbol_gold_path: Path | None = None,
    stage_b_path: Path | None = None,
    stage_b_ripple_path: Path | None = None,
    adjudication_path: Path | None = None,
    recent_summary_path: Path | None = None,
    provider_archives: Sequence[Path] = (),
    hashed_artifacts: Sequence[Path] = (),
    root_readme: Path | None = None,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest = load_tasks(manifest_path)
    task_map = {item.id: item for item in manifest.tasks}
    runs = _load_runs(raw_root)
    symbol_gold = (
        json.loads(symbol_gold_path.read_text())
        if symbol_gold_path and symbol_gold_path.exists()
        else {}
    )
    rows = _score_runs(runs, task_map, symbol_gold)
    aggregates, intervals = _aggregate(rows)
    task_rows = average_seeds(rows)
    systems = {item["system"] for item in task_rows}
    paired: dict[str, Any] = {}
    for baseline in ("B0", "B1", "B2", "B3", "B4"):
        if {"RIPPLE", baseline} <= systems:
            paired[f"RIPPLE_minus_{baseline}"] = {}
            for metric in PAIRED_METRICS:
                value = paired_cluster_bootstrap(
                    task_rows, "RIPPLE", baseline, metric=metric
                )
                value["ci_excludes_zero"] = value["ci_low"] > 0 or value["ci_high"] < 0
                paired[f"RIPPLE_minus_{baseline}"][metric] = value
    stage_records = (
        json.loads(stage_b_path.read_text())
        if stage_b_path and stage_b_path.exists()
        else []
    )
    ripple_stage = (
        json.loads(stage_b_ripple_path.read_text())
        if stage_b_ripple_path and stage_b_ripple_path.exists()
        else {}
    )
    recent = (
        json.loads(recent_summary_path.read_text())
        if recent_summary_path and recent_summary_path.exists()
        else None
    )
    status_by_system: dict[str, Counter[str]] = defaultdict(Counter)
    for run in runs:
        status_by_system[run.system][run.status] += 1
    total_fields = (
        "model_calls",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    )
    totals: dict[str, Any] = {
        field: sum(getattr(run, field) or 0 for run in runs) for field in total_fields
    }
    totals["runtime_seconds"] = sum(run.runtime_seconds or 0 for run in runs)
    summary = {
        "config_version": config["version"],
        "config_hash": config["config_hash"],
        "model": config["provider"]["model"],
        "seeds": config["requested_seeds"],
        "bootstrap_seed": config["bootstrap"]["seed"],
        "bootstrap_samples": config["bootstrap"]["samples"],
        "artifact_sha256": {
            path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in hashed_artifacts
        },
        "raw_artifact_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(raw_root.glob("*.json"))
        },
        "task_count": len(task_map),
        "repository_count": len({item.repository for item in task_map.values()}),
        "run_count": len(runs),
        "aggregates": aggregates,
        "confidence_intervals": intervals,
        "paired_differences": paired,
        "status_mix": dict(sorted(Counter(item.status for item in runs).items())),
        "status_mix_by_system": {
            system: dict(sorted(status_by_system[system].items()))
            for system in ALL_SYSTEMS
            if system in status_by_system
        },
        "failure_taxonomy": dict(
            sorted(
                Counter(item.failure_kind for item in runs if item.failure_kind).items()
            )
        ),
        "remaining_provider_failures": sorted(
            run.run_id for run in runs if run.status == "provider_failed"
        ),
        "provider_failure_reconciliation": _provider_reconciliation(
            provider_archives, runs
        ),
        "totals": totals,
        "stage_b": {
            "task_count": len({item["task_id"] for item in stage_records}),
            "metrics": stage_b_metrics(stage_records) if stage_records else {},
        },
        "stage_b_ripple": {
            "task_count": len(
                {item["task_id"] for item in ripple_stage.get("records", [])}
            ),
            "metrics": stage_b_metrics(ripple_stage["records"])
            if ripple_stage.get("records")
            else {},
            "skipped": ripple_stage.get("skipped", {}),
        },
        "recent_pr": recent,
        "recent_pr_note": "Not evaluated: no authoritative training cutoff was "
        "available for the model, so no post-cutoff split could be defined."
        if recent is None
        else "Generated separately; see final_summary.json.",
        "adjudication": _adjudication(adjudication_path, rows),
        "per_task_seed_averages": task_rows,
    }
    atomic_json(output_summary, summary)
    output_readme.parent.mkdir(parents=True, exist_ok=True)
    output_readme.write_text(_render(summary), encoding="utf-8")
    if root_readme is not None:
        text = root_readme.read_text(encoding="utf-8")
        start, end = ROOT_MARKERS
        if start not in text or end not in text:
            raise EvaluationError(f"{root_readme} lacks generated-results markers")
        head, _, rest = text.partition(start)
        _, _, tail = rest.partition(end)
        root_readme.write_text(
            f"{head}{start}\n{_headline(summary)}\n{end}{tail}", encoding="utf-8"
        )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=Path("evaluation/data/final_fea_tasks.json")
    )
    parser.add_argument("--raw", type=Path, default=Path("evaluation/raw/final-v1"))
    parser.add_argument(
        "--config", type=Path, default=Path("evaluation/final_config.json")
    )
    args = parser.parse_args(argv)
    raw_parent = args.raw.parent
    build_results(
        manifest_path=args.manifest,
        raw_root=args.raw,
        config_path=args.config,
        output_readme=Path("evaluation/results/README.md"),
        output_summary=Path("evaluation/results/final_summary.json"),
        symbol_gold_path=Path("evaluation/gold/final_symbols.json"),
        stage_b_path=Path("evaluation/results/stage_b_anomalies.json"),
        stage_b_ripple_path=Path("evaluation/results/stage_b_ripple_reports.json"),
        adjudication_path=Path("evaluation/adjudication/adjudication_template.json"),
        provider_archives=sorted(raw_parent.glob(f"{args.raw.name}-provider-*")),
        hashed_artifacts=(
            args.config,
            args.manifest,
            Path("evaluation/data/final_fea_provenance.json"),
            Path("evaluation/final_schedule.json"),
            Path("evaluation/audits/pre_run_leak_audit.json"),
            Path("evaluation/audits/post_run_symbol_audit.json"),
            Path("evaluation/gold/final_symbols.json"),
            Path("evaluation/results/stage_b_anomalies.json"),
            Path("evaluation/results/stage_b_ripple_reports.json"),
            Path("evaluation/adjudication/adjudication_template.json"),
            Path("evaluation/raw/final-v1-reports-and-traces.tar.gz"),
        ),
        root_readme=Path("README.md"),
    )
    print("Results: evaluation/results/README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
