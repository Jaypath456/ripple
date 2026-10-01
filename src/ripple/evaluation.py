"""Leak-safe deterministic evaluation over historical feature requests."""

import json
import os
import random
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from statistics import fmean
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ripple.agent import analyze_repository
from ripple.agent_models import (
    AGENT_CONFIG_VERSION,
    FULL_REPORT_CONFIG_VERSION,
    FeatureRequest,
    RunStatus,
)
from ripple.baselines import (
    DEFAULT_PREDICTION_K,
    BaselinePrediction,
    bm25_baseline,
    cochange_baseline,
    structural_baseline,
)
from ripple.llm import LLMClient
from ripple.phase5_baselines import one_shot_baseline, react_baseline
from ripple.scanner import is_test_file, scan_repository

EVALUATION_SCHEMA_VERSION = 1
EVALUATION_REQUEST_LIMIT = 2000
DEFAULT_BOOTSTRAP_SAMPLES = 1000
DEFAULT_BOOTSTRAP_SEED = 1729
DEFAULT_TASK_COUNT = 10

_SHA = re.compile(r"[0-9a-f]{40}")
_TASK_ID = re.compile(r"[A-Za-z0-9_.-]+")
_FILE_PATH = re.compile(
    r"(?<![\w@:/.])(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+"
    r"\.(?:py|md|rst|json|ya?ml|toml)\b"
)
_SLASH_PATH = re.compile(
    r"(?<![\w@:/.])(?:\.?\.?[\\/])?(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+"
)
_DOTTED_MODULE = re.compile(r"(?<![\w@/.])(?:[A-Za-z_]\w*\.){2,}[A-Za-z_]\w*(?![\w/])")


class EvaluationError(ValueError):
    """An evaluation input or setup is invalid."""


class GoldFileGroups(BaseModel):
    """Changed paths separated by the scoring taxonomy."""

    model_config = ConfigDict(frozen=True)

    source_python: tuple[Path, ...]
    test_python: tuple[Path, ...]
    other_implementation: tuple[Path, ...]
    ignored: tuple[Path, ...]


class EvaluationTask(BaseModel):
    """A fixed FEA-Bench task reconstructed from its public pull request."""

    model_config = ConfigDict(frozen=True)

    id: str
    repository: str
    repository_url: str
    pull_number: int = Field(gt=0)
    base_commit: str
    head_commit: str
    original_request: str
    masked_request: str
    gold_changed_files: tuple[Path, ...]
    gold_files: GoldFileGroups
    benchmark_source: str
    benchmark_split: Literal["lite"] = "lite"
    selection_bucket: Literal["2-4", "5-9", "10-20"]

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _TASK_ID.fullmatch(value):
            raise ValueError(
                "task ID must contain only letters, digits, dot, dash, underscore"
            )
        return value

    @field_validator("repository")
    @classmethod
    def _valid_repository(cls, value: str) -> str:
        parts = value.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("repository must be owner/name")
        return value

    @field_validator("base_commit", "head_commit")
    @classmethod
    def _valid_sha(cls, value: str) -> str:
        if not _SHA.fullmatch(value):
            raise ValueError("commit must be a full lowercase SHA-1")
        return value

    @field_validator("original_request", "masked_request", "benchmark_source")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text fields must not be blank")
        return value

    @field_validator("gold_changed_files")
    @classmethod
    def _valid_paths(cls, paths: tuple[Path, ...]) -> tuple[Path, ...]:
        normalized = tuple(_relative_path(path) for path in paths)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("gold paths must be non-empty and unique")
        return normalized

    @model_validator(mode="after")
    def _consistent(self) -> "EvaluationTask":
        if self.repository_url != f"https://github.com/{self.repository}.git":
            raise ValueError("repository_url must be the canonical GitHub clone URL")
        if self.masked_request != sanitize_request(self.original_request):
            raise ValueError(
                "masked_request does not match the deterministic sanitizer"
            )
        expected = classify_gold_files(self.gold_changed_files)
        if self.gold_files != expected:
            raise ValueError(
                "gold_files does not match gold_changed_files classification"
            )
        count = len(self.gold_files.source_python)
        expected_bucket = "2-4" if count <= 4 else "5-9" if count <= 9 else "10-20"
        if count < 2 or count > 20 or self.selection_bucket != expected_bucket:
            raise ValueError("selection bucket must describe 2-20 source Python files")
        return self


