"""Phase 7 manifest selection and post-prediction ground-truth derivation."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ripple.diffing import parse_diff
from ripple.evaluation import (
    EvaluationError,
    EvaluationManifest,
    EvaluationTask,
    classify_gold_files,
    sanitize_request,
)
from ripple.final_evaluation import atomic_json, validate_final_selection


class RecentPRTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    repository: str
    repository_url: str
    pull_number: int = Field(gt=0)
    base_commit: str
    head_commit: str
    request: str
    masked_request: str
    merged_at: date
    issue_url: str
    pull_url: str
    source_provenance: str
    qualification: str
    set_kind: Literal["post_cutoff_contamination", "recent_pr_robustness"]

    @model_validator(mode="after")
    def _request_is_masked(self) -> RecentPRTask:
        if self.masked_request != sanitize_request(self.request):
            raise ValueError("recent PR masked request is not deterministic")
        return self


class RecentPRManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "final-v1"
    model: str
    cutoff_source: str | None
    documented_cutoff: date | None
    set_kind: Literal["post_cutoff_contamination", "recent_pr_robustness"]
    tasks: tuple[RecentPRTask, ...]

    @model_validator(mode="after")
    def _consistent(self) -> RecentPRManifest:
        if self.set_kind == "post_cutoff_contamination" and (
            self.documented_cutoff is None or not self.cutoff_source
        ):
            raise ValueError(
                "strict contamination set requires an authoritative cutoff"
            )
        if any(task.set_kind != self.set_kind for task in self.tasks):
            raise ValueError("recent PR task kind differs from manifest")
        if self.documented_cutoff and any(
            task.merged_at <= self.documented_cutoff for task in self.tasks
        ):
            raise ValueError("a strict recent PR task is not after the cutoff")
        counts = Counter(task.repository for task in self.tasks)
        if not 3 <= len(counts) <= 5:
            raise ValueError("recent PR set must use three to five repositories")
        return self


def select_final_tasks(
    candidates: tuple[EvaluationTask, ...],
    *,
    development_ids: set[str],
    target: int = 40,
    per_repository: int = 5,
) -> tuple[EvaluationTask, ...]:
    """Deterministically stratify eligible tasks without inspecting request quality."""

    eligible = [item for item in candidates if item.id not in development_ids]
    by_bucket: dict[str, list[EvaluationTask]] = defaultdict(list)
    for task in sorted(eligible, key=lambda item: (item.repository, item.id)):
        by_bucket[task.selection_bucket].append(task)
    chosen: list[EvaluationTask] = []
    repo_counts: Counter[str] = Counter()
    buckets = ("2-4", "5-9", "10-20")
    positions = {name: 0 for name in buckets}
    while len(chosen) < min(target, len(eligible)):
        progressed = False
        for name in buckets:
            items = by_bucket[name]
            while positions[name] < len(items):
                task = items[positions[name]]
                positions[name] += 1
                if repo_counts[task.repository] >= per_repository:
                    continue
                chosen.append(task)
                repo_counts[task.repository] += 1
                progressed = True
                break
            if len(chosen) >= target:
                break
        if not progressed:
            break
    selected = tuple(sorted(chosen, key=lambda item: item.id))
    validate_final_selection(selected, development_ids)
    return selected


def build_final_manifest(
    candidate_manifest: EvaluationManifest,
    development_manifests: tuple[EvaluationManifest, ...],
    output_path: Path,
    *,
    target: int = 40,
) -> dict[str, Any]:
    development_ids = {
        task.id for manifest in development_manifests for task in manifest.tasks
    }
    tasks = select_final_tasks(
        candidate_manifest.tasks,
        development_ids=development_ids,
        target=target,
    )
    manifest = EvaluationManifest(
        benchmark_source=candidate_manifest.benchmark_source,
        selection_method=(
            "final-v1 deterministic round-robin across 2-4, 5-9, and 10-20 "
            "source-file strata; development exclusions; maximum five per repository"
        ),
        tasks=tasks,
    )
    atomic_json(output_path, manifest.model_dump(mode="json"))
    return validate_final_selection(tasks, development_ids)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise EvaluationError(result.stderr.strip() or "Git command failed")
    return result.stdout


def derive_symbol_gold(
    tasks: tuple[EvaluationTask, ...],
    *,
    raw_root: Path,
    workspace: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Fetch heads only after every prediction checkpoint exists, then map symbols."""

    expected_system_runs = 3 + 6 * 3
    for task in tasks:
        count = len(list(raw_root.glob(f"{task.id}__*.json")))
        if count != expected_system_runs:
            raise EvaluationError(
                f"refusing gold access before predictions: {task.id} has "
                f"{count}/{expected_system_runs} checkpoints"
            )
    workspace.mkdir(parents=True, exist_ok=True)
    output: dict[str, Any] = {}
    for task in tasks:
        repo = Path(tempfile.mkdtemp(prefix=f"gold-{task.id}-", dir=workspace))
        try:
            _git(repo, "init", "--quiet")
            _git(repo, "remote", "add", "origin", task.repository_url)
            _git(repo, "fetch", "--quiet", "--no-tags", "origin", task.base_commit)
            _git(repo, "fetch", "--quiet", "--no-tags", "origin", task.head_commit)
            snapshot = parse_diff(repo, task.base_commit, task.head_commit)
            paths = tuple(Path(item.change.path) for item in snapshot.files)
            groups = classify_gold_files(paths)
            if groups != task.gold_files:
                raise EvaluationError(f"manifest gold mismatch for {task.id}")
            symbols: list[str] = []
            unmappable = 0
            gold_only_names: set[str] = set()
            for item in snapshot.files:
                if item.change.parse_error:
                    unmappable += len(item.hunks)
                    continue
                symbols.extend(
                    f"{item.change.path}::{name}"
                    for name in item.change.changed_symbols
                )
                if item.old_source is None:
                    gold_only_names.update(
                        name.rsplit(".", 1)[-1] for name in item.change.changed_symbols
                    )
            output[task.id] = {
                "symbols": sorted(set(symbols)),
                "unmappable_hunks": unmappable,
                "gold_only_symbol_names": sorted(gold_only_names),
                "files": groups.model_dump(mode="json"),
            }
        finally:
            shutil.rmtree(repo, ignore_errors=True)
    atomic_json(output_path, output)
    return output


def audit_gold_symbol_leaks(
    tasks: tuple[EvaluationTask, ...],
    symbol_gold: dict[str, Any],
    *,
    prediction_workspace: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Post-prediction audit for names that exist only in the future diff."""

    audits: dict[str, Any] = {}
    for task in tasks:
        names = symbol_gold.get(task.id, {}).get("gold_only_symbol_names", [])
        repo = prediction_workspace.resolve() / task.id
        leaks: list[dict[str, str]] = []
        for name in names:
            if name.casefold() in task.masked_request.casefold():
                leaks.append({"symbol": name, "location": "prepared_request"})
                continue
            result = subprocess.run(
                ["rg", "--files-with-matches", "--word-regexp", name, str(repo)],
                capture_output=True,
                text=True,
                check=False,
            )
            for path in result.stdout.splitlines():
                leaks.append({"symbol": name, "location": path})
        audits[task.id] = {
            "checked_gold_only_symbols": len(names),
            "status": "failed" if leaks else "passed",
            "leaks": leaks,
        }
    atomic_json(output_path, audits)
    if any(item["status"] == "failed" for item in audits.values()):
        raise EvaluationError(
            "gold-only symbol leak detected; affected tasks are setup failures"
        )
    return audits
