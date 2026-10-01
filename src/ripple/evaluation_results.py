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
    entries = []
    for row in rows:
        if row["system"] != "RIPPLE" or row["task_id"] not in selected:
            continue
        for path in row.get("false_positive_paths", []):
            entries.append(
                {
                    "task_id": row["task_id"],
                    "request": task_map[row["task_id"]].masked_request,
                    "predicted_false_positive_file": path,
                    "evidence_reason": row.get("prediction_evidence", {}).get(
                        path, "See saved report and trace."
                    ),
                    "base_code_context": row.get("base_code_context", {}).get(
                        path, "See leak-safe base checkout."
                    ),
                    "label": "",
                    "note": "",
                }
            )
    payload = {
        "instructions": "Human: set each label to plausible_alternative or wrong. Do not change benchmark gold.",
        "selected_task_ids": sorted(selected),
        "entries": entries,
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
        "Runtime",
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


def _render(summary: dict[str, Any]) -> str:
    return f"""# RIPPLE Evaluation

## Experimental Setup

Configuration `{summary["config_version"]}` (`{summary["config_hash"]}`) used model `{summary["model"]}` through the configured BullsAI OpenAI-compatible gateway. Primary requests were deterministically path-masked and truncated to the frozen character limit. The agent saw only an exact, clean base checkout. Model configurations used requested seeds {summary["seeds"]}; provider deterministic seed control was unavailable. Confidence intervals use {summary["bootstrap_samples"]} repository-cluster bootstrap samples with seed {summary["bootstrap_seed"]}.

Deterministic B0–B2 set precision uses a matched *k* equal to RIPPLE's confirmed source-file count for that task and seed; *k*=0 remains zero. Ranked recall and MRR use each method's own ranking. Model seeds are averaged within each task before aggregation.

## FEA-Bench Results

{_results_table(summary["aggregates"])}

## Confidence Intervals

```json
{json.dumps(summary["confidence_intervals"], indent=2, sort_keys=True)}
```

## Paired Differences

```json
{json.dumps(summary["paired_differences"], indent=2, sort_keys=True)}
```

## Ablations

A1 removes `co_changed`; A2 removes `get_dependencies` and `find_references`; A3 removes report validation. All other frozen settings are shared. Their measurements appear in the main generated table.

## Recent PR Results

{summary["recent_pr_note"]}

## Stage B Verification

```json
{json.dumps(summary["stage_b"], indent=2, sort_keys=True)}
```

## Status Mix

```json
{json.dumps(summary["status_mix"], indent=2, sort_keys=True)}
```

## Cost / Runtime

Calls, tokens, and runtime are in the generated table. Dollar cost is not reported because no explicit per-token end-user price was available for the university gateway.

## Failure Taxonomy

```json
{json.dumps(summary["failure_taxonomy"], indent=2, sort_keys=True)}
```

## Manual Adjudication

Primary exact-diff precision is unchanged. Secondary adjudicated precision is `{_fmt(summary["adjudicated_precision"])}`. `{summary["adjudication_note"]}`

## Limitations

A historical PR is one implementation, not the only plausible one. Static analysis is limited by dynamic Python behavior. Results depend on the selected model and provider. The recent-PR sample is small. The benchmark is finite and confidence intervals remain uncertain. Symbol truth excludes unmappable syntax failures. Changed-test localization is not runtime coverage. RIPPLE does not execute target code or tests.
"""


def build_results(
    *,
    manifest_path: Path,
    raw_root: Path,
    config_path: Path,
    output_readme: Path,
    output_summary: Path,
    symbol_gold_path: Path | None = None,
    stage_b_path: Path | None = None,
    adjudication_path: Path | None = None,
    recent_summary_path: Path | None = None,
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
    paired = {
        f"RIPPLE_minus_{baseline}": paired_cluster_bootstrap(
            task_rows, "RIPPLE", baseline
        )
        for baseline in ("B0", "B1", "B2", "B3", "B4")
        if {"RIPPLE", baseline} <= {item["system"] for item in task_rows}
    }
    stage_records = (
        json.loads(stage_b_path.read_text())
        if stage_b_path and stage_b_path.exists()
        else []
    )
    stage = stage_b_metrics(stage_records) if stage_records else {}
    labels: list[str] = []
    true_positives = 0
    adjudication_note = "Human labels have not been supplied."
    if adjudication_path and adjudication_path.exists():
        adjudication = json.loads(adjudication_path.read_text())
        labels = [item.get("label", "") for item in adjudication.get("entries", [])]
        adjudication_note = (
            "All human labels are complete."
            if labels and all(labels)
            else "Human labels remain incomplete."
        )
        selected = set(adjudication.get("selected_task_ids", []))
        true_positives = sum(
            round(float(item["precision"]) * (float(item["false_positives"]) + 1))
            for item in task_rows
            if item["system"] == "RIPPLE" and item["task_id"] in selected
        )
    recent = (
        json.loads(recent_summary_path.read_text())
        if recent_summary_path and recent_summary_path.exists()
        else None
    )
    summary = {
        "config_version": config["version"],
        "config_hash": config["config_hash"],
        "model": config["provider"]["model"],
        "seeds": config["requested_seeds"],
        "bootstrap_seed": config["bootstrap"]["seed"],
        "bootstrap_samples": config["bootstrap"]["samples"],
        "raw_artifact_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(raw_root.glob("*.json"))
        },
        "task_count": len(task_map),
        "run_count": len(runs),
        "aggregates": aggregates,
        "confidence_intervals": intervals,
        "paired_differences": paired,
        "status_mix": dict(sorted(Counter(item.status for item in runs).items())),
        "failure_taxonomy": dict(
            sorted(
                Counter(item.failure_kind for item in runs if item.failure_kind).items()
            )
        ),
        "stage_b": stage,
        "recent_pr": recent,
        "recent_pr_note": "Not available."
        if recent is None
        else "Generated separately; see final_summary.json.",
        "adjudicated_precision": adjudicated_precision(true_positives, labels)
        if labels
        else None,
        "adjudication_note": adjudication_note,
        "per_task_seed_averages": task_rows,
    }
    atomic_json(output_summary, summary)
    output_readme.parent.mkdir(parents=True, exist_ok=True)
    rendered = _render(summary)
    output_readme.write_text(rendered, encoding="utf-8")
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
    parser.add_argument(
        "--readme", type=Path, default=Path("evaluation/results/README.md")
    )
    parser.add_argument(
        "--summary", type=Path, default=Path("evaluation/results/final_summary.json")
    )
    parser.add_argument(
        "--symbol-gold", type=Path, default=Path("evaluation/gold/final_symbols.json")
    )
    parser.add_argument(
        "--stage-b",
        type=Path,
        default=Path("evaluation/results/stage_b_anomalies.json"),
    )
    parser.add_argument(
        "--adjudication",
        type=Path,
        default=Path("evaluation/adjudication/adjudication_template.json"),
    )
    args = parser.parse_args(argv)
    build_results(
        manifest_path=args.manifest,
        raw_root=args.raw,
        config_path=args.config,
        output_readme=args.readme,
        output_summary=args.summary,
        symbol_gold_path=args.symbol_gold,
        stage_b_path=args.stage_b,
        adjudication_path=args.adjudication,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