class EvaluationManifest(BaseModel):
    """Versioned fixed development-task list."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = EVALUATION_SCHEMA_VERSION
    benchmark_source: str
    selection_method: str
    tasks: tuple[EvaluationTask, ...]

    @model_validator(mode="after")
    def _unique(self) -> "EvaluationManifest":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task IDs must be unique")
        if self.schema_version != EVALUATION_SCHEMA_VERSION:
            raise ValueError("unsupported evaluation manifest schema")
        return self


class SetMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    precision: float
    recall: float
    f1: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    false_positives: int


class BaselineTaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    repository: str
    base_commit: str
    masked_request: str
    prediction: BaselinePrediction
    gold_files: GoldFileGroups
    metrics: SetMetrics
    test_precision: float
    test_recall: float
    runtime_seconds: float
    setup_status: Literal["passed"] = "passed"
    leak_checks: tuple[str, ...]


class ConfidenceInterval(BaseModel):
    model_config = ConfigDict(frozen=True)

    low: float
    high: float


class AggregateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    baseline: Literal["B0", "B1"]
    task_count: int
    mean_precision: float
    mean_recall: float
    mean_f1: float
    mean_recall_at_5: float
    mean_recall_at_10: float
    mean_mrr: float
    mean_false_positives: float
    mean_test_precision: float | None
    mean_test_recall: float | None
    mean_runtime_seconds: float
    confidence_intervals: dict[str, ConfidenceInterval]


class EvaluationResults(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = EVALUATION_SCHEMA_VERSION
    created_at: datetime
    task_manifest: str
    task_ids: tuple[str, ...]
    baseline_configuration: dict[str, Any]
    prediction_cutoff: int
    bootstrap_seed: int
    bootstrap_samples: int
    ripple_git_commit: str | None
    ripple_git_dirty: bool | None
    total_tasks: int
    valid_tasks: int
    skipped_tasks: tuple[dict[str, str], ...]
    failed_setup_tasks: tuple[dict[str, str], ...]
    per_task: tuple[BaselineTaskResult, ...]
    aggregates: tuple[AggregateResult, ...]
    ground_truth_limitation: str


class MVPSystemResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: Literal["B0", "B1", "RIPPLE"]
    source_ranking: tuple[Path, ...]
    test_ranking: tuple[Path, ...]
    metrics: SetMetrics
    test_precision: float
    test_recall: float
    runtime_seconds: float


class PreparedEvaluationRequest(BaseModel):
    """One deterministic benchmark input shared by every evaluated system."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(max_length=EVALUATION_REQUEST_LIMIT)
    original_length: int = Field(ge=0)
    used_length: int = Field(ge=0, le=EVALUATION_REQUEST_LIMIT)
    truncated: bool

    @model_validator(mode="after")
    def _consistent_lengths(self) -> "PreparedEvaluationRequest":
        if self.used_length != len(self.text):
            raise ValueError("used length must match prepared request text")
        if self.truncated != (self.original_length > self.used_length):
            raise ValueError("truncation flag must match request lengths")
        return self


class MVPTaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    repository: str
    base_commit: str
    masked_request: str
    masked_request_original_length: int = Field(ge=0)
    masked_request_used_length: int = Field(ge=0, le=EVALUATION_REQUEST_LIMIT)
    masked_request_truncated: bool
    gold_files: GoldFileGroups
    agent_k: int = Field(ge=0)
    systems: tuple[MVPSystemResult, ...]
    agent_status: RunStatus
    agent_tool_calls: int
    agent_llm_calls: int
    agent_total_tokens: int | None
    agent_dropped_claims: int
    agent_stop_reason: str
    agent_report_path: Path
    agent_trace_path: Path
    leak_checks: tuple[str, ...]


class MVPAggregateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: Literal["B0", "B1", "RIPPLE"]
    task_count: int
    mean_precision: float
    mean_recall: float
    mean_f1: float
    mean_recall_at_5: float
    mean_recall_at_10: float
    mean_mrr: float
    mean_false_positives: float
    mean_test_precision: float
    mean_test_recall: float
    mean_runtime_seconds: float


