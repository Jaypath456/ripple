"""Leak-safe, checkpointed Phase 7 evaluation harness."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ripple.agent import (
    AGENT_VARIANTS,
    MAX_CANDIDATES,
    MAX_NO_PROGRESS,
    MAX_TOOL_CALLS,
    analyze_repository,
)
from ripple.agent_models import FULL_REPORT_CONFIG_VERSION, FeatureRequest
from ripple.baselines import bm25_baseline, cochange_baseline, structural_baseline
from ripple.diff_models import STAGE_B_CONFIG_VERSION
from ripple.evaluation import (
    EVALUATION_REQUEST_LIMIT,
    EvaluationError,
    EvaluationTask,
    _dedupe_paths,
    load_tasks,
    prepare_evaluation_request,
    prepare_repository,
)
from ripple.llm import LLMClient, LLMError
from ripple.models import RepositoryIndex
from ripple.phase5_baselines import one_shot_baseline, react_baseline
from ripple.scanner import scan_repository

FINAL_CONFIG_VERSION = "final-v1"
FINAL_SEEDS = (17, 42, 1729)
FINAL_BOOTSTRAP_SEED = 1729
FINAL_BOOTSTRAP_SAMPLES = 1000
DETERMINISTIC_SYSTEMS = ("B0", "B1", "B2")
LLM_SYSTEMS = ("B3", "B4", "RIPPLE", "A1", "A2", "A3")
ALL_SYSTEMS = DETERMINISTIC_SYSTEMS + LLM_SYSTEMS
FailureKind = Literal[
    "authentication_failure",
    "rate_limit",
    "timeout",
    "server_error",
    "malformed_model_output",
    "controller_failure",
    "setup_failure",
    "other_provider_failure",
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScheduleEntry(FrozenModel):
    run_id: str
    task_id: str
    system: Literal["B0", "B1", "B2", "B3", "B4", "RIPPLE", "A1", "A2", "A3"]
    requested_seed: int | None = None


class RawRun(FrozenModel):
    schema_version: int = 1
    config_version: str
    config_hash: str
    run_id: str
    task_id: str
    repository: str
    system: str
    requested_seed: int | None
    provider_seed_control: bool = False
    status: str
    failure_kind: FailureKind | None = None
    failure_message: str | None = None
    prepared_request_sha256: str
    request_original_length: int
    request_used_length: int
    request_truncated: bool
    source_ranking: tuple[Path, ...] = ()
    test_ranking: tuple[Path, ...] = ()
    predicted_symbols: tuple[str, ...] = ()
    tool_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    runtime_seconds: float = Field(default=0.0, ge=0)
    malformed_outputs: int = Field(default=0, ge=0)
    dropped_predictions: tuple[str, ...] = ()
    report_path: Path | None = None
    trace_path: Path | None = None
    leak_checks: tuple[str, ...] = ()
    completed_at: datetime


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def atomic_json(path: Path, payload: Any, *, refuse_existing: bool = False) -> None:
    """Atomically persist JSON, optionally protecting successful checkpoints."""

    if refuse_existing and path.exists():
        raise EvaluationError(f"refusing to replace checkpoint: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, default=str)
            stream.write("\n")
        Path(name).replace(path)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise


def classify_failure(error: BaseException) -> FailureKind:
    text = f"{type(error).__name__}: {error}".casefold()
    if "401" in text or "authentication" in text or "auth_error" in text:
        return "authentication_failure"
    if "429" in text or "rate limit" in text or "ratelimit" in text:
        return "rate_limit"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(code in text for code in ("500", "502", "503", "504", "server error")):
        return "server_error"
    if "validation" in text or "malformed" in text or "json" in text:
        return "malformed_model_output"
    if isinstance(error, LLMError):
        return "other_provider_failure"
    return "controller_failure"


def build_schedule(
    task_ids: Sequence[str], seeds: Sequence[int] = FINAL_SEEDS
) -> tuple[ScheduleEntry, ...]:
    """Predeclare a deterministic interleaved schedule with rotated LLM order."""

    entries: list[ScheduleEntry] = []
    llm = list(LLM_SYSTEMS)
    for task_position, task_id in enumerate(sorted(task_ids)):
        for system in DETERMINISTIC_SYSTEMS:
            entries.append(
                ScheduleEntry(
                    run_id=f"{task_id}__{system}", task_id=task_id, system=system
                )
            )
        for seed_position, seed in enumerate(seeds):
            offset = (task_position + seed_position) % len(llm)
            for system in llm[offset:] + llm[:offset]:
                entries.append(
                    ScheduleEntry(
                        run_id=f"{task_id}__{system}__{seed}",
                        task_id=task_id,
                        system=system,
                        requested_seed=seed,
                    )
                )
    return tuple(entries)


def freeze_config(path: Path, *, model: str, base_url: str) -> dict[str, Any]:
    """Write the credential-free, content-addressed final configuration."""

    payload: dict[str, Any] = {
        "version": FINAL_CONFIG_VERSION,
        "provider": {"base_url": base_url, "model": model, "api_key": "excluded"},
        "provider_seed_control": False,
        "requested_seeds": list(FINAL_SEEDS),
        "bootstrap": {
            "seed": FINAL_BOOTSTRAP_SEED,
            "samples": FINAL_BOOTSTRAP_SAMPLES,
            "cluster": "repository",
        },
        "agent_config_version": FULL_REPORT_CONFIG_VERSION,
        "stage_b_config_version": STAGE_B_CONFIG_VERSION,
        "controller": {
            "tool_call_budget": MAX_TOOL_CALLS,
            "candidate_cap": MAX_CANDIDATES,
            "no_progress_limit": MAX_NO_PROGRESS,
            "report_validator": True,
        },
        "tools": [
            "search_code",
            "inspect_symbol",
            "find_references",
            "get_dependencies",
            "find_tests",
            "repo_facts",
            "co_changed",
        ],
        "systems": {
            "B0": "BM25",
            "B1": "BM25 plus dependency expansion",
            "B2": "co-change",
            "B3": "one-shot LLM",
            "B4": "plain ReAct",
            "RIPPLE": "full controller",
        },
        "ablations": {
            "A1": "RIPPLE without co_changed",
            "A2": "RIPPLE without get_dependencies and find_references",
            "A3": "RIPPLE without report validator",
        },
        "request_preparation": {
            "mask": "evaluation.sanitize_request",
            "maximum_characters": EVALUATION_REQUEST_LIMIT,
            "truncation": "deterministic prefix",
        },
        "ranking": "full deterministic ranking; LLM submitted/validated set",
        "scoring": {
            "deterministic_set_k": "matched to RIPPLE confirmed source count per seed",
            "k_zero": "preserved",
            "llm_seed_reduction": "mean within task before task aggregation",
        },
        "retry_policy": "OpenAILLM bounded: at most three attempts",
    }
    payload["config_hash"] = _canonical_hash(payload)
    atomic_json(path, payload)
    return payload


def _request_sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _raw_path(root: Path, run_id: str) -> Path:
    return root / f"{run_id}.json"


def _write_raw(path: Path, run: RawRun, *, force: bool) -> None:
    atomic_json(
        path,
        run.model_dump(mode="json"),
        refuse_existing=not force,
    )


def _run_one(
    entry: ScheduleEntry,
    task: EvaluationTask,
    repo: Path,
    leak_checks: tuple[str, ...],
    llm: LLMClient,
    raw_root: Path,
    artifact_root: Path,
    config_hash: str,
    *,
    force: bool,
    index: RepositoryIndex | None = None,
) -> RawRun:
    path = _raw_path(raw_root, entry.run_id)
    if path.exists() and not force:
        return RawRun.model_validate_json(path.read_text(encoding="utf-8"))
    prepared = prepare_evaluation_request(task.masked_request)
    request = FeatureRequest(text=prepared.text)
    common: dict[str, Any] = {
        "config_version": FINAL_CONFIG_VERSION,
        "config_hash": config_hash,
        "run_id": entry.run_id,
        "task_id": task.id,
        "repository": task.repository,
        "system": entry.system,
        "requested_seed": entry.requested_seed,
        "prepared_request_sha256": _request_sha(prepared.text),
        "request_original_length": prepared.original_length,
        "request_used_length": prepared.used_length,
        "request_truncated": prepared.truncated,
        "leak_checks": leak_checks,
        "completed_at": datetime.now(UTC),
    }
    started = perf_counter()
    try:
        index = index or scan_repository(repo)
        if entry.system in DETERMINISTIC_SYSTEMS:
            runner = {
                "B0": bm25_baseline,
                "B1": structural_baseline,
                "B2": cochange_baseline,
            }[entry.system]
            prediction = runner(index, prepared.text)
            run = RawRun(
                **common,
                status="completed",
                source_ranking=_dedupe_paths(
                    item.path for item in prediction.source_predictions
                ),
                test_ranking=_dedupe_paths(
                    item.path for item in prediction.test_predictions
                ),
                runtime_seconds=perf_counter() - started,
            )
        elif entry.system in {"B3", "B4"}:
            baseline = (
                one_shot_baseline(index, request, llm)
                if entry.system == "B3"
                else react_baseline(index, request, llm)
            )
            failure = "malformed_model_output" if baseline.malformed_outputs else None
            run = RawRun(
                **common,
                status="failed" if failure else "completed",
                failure_kind=failure,
                failure_message=(
                    f"{baseline.malformed_outputs} malformed structured output(s)"
                    if failure
                    else None
                ),
                source_ranking=_dedupe_paths(
                    item.path for item in baseline.prediction.source_predictions
                ),
                test_ranking=_dedupe_paths(
                    item.path for item in baseline.prediction.test_predictions
                ),
                model_calls=baseline.llm_calls,
                tool_calls=baseline.tool_calls,
                input_tokens=baseline.input_tokens,
                output_tokens=baseline.output_tokens,
                total_tokens=baseline.total_tokens,
                runtime_seconds=baseline.runtime_seconds,
                malformed_outputs=baseline.malformed_outputs,
                dropped_predictions=baseline.dropped_predictions,
            )
        else:
            agent = analyze_repository(
                index,
                request,
                llm,
                output_root=artifact_root / entry.run_id,
                variant=AGENT_VARIANTS[entry.system],
            )
            source_paths = {item.path for item in index.files if not item.is_test}
            source = _dedupe_paths(
                item.target.partition("::")[0]
                for item in agent.report.affected_components
                if Path(item.target.partition("::")[0]) in source_paths
            )
            symbols = tuple(
                dict.fromkeys(
                    item.target
                    for item in agent.report.affected_components
                    if "::" in item.target
                )
            )
            stats = agent.report.run_stats
            failure: FailureKind | None = (
                "controller_failure" if agent.report.status == "failed" else None
            )
            failure_message = stats.stop_reason if failure else None
            attempted_model_calls = stats.llm_calls
            if failure and agent.trace_path.exists():
                attempted_model_calls = 0
                for line in agent.trace_path.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    if event.get("event") == "llm_request":
                        attempted_model_calls += 1
                    if event.get("event") == "provider_error":
                        failure_message = str(event.get("error", "provider error"))
                        failure = classify_failure(LLMError(failure_message))
            run = RawRun(
                **common,
                status=(
                    "provider_failed"
                    if failure
                    and failure
                    in {
                        "authentication_failure",
                        "rate_limit",
                        "timeout",
                        "server_error",
                        "other_provider_failure",
                    }
                    else agent.report.status
                ),
                failure_kind=failure,
                failure_message=failure_message,
                source_ranking=source,
                test_ranking=_dedupe_paths(
                    item.test_path for item in agent.report.suggested_tests
                ),
                predicted_symbols=symbols,
                tool_calls=stats.tool_calls,
                model_calls=max(stats.llm_calls, attempted_model_calls),
                input_tokens=stats.input_tokens,
                output_tokens=stats.output_tokens,
                total_tokens=stats.total_tokens,
                runtime_seconds=stats.runtime_seconds,
                malformed_outputs=sum(
                    "invalid" in item.casefold() for item in agent.report.dropped_claims
                ),
                report_path=agent.report_path,
                trace_path=agent.trace_path,
            )
    except Exception as error:  # noqa: BLE001 - checkpoint provider failures
        run = RawRun(
            **common,
            status="provider_failed" if isinstance(error, LLMError) else "failed",
            failure_kind=classify_failure(error),
            failure_message=f"{type(error).__name__}: {error}",
            model_calls=1 if isinstance(error, LLMError) else 0,
            runtime_seconds=perf_counter() - started,
        )
    _write_raw(path, run, force=force)
    return run


def run_smoke(
    manifest_path: Path,
    *,
    workspace: Path,
    output_path: Path,
    llm: LLMClient,
    force: bool = False,
) -> dict[str, Any]:
    """Run exactly B3/B4/RIPPLE on five existing development tasks."""

    manifest = load_tasks(manifest_path)
    tasks = manifest.tasks[:5]
    if len(tasks) != 5:
        raise EvaluationError("compatibility smoke requires five development tasks")
    raw_root = output_path.parent / "smoke_raw"
    runs: list[RawRun] = []
    setup_failures: list[dict[str, str]] = []
    for task in tasks:
        try:
            repo, leak_checks = prepare_repository(task, workspace, history_depth=501)
        except Exception as error:  # noqa: BLE001 - smoke setup audit
            setup_failures.append({"task_id": task.id, "error": str(error)})
            continue
        for system in ("B3", "B4", "RIPPLE"):
            entry = ScheduleEntry(
                run_id=f"smoke__{task.id}__{system}",
                task_id=task.id,
                system=system,
                requested_seed=FINAL_SEEDS[0],
            )
            runs.append(
                _run_one(
                    entry,
                    task,
                    repo,
                    leak_checks,
                    llm,
                    raw_root,
                    workspace / "_artifacts",
                    "pre-freeze-smoke",
                    force=force,
                )
            )
    successful = [
        item
        for item in runs
        if item.status != "provider_failed" and not item.failure_kind
    ]
    totals = {
        "requests": sum(item.model_calls for item in runs),
        "input_tokens": sum(item.input_tokens or 0 for item in runs),
        "output_tokens": sum(item.output_tokens or 0 for item in runs),
        "total_tokens": sum(item.total_tokens or 0 for item in runs),
        "runtime_seconds": sum(item.runtime_seconds for item in runs),
    }
    per_task = {key: value / len(tasks) for key, value in totals.items()}
    # Full FEA estimate: 40 tasks * 3 seeds * 6 LLM configs. Smoke contains
    # 5 tasks * 3 configs, so scale observed total by 48.
    scale = (40 * len(FINAL_SEEDS) * len(LLM_SYSTEMS)) / (5 * 3)
    estimate = {key: value * scale for key, value in totals.items()}
    payload = {
        "label": "DEVELOPMENT TASK PROVIDER COMPATIBILITY SMOKE",
        "created_at": datetime.now(UTC).isoformat(),
        "model": llm.model,
        "task_manifest": str(manifest_path),
        "task_ids": [item.id for item in tasks],
        "systems": ["B3", "B4", "RIPPLE"],
        "requested_seed": FINAL_SEEDS[0],
        "provider_seed_control": False,
        "setup_failures": setup_failures,
        "runs": [item.model_dump(mode="json") for item in runs],
        "successful_runs": len(successful),
        "required_runs": 15,
        "totals": totals,
        "means_per_development_task": per_task,
        "estimated_full_40_task_llm_workload": estimate,
        "estimate_scale": scale,
        "dollar_cost": None,
        "dollar_cost_note": "No explicit BullsAI per-token user price configured.",
    }
    atomic_json(output_path, payload)
    return payload


def run_final(
    manifest_path: Path,
    *,
    workspace: Path,
    raw_root: Path,
    schedule_path: Path,
    config_path: Path,
    llm: LLMClient,
    force: bool = False,
) -> tuple[RawRun, ...]:
    """Execute or resume the frozen final schedule, checkpointing every run."""

    config = json.loads(config_path.read_text(encoding="utf-8"))
    expected_hash = config.pop("config_hash")
    if _canonical_hash(config) != expected_hash:
        raise EvaluationError("final configuration hash mismatch")
    manifest = load_tasks(manifest_path)
    tasks = {task.id: task for task in manifest.tasks}
    schedule = build_schedule(tuple(tasks))
    if schedule_path.exists():
        frozen = tuple(
            ScheduleEntry.model_validate(item)
            for item in json.loads(schedule_path.read_text(encoding="utf-8"))["runs"]
        )
        if frozen != schedule:
            raise EvaluationError("frozen run schedule does not match manifest")
    else:
        atomic_json(
            schedule_path,
            {
                "config_hash": expected_hash,
                "runs": [item.model_dump(mode="json") for item in schedule],
            },
        )
    prepared_repos: dict[str, tuple[Path, tuple[str, ...]]] = {}
    # The index is deterministic and frozen; build it once per task, not per run.
    indexes: dict[str, RepositoryIndex] = {}
    results: list[RawRun] = []
    for entry in schedule:
        checkpoint = _raw_path(raw_root, entry.run_id)
        if checkpoint.exists() and not force:
            results.append(
                RawRun.model_validate_json(checkpoint.read_text(encoding="utf-8"))
            )
            continue
        task = tasks[entry.task_id]
        if task.id not in prepared_repos:
            prepared_repos[task.id] = prepare_repository(
                task, workspace, history_depth=501
            )
        repo, checks = prepared_repos[task.id]
        if task.id not in indexes:
            indexes.clear()
            indexes[task.id] = scan_repository(repo)
        results.append(
            _run_one(
                entry,
                task,
                repo,
                checks,
                llm,
                raw_root,
                workspace / "_artifacts",
                expected_hash,
                force=force,
                index=indexes[task.id],
            )
        )
    return tuple(results)


def validate_final_selection(
    tasks: Iterable[EvaluationTask], development_ids: set[str]
) -> dict[str, Any]:
    selected = tuple(tasks)
    overlap = sorted(item.id for item in selected if item.id in development_ids)
    counts = Counter(item.repository for item in selected)
    if overlap:
        raise EvaluationError(f"final manifest overlaps development tasks: {overlap}")
    if any(count > 5 for count in counts.values()):
        raise EvaluationError("final manifest exceeds five tasks per repository")
    strata = Counter(item.selection_bucket for item in selected)
    return {
        "tasks": len(selected),
        "repositories": len(counts),
        "per_repository": dict(sorted(counts.items())),
        "strata": dict(sorted(strata.items())),
    }
