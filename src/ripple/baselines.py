"""Deterministic file-ranking baselines for change-surface evaluation."""

from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ripple.models import RepositoryIndex
from ripple.search import SearchIndex

DEFAULT_PREDICTION_K = 5
STRUCTURAL_BONUS_FRACTION = 0.25
FAN_IN_BONUS_FRACTION = 0.01
FAN_IN_CAP = 10


class RankedFile(BaseModel):
    """One auditable file prediction."""

    model_config = ConfigDict(frozen=True)

    path: Path
    score: float
    reasons: tuple[str, ...]


class BaselinePrediction(BaseModel):
    """Full source ranking and any separately ranked tests."""

    model_config = ConfigDict(frozen=True)

    baseline: Literal["B0", "B1"]
    source_predictions: tuple[RankedFile, ...]
    test_predictions: tuple[RankedFile, ...] = ()


def bm25_baseline(index: RepositoryIndex, request: str) -> BaselinePrediction:
    """Rank every non-test Python file by aggregated Phase 2 BM25 scores."""

    search = SearchIndex(index)
    hits, _ = search.search(request, limit=max(1, len(search.documents)))
    source_paths = {file.path for file in index.files if not file.is_test}
    scores = {path: 0.0 for path in source_paths}
    kinds: dict[Path, Counter[str]] = defaultdict(Counter)
    for hit in hits:
        if hit.path not in source_paths:
            continue
        scores[hit.path] += hit.score
        kinds[hit.path][hit.kind] += 1

    ranked = tuple(
        RankedFile(
            path=path,
            score=round(score, 6),
            reasons=tuple(
                f"bm25:{kind}:{count}" for kind, count in sorted(kinds[path].items())
            )
            or ("bm25:no_match",),
        )
        for path, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    )
    return BaselinePrediction(baseline="B0", source_predictions=ranked)


def structural_baseline(
    index: RepositoryIndex,
    request: str,
    *,
    seed_count: int = DEFAULT_PREDICTION_K,
) -> BaselinePrediction:
    """Add bounded one-hop dependency and fan-in signals to B0."""

    b0 = bm25_baseline(index, request)
    seeds = b0.source_predictions[:seed_count]
    seed_paths = {item.path for item in seeds}
    maximum_seed_score = max((item.score for item in seeds), default=0.0)
    graph = {node.path: node for node in index.dependency_graph}
    source_paths = {file.path for file in index.files if not file.is_test}
    expanded_by: dict[Path, set[Path]] = defaultdict(set)
    for seed in seeds:
        node = graph.get(seed.path)
        if node is None:
            continue
        for candidate in (*node.dependencies, *node.dependents):
            if candidate in source_paths and candidate not in seed_paths:
                expanded_by[candidate].add(seed.path)

    ranked: list[RankedFile] = []
    for item in b0.source_predictions:
        node = graph.get(item.path)
        fan_in = len(node.dependents) if node is not None else 0
        structural_bonus = (
            STRUCTURAL_BONUS_FRACTION * maximum_seed_score
            if item.path in expanded_by
            else 0.0
        )
        fan_in_bonus = (
            FAN_IN_BONUS_FRACTION
            * maximum_seed_score
            * min(fan_in, FAN_IN_CAP)
            / FAN_IN_CAP
        )
        reasons = list(item.reasons)
        if item.path in seed_paths:
            reasons.append("bm25_seed")
        if item.path in expanded_by:
            seeds_text = ",".join(
                path.as_posix() for path in sorted(expanded_by[item.path])
            )
            reasons.append(f"one_hop:{seeds_text}")
        if fan_in:
            reasons.append(f"fan_in:{fan_in}")
        ranked.append(
            RankedFile(
                path=item.path,
                score=round(item.score + structural_bonus + fan_in_bonus, 6),
                reasons=tuple(reasons),
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.path))

    source_rank = {item.path: rank for rank, item in enumerate(ranked, start=1)}
    structural_candidates = seed_paths | set(expanded_by)
    test_sources: dict[Path, set[Path]] = defaultdict(set)
    for mapping in index.test_mappings:
        if mapping.source_path in structural_candidates:
            test_sources[mapping.test_path].add(mapping.source_path)
    tests = tuple(
        RankedFile(
            path=test_path,
            score=round(1.0 / min(source_rank[path] for path in associated_sources), 6),
            reasons=tuple(
                f"mapped_from:{path.as_posix()}" for path in sorted(associated_sources)
            ),
        )
        for test_path, associated_sources in sorted(
            test_sources.items(),
            key=lambda item: (
                min(source_rank[path] for path in item[1]),
                item[0],
            ),
        )
    )
    return BaselinePrediction(
        baseline="B1",
        source_predictions=tuple(ranked),
        test_predictions=tests,
    )