class MVPEvaluationResults(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = EVALUATION_SCHEMA_VERSION
    created_at: datetime
    task_manifest: str
    task_ids: tuple[str, ...]
    agent_config_version: str
    request_preparation: str
    llm_model: str
    fair_cutoff: str
    total_tasks: int
    valid_tasks: int
    failed_setup_tasks: tuple[dict[str, str], ...]
    status_counts: dict[str, int]
    per_task: tuple[MVPTaskResult, ...]
    aggregates: tuple[MVPAggregateResult, ...]
    ground_truth_limitation: str


Phase5SystemName = Literal["B0", "B1", "B2", "B3", "B4", "RIPPLE"]


class Phase5SystemResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: Phase5SystemName
    source_ranking: tuple[Path, ...]
    test_ranking: tuple[Path, ...]
    metrics: SetMetrics
    test_precision: float
    test_recall: float
    runtime_seconds: float
    dropped_predictions: tuple[str, ...] = ()
    llm_calls: int = 0
    tool_calls: int = 0


class Phase5TaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    repository: str
    base_commit: str
    masked_request_original_length: int
    masked_request_used_length: int
    masked_request_truncated: bool
    agent_k: int
    systems: tuple[Phase5SystemResult, ...]
    agent_status: RunStatus
    leak_checks: tuple[str, ...]


class Phase5AggregateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: Phase5SystemName
    task_count: int
    mean_precision: float
    mean_recall: float
    mean_f1: float
    mean_recall_at_5: float
    mean_recall_at_10: float
    mean_mrr: float
    mean_false_positives: float


class Phase5EvaluationResults(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = EVALUATION_SCHEMA_VERSION
    label: Literal["DEVELOPMENT / PHASE 5 — NOT FINAL HELD-OUT RESULTS"]
    created_at: datetime
    task_manifest: str
    agent_config_version: str
    llm_model: str
    history_policy: str
    total_tasks: int
    valid_tasks: int
    failed_setup_tasks: tuple[dict[str, str], ...]
    status_counts: dict[str, int]
    per_task: tuple[Phase5TaskResult, ...]
    aggregates: tuple[Phase5AggregateResult, ...]
    ground_truth_limitation: str


def _relative_path(path: str | Path) -> Path:
    candidate = Path(PurePosixPath(str(path).replace("\\", "/")))
    if candidate.is_absolute() or ".." in candidate.parts or candidate == Path("."):
        raise ValueError(f"path must be safe and repository-relative: {path}")
    return candidate


def sanitize_request(request: str) -> str:
    """Mask explicit paths while preserving ordinary feature prose."""

    masked = _FILE_PATH.sub("[PATH]", request)
    masked = _SLASH_PATH.sub("[PATH]", masked)
    masked = _DOTTED_MODULE.sub("[PATH]", masked)
    masked = re.sub(
        r"\b(from|import)\s+[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+",
        lambda match: f"{match.group(1)} [PATH]",
        masked,
    )
    return masked


def _ignored_gold_path(path: Path) -> bool:
    lower = path.as_posix().lower()
    name = path.name.lower()
    parts = {part.lower() for part in path.parts[:-1]}
    return (
        bool(parts & {"doc", "docs", "documentation", "changelog", "news"})
        or name.startswith(("readme", "changelog", "changes", "authors"))
        or name in {"poetry.lock", "pdm.lock", "pipfile.lock", "uv.lock"}
        or name.endswith((".lock", ".md", ".rst", "_pb2.py", "_pb2_grpc.py", ".min.js"))
        or ".generated." in name
        or lower.startswith(".github/")
    )


def classify_gold_files(paths: Iterable[str | Path]) -> GoldFileGroups:
    """Apply RIPPLE's compact ground-truth file taxonomy."""

    source: list[Path] = []
    tests: list[Path] = []
    other: list[Path] = []
    ignored: list[Path] = []
    for raw_path in sorted({_relative_path(path) for path in paths}):
        if _ignored_gold_path(raw_path):
            ignored.append(raw_path)
        elif raw_path.suffix == ".py" and is_test_file(raw_path):
            tests.append(raw_path)
        elif raw_path.suffix == ".py":
            source.append(raw_path)
        else:
            other.append(raw_path)
    return GoldFileGroups(
        source_python=tuple(source),
        test_python=tuple(tests),
        other_implementation=tuple(other),
        ignored=tuple(ignored),
    )


def extract_changed_files(diff_text: str) -> tuple[Path, ...]:
    """Extract stable destination paths from a GitHub unified PR diff."""

    paths: list[Path] = []
    for line in diff_text.splitlines():
        if not line.startswith("diff --git a/"):
            continue
        match = re.match(r"diff --git a/(.+) b/(.+)$", line)
        if match is None or '"' in line:
            raise EvaluationError("quoted/ambiguous diff paths are unsupported")
        path = _relative_path(match.group(2))
        if path not in paths:
            paths.append(path)
    if not paths:
        raise EvaluationError("PR diff contains no changed files")
    return tuple(paths)


def build_fea_bench_task(
    benchmark_record: dict[str, Any],
    pull_request: dict[str, Any],
    diff_text: str,
    *,
    benchmark_source: str,
) -> EvaluationTask:
    """Ingest an official lightweight row plus its public GitHub PR data."""

    required = {"instance_id", "repo", "pull_number", "base_commit"}
    missing = sorted(required - benchmark_record.keys())
    if missing:
        raise EvaluationError(f"FEA-Bench record missing fields: {', '.join(missing)}")
    if pull_request.get("number") != benchmark_record["pull_number"]:
        raise EvaluationError("GitHub pull number does not match FEA-Bench")
    if pull_request.get("base", {}).get("sha") != benchmark_record["base_commit"]:
        raise EvaluationError("GitHub pull base does not match FEA-Bench")
    if pull_request.get("merged") is not True:
        raise EvaluationError("FEA-Bench pull request is not merged")
    title = str(pull_request.get("title") or "").strip()
    body = str(pull_request.get("body") or "").strip()
    request = "\n\n".join(part for part in (title, body) if part)
    head = pull_request.get("head", {}).get("sha")
    paths = extract_changed_files(diff_text)
    groups = classify_gold_files(paths)
    count = len(groups.source_python)
    bucket = "2-4" if count <= 4 else "5-9" if count <= 9 else "10-20"
    return EvaluationTask(
        id=benchmark_record["instance_id"],
        repository=benchmark_record["repo"],
        repository_url=f"https://github.com/{benchmark_record['repo']}.git",
        pull_number=benchmark_record["pull_number"],
        base_commit=benchmark_record["base_commit"],
        head_commit=head,
        original_request=request,
        masked_request=sanitize_request(request),
        gold_changed_files=paths,
        gold_files=groups,
        benchmark_source=benchmark_source,
        selection_bucket=bucket,
    )


def load_tasks(
    path: str | Path, *, expected_count: int | None = None
) -> EvaluationManifest:
    """Load and validate a versioned task manifest."""

    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = EvaluationManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise EvaluationError(
            f"invalid task manifest {manifest_path}: {error}"
        ) from error
    if expected_count is not None and len(manifest.tasks) != expected_count:
        raise EvaluationError(
            f"task manifest must contain exactly {expected_count} tasks; "
            f"found {len(manifest.tasks)}"
        )
    return manifest


def _git(
    repo: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        raise EvaluationError(result.stderr.strip() or "Git command failed")
    return result


def check_repository_leaks(repo: Path, task: EvaluationTask) -> tuple[str, ...]:
    """Fail closed if future/gold state is visible from the prediction checkout."""

    checks: list[str] = []
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != task.base_commit:
        raise EvaluationError(f"wrong HEAD: expected {task.base_commit}, found {head}")
    checks.append("head_is_exact_base")

    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    if status:
        raise EvaluationError("evaluation checkout is not clean")
    checks.append("working_tree_clean")

    refs = _git(repo, "for-each-ref", "--format=%(refname)").stdout.splitlines()
    forbidden = (f"pull/{task.pull_number}", task.head_commit)
    if any(any(value in ref for value in forbidden) for ref in refs):
        raise EvaluationError("target pull/head ref is retained")
    head_exists = _git(
        repo, "cat-file", "-e", f"{task.head_commit}^{{commit}}", check=False
    )
    if head_exists.returncode == 0:
        containing = _git(
            repo, "for-each-ref", "--contains", task.head_commit, "--format=%(refname)"
        ).stdout.splitlines()
        if containing:
            raise EvaluationError(f"target commit reachable through refs: {containing}")
    checks.append("target_head_not_reachable")

    for path in task.gold_changed_files:
        at_base = _git(
            repo,
            "cat-file",
            "-e",
            f"{task.base_commit}:{path.as_posix()}",
            check=False,
        )
        if at_base.returncode and (repo / path).exists():
            raise EvaluationError(f"gold-only path is visible: {path}")
    checks.append("gold_only_paths_absent")

    lowered_request = task.masked_request.casefold()
    for path in task.gold_changed_files:
        path_text = path.as_posix().casefold()
        if path_text in lowered_request or path.name.casefold() in lowered_request:
            raise EvaluationError(f"gold location leaked into masked request: {path}")
    checks.append("request_has_no_gold_paths")
    return tuple(checks)


def prepare_repository(
    task: EvaluationTask, workspace: str | Path, *, history_depth: int = 1
) -> tuple[Path, tuple[str, ...]]:
    """Create a fresh base-only clone and remove all remote/future refs."""

    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = (root / task.id).resolve()
    if destination.parent != root:
        raise EvaluationError("task checkout escaped evaluation workspace")
    if destination.exists():
        shutil.rmtree(destination)
    temporary = Path(tempfile.mkdtemp(prefix=f".{task.id}-", dir=root))
    try:
        _git(temporary, "init", "--quiet")
        _git(temporary, "remote", "add", "origin", task.repository_url)
        _git(
            temporary,
            "fetch",
            "--quiet",
            f"--depth={history_depth}",
            "--no-tags",
            "origin",
            task.base_commit,
        )
        _git(temporary, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        _git(temporary, "remote", "remove", "origin")
        for ref in _git(
            temporary, "for-each-ref", "--format=%(refname)"
        ).stdout.splitlines():
            _git(temporary, "update-ref", "-d", ref)
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination, check_repository_leaks(destination, task)


def score_ranking(
    ranking: Sequence[str | Path],
    gold: Iterable[str | Path],
    *,
    prediction_k: int = DEFAULT_PREDICTION_K,
) -> SetMetrics:
    """Compute per-task set and ranked source-file metrics."""

    ranked = tuple(_relative_path(path) for path in ranking)
    gold_set = {_relative_path(path) for path in gold}
    predicted = set(ranked[:prediction_k])
    true_positives = len(predicted & gold_set)
    precision = true_positives / len(predicted) if predicted else 0.0
    recall = true_positives / len(gold_set) if gold_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    def recall_at(cutoff: int) -> float:
        return len(set(ranked[:cutoff]) & gold_set) / len(gold_set) if gold_set else 0.0

    first_rank = next(
        (rank for rank, path in enumerate(ranked, start=1) if path in gold_set), None
    )
    return SetMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        recall_at_5=recall_at(5),
        recall_at_10=recall_at(10),
        mrr=1.0 / first_rank if first_rank is not None else 0.0,
        false_positives=len(predicted - gold_set),
    )


def test_set_metrics(
    predictions: Sequence[str | Path], gold: Iterable[str | Path], *, cutoff: int
) -> tuple[float, float]:
    predicted = {_relative_path(path) for path in predictions[:cutoff]}
    gold_set = {_relative_path(path) for path in gold}
    overlap = len(predicted & gold_set)
    return (
        overlap / len(predicted) if predicted else 0.0,
        overlap / len(gold_set) if gold_set else 0.0,
    )


_AGGREGATE_FIELDS = {
    "mean_precision": lambda item: item.metrics.precision,
    "mean_recall": lambda item: item.metrics.recall,
    "mean_f1": lambda item: item.metrics.f1,
    "mean_recall_at_5": lambda item: item.metrics.recall_at_5,
    "mean_recall_at_10": lambda item: item.metrics.recall_at_10,
    "mean_mrr": lambda item: item.metrics.mrr,
    "mean_false_positives": lambda item: float(item.metrics.false_positives),
    "mean_runtime_seconds": lambda item: item.runtime_seconds,
}


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def cluster_bootstrap(
    results: Sequence[BaselineTaskResult],
    *,
    samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    _accessors: dict[str, Any] | None = None,
) -> dict[str, ConfidenceInterval]:
    """Bootstrap task means by sampling repository clusters with replacement."""

    if not results or samples < 1:
        raise EvaluationError("bootstrap requires results and at least one sample")
    grouped: dict[str, list[BaselineTaskResult]] = defaultdict(list)
    for result in results:
        grouped[result.repository].append(result)
    repositories = sorted(grouped)
    generator = random.Random(seed)
    distributions: dict[str, list[float]] = defaultdict(list)
    accessors = _accessors or _AGGREGATE_FIELDS
    for _ in range(samples):
        sampled: list[BaselineTaskResult] = []
        for _ in repositories:
            sampled.extend(grouped[generator.choice(repositories)])
        for name, accessor in accessors.items():
            distributions[name].append(fmean(accessor(item) for item in sampled))
    return {
        name: ConfidenceInterval(
            low=_percentile(values, 0.025), high=_percentile(values, 0.975)
        )
        for name, values in distributions.items()
    }


def aggregate_results(
    baseline: Literal["B0", "B1"],
    results: Sequence[BaselineTaskResult],
    *,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> AggregateResult:
    selected = [item for item in results if item.prediction.baseline == baseline]
    if not selected:
        raise EvaluationError(f"no results for {baseline}")
    values = {
        name: fmean(accessor(item) for item in selected)
        for name, accessor in _AGGREGATE_FIELDS.items()
    }
    with_test_predictions = [
        item for item in selected if item.prediction.test_predictions
    ]
    intervals = cluster_bootstrap(
        selected, samples=bootstrap_samples, seed=bootstrap_seed
    )
    if with_test_predictions:
        intervals.update(
            cluster_bootstrap(
                with_test_predictions,
                samples=bootstrap_samples,
                seed=bootstrap_seed,
                _accessors={
                    "mean_test_precision": lambda item: item.test_precision,
                    "mean_test_recall": lambda item: item.test_recall,
                },
            )
        )
    return AggregateResult(
        baseline=baseline,
        task_count=len(selected),
        **values,
        mean_test_precision=(
            fmean(item.test_precision for item in with_test_predictions)
            if with_test_predictions
            else None
        ),
        mean_test_recall=(
            fmean(item.test_recall for item in with_test_predictions)
            if with_test_predictions
            else None
        ),
        confidence_intervals=intervals,
    )


def _repository_version(repo: Path) -> tuple[str | None, bool | None]:
    try:
        commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
        dirty = bool(_git(repo, "status", "--porcelain=v1").stdout)
    except EvaluationError:
        return None, None
    return commit, dirty


def evaluate_manifest(
    manifest_path: str | Path,
    *,
    workspace: str | Path,
    output_path: str | Path,
    prediction_k: int = DEFAULT_PREDICTION_K,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    verbose: bool = False,
) -> EvaluationResults:
    """Prepare, scan, run, score, aggregate, and persist a fixed dev evaluation."""

    manifest = load_tasks(manifest_path, expected_count=DEFAULT_TASK_COUNT)
    per_task: list[BaselineTaskResult] = []
    failures: list[dict[str, str]] = []
    for task in manifest.tasks:
        try:
            repo, leak_checks = prepare_repository(task, workspace)
            index = scan_repository(repo)
            task_results: list[BaselineTaskResult] = []
            for runner in (bm25_baseline, structural_baseline):
                started = perf_counter()
                prediction = runner(index, task.masked_request)
                runtime = perf_counter() - started
                metrics = score_ranking(
                    [item.path for item in prediction.source_predictions],
                    task.gold_files.source_python,
                    prediction_k=prediction_k,
                )
                test_precision, test_recall = test_set_metrics(
                    [item.path for item in prediction.test_predictions],
                    task.gold_files.test_python,
                    cutoff=prediction_k,
                )
                task_results.append(
                    BaselineTaskResult(
                        task_id=task.id,
                        repository=task.repository,
                        base_commit=task.base_commit,
                        masked_request=task.masked_request,
                        prediction=prediction,
                        gold_files=task.gold_files,
                        metrics=metrics,
                        test_precision=test_precision,
                        test_recall=test_recall,
                        runtime_seconds=runtime,
                        leak_checks=leak_checks,
                    )
                )
            per_task.extend(task_results)
            if verbose:
                print(f"evaluated {task.id}")
        except (EvaluationError, OSError, ValueError) as error:
            failures.append({"task_id": task.id, "reason": str(error)})
            if verbose:
                print(f"failed {task.id}: {error}")

    valid_ids = {item.task_id for item in per_task if item.prediction.baseline == "B0"}
    if not valid_ids:
        raise EvaluationError("no tasks completed evaluation")
    aggregates = tuple(
        aggregate_results(
            baseline,
            per_task,
            bootstrap_samples=bootstrap_samples,
            bootstrap_seed=bootstrap_seed,
        )
        for baseline in ("B0", "B1")
    )
    project_root = Path(__file__).resolve().parents[2]
    git_commit, git_dirty = _repository_version(project_root)
    results = EvaluationResults(
        created_at=datetime.now(UTC),
        task_manifest=str(Path(manifest_path)),
        task_ids=tuple(task.id for task in manifest.tasks),
        baseline_configuration={
            "B0": "sum Phase 2 BM25 document scores by source file",
            "B1": (
                "B0 + one-hop import/imported-by bonus 0.25*max_seed_score + "
                "fan-in bonus 0.01*max_seed_score*min(fan_in,10)/10"
            ),
            "b1_seed_count": DEFAULT_PREDICTION_K,
        },
        prediction_cutoff=prediction_k,
        bootstrap_seed=bootstrap_seed,
        bootstrap_samples=bootstrap_samples,
        ripple_git_commit=git_commit,
        ripple_git_dirty=git_dirty,
        total_tasks=len(manifest.tasks),
        valid_tasks=len(valid_ids),
        skipped_tasks=(),
        failed_setup_tasks=tuple(failures),
        per_task=tuple(per_task),
        aggregates=aggregates,
        ground_truth_limitation=(
            "A historical PR is one valid implementation, not necessarily the only "
            "valid implementation; exact-diff precision may penalize alternatives."
        ),
    )
    _write_results(Path(output_path), results)
    return results


def _write_results(path: Path, results: BaseModel) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise EvaluationError(
                f"cannot safely replace invalid results file: {error}"
            ) from error
        if existing.get("schema_version") != EVALUATION_SCHEMA_VERSION:
            raise EvaluationError("refusing to overwrite incompatible results schema")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(results.model_dump_json(indent=2))
            output.write("\n")
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _dedupe_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(_relative_path(path) for path in paths))


def prepare_evaluation_request(masked_request: str) -> PreparedEvaluationRequest:
    """Apply the frozen non-LLM prefix limit before any evaluated system runs."""

    prepared = masked_request[:EVALUATION_REQUEST_LIMIT]
    return PreparedEvaluationRequest(
        text=prepared,
        original_length=len(masked_request),
        used_length=len(prepared),
        truncated=len(masked_request) > len(prepared),
    )


def _mvp_aggregate(
    system: Literal["B0", "B1", "RIPPLE"], results: Sequence[MVPTaskResult]
) -> MVPAggregateResult:
    selected = [
        candidate
        for task in results
        for candidate in task.systems
        if candidate.system == system
    ]
    if not selected:
        raise EvaluationError(f"no MVP results for {system}")
    return MVPAggregateResult(
        system=system,
        task_count=len(selected),
        mean_precision=fmean(item.metrics.precision for item in selected),
        mean_recall=fmean(item.metrics.recall for item in selected),
        mean_f1=fmean(item.metrics.f1 for item in selected),
        mean_recall_at_5=fmean(item.metrics.recall_at_5 for item in selected),
        mean_recall_at_10=fmean(item.metrics.recall_at_10 for item in selected),
        mean_mrr=fmean(item.metrics.mrr for item in selected),
        mean_false_positives=fmean(item.metrics.false_positives for item in selected),
        mean_test_precision=fmean(item.test_precision for item in selected),
        mean_test_recall=fmean(item.test_recall for item in selected),
        mean_runtime_seconds=fmean(item.runtime_seconds for item in selected),
    )


def evaluate_agent_manifest(
    manifest_path: str | Path,
    *,
    workspace: str | Path,
    output_path: str | Path,
    llm: LLMClient,
    task_limit: int | None = None,
    verbose: bool = False,
) -> MVPEvaluationResults:
    """Evaluate B0/B1 and the agent at the agent's leak-safe prediction size."""

    manifest = load_tasks(manifest_path, expected_count=20)
    tasks = manifest.tasks[:task_limit] if task_limit is not None else manifest.tasks
    per_task: list[MVPTaskResult] = []
    failures: list[dict[str, str]] = []
    workspace_path = Path(workspace).resolve()
    for task in tasks:
        try:
            repo, leak_checks = prepare_repository(task, workspace_path)
            index = scan_repository(repo)
            prepared_request = prepare_evaluation_request(task.masked_request)
            # Only the masked request and base-checkout index cross this boundary.
            agent_run = analyze_repository(
                index,
                FeatureRequest(text=prepared_request.text),
                llm,
                output_root=workspace_path / "_agent_artifacts" / task.id,
            )
            source_paths = {item.path for item in index.files if not item.is_test}
            agent_sources = _dedupe_paths(
                component.target.partition("::")[0]
                for component in agent_run.report.affected_components
                if Path(component.target.partition("::")[0]) in source_paths
            )
            agent_tests = _dedupe_paths(
                item.test_path for item in agent_run.report.suggested_tests
            )
            agent_k = len(agent_sources)
            systems: list[MVPSystemResult] = []
            for name, runner in (("B0", bm25_baseline), ("B1", structural_baseline)):
                started = perf_counter()
                prediction = runner(index, prepared_request.text)
                runtime = perf_counter() - started
                source_ranking = _dedupe_paths(
                    item.path for item in prediction.source_predictions
                )
                test_ranking = _dedupe_paths(
                    item.path for item in prediction.test_predictions
                )
                test_precision, test_recall = test_set_metrics(
                    test_ranking, task.gold_files.test_python, cutoff=agent_k
                )
                systems.append(
                    MVPSystemResult(
                        system=name,
                        source_ranking=source_ranking,
                        test_ranking=test_ranking,
                        metrics=score_ranking(
                            source_ranking,
                            task.gold_files.source_python,
                            prediction_k=agent_k,
                        ),
                        test_precision=test_precision,
                        test_recall=test_recall,
                        runtime_seconds=runtime,
                    )
                )
            agent_test_precision, agent_test_recall = test_set_metrics(
                agent_tests, task.gold_files.test_python, cutoff=agent_k
            )
            systems.append(
                MVPSystemResult(
                    system="RIPPLE",
                    source_ranking=agent_sources,
                    test_ranking=agent_tests,
                    metrics=score_ranking(
                        agent_sources,
                        task.gold_files.source_python,
                        prediction_k=agent_k,
                    ),
                    test_precision=agent_test_precision,
                    test_recall=agent_test_recall,
                    runtime_seconds=agent_run.report.run_stats.runtime_seconds,
                )
            )
            per_task.append(
                MVPTaskResult(
                    task_id=task.id,
                    repository=task.repository,
                    base_commit=task.base_commit,
                    masked_request=prepared_request.text,
                    masked_request_original_length=prepared_request.original_length,
                    masked_request_used_length=prepared_request.used_length,
                    masked_request_truncated=prepared_request.truncated,
                    gold_files=task.gold_files,
                    agent_k=agent_k,
                    systems=tuple(systems),
                    agent_status=agent_run.report.status,
                    agent_tool_calls=agent_run.report.run_stats.tool_calls,
                    agent_llm_calls=agent_run.report.run_stats.llm_calls,
                    agent_total_tokens=agent_run.report.run_stats.total_tokens,
                    agent_dropped_claims=len(agent_run.report.dropped_claims),
                    agent_stop_reason=agent_run.report.run_stats.stop_reason,
                    agent_report_path=agent_run.report_path,
                    agent_trace_path=agent_run.trace_path,
                    leak_checks=leak_checks,
                )
            )
            if verbose:
                print(f"evaluated {task.id}: {agent_run.report.status}, k={agent_k}")
        except (EvaluationError, OSError, ValueError) as error:
            failures.append({"task_id": task.id, "reason": str(error)})
            if verbose:
                print(f"failed {task.id}: {error}")
    if not per_task:
        raise EvaluationError("no tasks completed MVP evaluation")
    results = MVPEvaluationResults(
        created_at=datetime.now(UTC),
        task_manifest=str(Path(manifest_path)),
        task_ids=tuple(task.id for task in tasks),
        agent_config_version=AGENT_CONFIG_VERSION,
        request_preparation=(
            "Deterministic Python character-prefix truncation to at most 2000 "
            "characters, performed once and shared unchanged by B0, B1, and RIPPLE."
        ),
        llm_model=llm.model,
        fair_cutoff=(
            "Per task, B0 and B1 set metrics use agent_k, the number of validated "
            "RIPPLE source components; agent_k=0 gives exact-zero set metrics."
        ),
        total_tasks=len(tasks),
        valid_tasks=len(per_task),
        failed_setup_tasks=tuple(failures),
        status_counts=dict(Counter(item.agent_status for item in per_task)),
        per_task=tuple(per_task),
        aggregates=tuple(
            _mvp_aggregate(system, per_task) for system in ("B0", "B1", "RIPPLE")
        ),
        ground_truth_limitation=(
            "A historical PR is one valid implementation, not necessarily the only "
            "valid implementation; exact-diff precision may penalize alternatives."
        ),
    )
    _write_results(Path(output_path), results)
    return results


def _phase5_aggregate(
    system: Phase5SystemName, results: Sequence[Phase5TaskResult]
) -> Phase5AggregateResult:
    selected = [
        item for task in results for item in task.systems if item.system == system
    ]
    if not selected:
        raise EvaluationError(f"no Phase 5 results for {system}")
    return Phase5AggregateResult(
        system=system,
        task_count=len(selected),
        mean_precision=fmean(item.metrics.precision for item in selected),
        mean_recall=fmean(item.metrics.recall for item in selected),
        mean_f1=fmean(item.metrics.f1 for item in selected),
        mean_recall_at_5=fmean(item.metrics.recall_at_5 for item in selected),
        mean_recall_at_10=fmean(item.metrics.recall_at_10 for item in selected),
        mean_mrr=fmean(item.metrics.mrr for item in selected),
        mean_false_positives=fmean(item.metrics.false_positives for item in selected),
    )


def evaluate_phase5_manifest(
    manifest_path: str | Path,
    *,
    workspace: str | Path,
    output_path: str | Path,
    llm: LLMClient,
    task_limit: int | None = None,
    verbose: bool = False,
) -> Phase5EvaluationResults:
    """Run the separate six-system Phase 5 development comparison."""

    manifest = load_tasks(manifest_path, expected_count=20)
    tasks = manifest.tasks[:task_limit] if task_limit is not None else manifest.tasks
    per_task: list[Phase5TaskResult] = []
    failures: list[dict[str, str]] = []
    workspace_path = Path(workspace).resolve()
    for task in tasks:
        try:
            repo, leak_checks = prepare_repository(
                task, workspace_path, history_depth=501
            )
            index = scan_repository(repo)
            prepared = prepare_evaluation_request(task.masked_request)
            request = FeatureRequest(text=prepared.text)

            agent_run = analyze_repository(
                index,
                request,
                llm,
                output_root=workspace_path / "_agent_artifacts" / task.id,
            )
            source_paths = {item.path for item in index.files if not item.is_test}
            agent_sources = _dedupe_paths(
                item.target.partition("::")[0]
                for item in agent_run.report.affected_components
                if Path(item.target.partition("::")[0]) in source_paths
            )
            agent_tests = _dedupe_paths(
                item.test_path for item in agent_run.report.suggested_tests
            )
            agent_k = len(agent_sources)
            systems: list[Phase5SystemResult] = []

            for name, runner in (
                ("B0", bm25_baseline),
                ("B1", structural_baseline),
                ("B2", cochange_baseline),
            ):
                started = perf_counter()
                prediction = runner(index, prepared.text)
                runtime = perf_counter() - started
                source_ranking = _dedupe_paths(
                    item.path for item in prediction.source_predictions
                )
                test_ranking = _dedupe_paths(
                    item.path for item in prediction.test_predictions
                )
                test_precision, test_recall = test_set_metrics(
                    test_ranking, task.gold_files.test_python, cutoff=agent_k
                )
                systems.append(
                    Phase5SystemResult(
                        system=name,
                        source_ranking=source_ranking,
                        test_ranking=test_ranking,
                        metrics=score_ranking(
                            source_ranking,
                            task.gold_files.source_python,
                            prediction_k=agent_k,
                        ),
                        test_precision=test_precision,
                        test_recall=test_recall,
                        runtime_seconds=runtime,
                    )
                )

            for name, runner in (("B3", one_shot_baseline), ("B4", react_baseline)):
                started = perf_counter()
                baseline_run = runner(index, request, llm)
                runtime = perf_counter() - started
                source_ranking = _dedupe_paths(
                    item.path for item in baseline_run.prediction.source_predictions
                )
                test_ranking = _dedupe_paths(
                    item.path for item in baseline_run.prediction.test_predictions
                )
                test_precision, test_recall = test_set_metrics(
                    test_ranking, task.gold_files.test_python, cutoff=agent_k
                )
                systems.append(
                    Phase5SystemResult(
                        system=name,
                        source_ranking=source_ranking,
                        test_ranking=test_ranking,
                        metrics=score_ranking(
                            source_ranking,
                            task.gold_files.source_python,
                            prediction_k=agent_k,
                        ),
                        test_precision=test_precision,
                        test_recall=test_recall,
                        runtime_seconds=runtime,
                        dropped_predictions=baseline_run.dropped_predictions,
                        llm_calls=baseline_run.llm_calls,
                        tool_calls=baseline_run.tool_calls,
                    )
                )

            test_precision, test_recall = test_set_metrics(
                agent_tests, task.gold_files.test_python, cutoff=agent_k
            )
            systems.append(
                Phase5SystemResult(
                    system="RIPPLE",
                    source_ranking=agent_sources,
                    test_ranking=agent_tests,
                    metrics=score_ranking(
                        agent_sources,
                        task.gold_files.source_python,
                        prediction_k=agent_k,
                    ),
                    test_precision=test_precision,
                    test_recall=test_recall,
                    runtime_seconds=agent_run.report.run_stats.runtime_seconds,
                    llm_calls=agent_run.report.run_stats.llm_calls,
                    tool_calls=agent_run.report.run_stats.tool_calls,
                )
            )
            per_task.append(
                Phase5TaskResult(
                    task_id=task.id,
                    repository=task.repository,
                    base_commit=task.base_commit,
                    masked_request_original_length=prepared.original_length,
                    masked_request_used_length=prepared.used_length,
                    masked_request_truncated=prepared.truncated,
                    agent_k=agent_k,
                    systems=tuple(systems),
                    agent_status=agent_run.report.status,
                    leak_checks=leak_checks,
                )
            )
            if verbose:
                print(f"evaluated {task.id}: {agent_run.report.status}, k={agent_k}")
        except (EvaluationError, OSError, ValueError) as error:
            failures.append({"task_id": task.id, "reason": str(error)})
            if verbose:
                print(f"failed {task.id}: {error}")
    if not per_task:
        raise EvaluationError("no tasks completed Phase 5 evaluation")
    systems_order: tuple[Phase5SystemName, ...] = (
        "B0",
        "B1",
        "B2",
        "B3",
        "B4",
        "RIPPLE",
    )
    results = Phase5EvaluationResults(
        label="DEVELOPMENT / PHASE 5 — NOT FINAL HELD-OUT RESULTS",
        created_at=datetime.now(UTC),
        task_manifest=str(Path(manifest_path)),
        agent_config_version=FULL_REPORT_CONFIG_VERSION,
        llm_model=llm.model,
        history_policy=(
            "Each checkout fetches at most 501 commits ending at the exact base SHA, "
            "removes the remote and all refs, and co-change queries HEAD only."
        ),
        total_tasks=len(tasks),
        valid_tasks=len(per_task),
        failed_setup_tasks=tuple(failures),
        status_counts=dict(Counter(item.agent_status for item in per_task)),
        per_task=tuple(per_task),
        aggregates=tuple(_phase5_aggregate(name, per_task) for name in systems_order),
        ground_truth_limitation=(
            "A historical PR is one valid implementation, not necessarily the only "
            "valid implementation; exact-diff precision may penalize alternatives."
        ),
    )
    _write_results(Path(output_path), results)
    return results


def format_results_table(results: EvaluationResults) -> str:
    """Render the concise CLI aggregate table."""

    headings = ("Baseline", "P", "R", "F1", "R@5", "R@10", "MRR", "Test R", "Time")
    rows = [headings]
    for item in results.aggregates:
        rows.append(
            (
                item.baseline,
                f"{item.mean_precision:.3f}",
                f"{item.mean_recall:.3f}",
                f"{item.mean_f1:.3f}",
                f"{item.mean_recall_at_5:.3f}",
                f"{item.mean_recall_at_10:.3f}",
                f"{item.mean_mrr:.3f}",
                "—"
                if item.mean_test_recall is None
                else f"{item.mean_test_recall:.3f}",
                f"{item.mean_runtime_seconds:.3f}s",
            )
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(headings))]
    rendered = [
        " | ".join(value.ljust(widths[i]) for i, value in enumerate(row))
        for row in rows
    ]
    rendered.insert(1, "-+-".join("-" * width for width in widths))
    return "\n".join(rendered)
