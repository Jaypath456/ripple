"""Phase 5 LLM baselines without RIPPLE controller advantages."""

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from ripple.agent import MAX_TOOL_CALLS, _prompt_data, build_repository_map
from ripple.agent_models import BaselineDecision, FeatureRequest, RankedPathDraft
from ripple.baselines import BaselinePrediction, RankedFile
from ripple.llm import LLMClient
from ripple.models import RepositoryIndex
from ripple.tools import ToolSession, validate_tool_arguments

B3_OUTLINE_CHARACTER_BUDGET = 24_000


@dataclass(frozen=True)
class LLMBaselineRun:
    prediction: BaselinePrediction
    dropped_predictions: tuple[str, ...]
    llm_calls: int
    tool_calls: int


def _validated_prediction(
    baseline: str, raw_paths: tuple[str, ...], index: RepositoryIndex
) -> tuple[BaselinePrediction, tuple[str, ...]]:
    source_paths = {file.path.as_posix() for file in index.files if not file.is_test}
    ranked: list[RankedFile] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for position, path in enumerate(raw_paths, 1):
        if path not in source_paths:
            dropped.append(f"missing or non-source path: {path}")
        elif path in seen:
            dropped.append(f"duplicate path: {path}")
        else:
            seen.add(path)
            ranked.append(
                RankedFile(
                    path=Path(path),
                    score=round(1.0 / position, 6),
                    reasons=(f"{baseline.lower()}_rank:{position}",),
                )
            )
    return (
        BaselinePrediction(baseline=baseline, source_predictions=tuple(ranked)),
        tuple(dropped),
    )


def _outline(index: RepositoryIndex) -> str:
    by_path: dict[str, list[str]] = {file.path.as_posix(): [] for file in index.files}
    for symbol in index.symbols:
        by_path[symbol.path.as_posix()].append(
            f"{symbol.kind} {symbol.qualname}{symbol.signature or ''}"
        )
    lines = [
        f"{path}: {'; '.join(symbols) if symbols else '[no symbols]'}"
        for path, symbols in sorted(by_path.items())
    ]
    return "\n".join(lines)[:B3_OUTLINE_CHARACTER_BUDGET]


def one_shot_baseline(
    index: RepositoryIndex, request: FeatureRequest, llm: LLMClient
) -> LLMBaselineRun:
    """B3: exactly one model call over the fixed map and symbol outline."""

    prompt = (
        "Rank the source Python files likely to change. Return repository-relative "
        "paths only, most likely first. This is one-shot; no tools are available.\n"
        + _prompt_data("feature_request", request.text)
        + "\n"
        + _prompt_data("repository_map", build_repository_map(index))
        + "\n"
        + _prompt_data("symbol_outline", _outline(index))
    )
    response = llm.one_shot_rank(prompt)
    try:
        raw = RankedPathDraft.model_validate(response.payload).paths
    except ValidationError:
        raw = ()
    prediction, dropped = _validated_prediction("B3", raw, index)
    return LLMBaselineRun(prediction, dropped, llm_calls=1, tool_calls=0)


def react_baseline(
    index: RepositoryIndex, request: FeatureRequest, llm: LLMClient
) -> LLMBaselineRun:
    """B4: plain bounded ReAct with tools but no ledger, Expand, or report validator."""

    session = ToolSession(index)
    observations: list[dict[str, object]] = []
    calls = 0
    llm_calls = 0
    raw_paths: tuple[str, ...] = ()
    while calls < MAX_TOOL_CALLS and llm_calls < MAX_TOOL_CALLS:
        prompt = (
            "Choose one registered tool, or submit_report with predicted_paths ranked "
            "most likely first. No candidate ledger is provided. Tool argument shapes "
            "are the same as RIPPLE and the hard tool budget is 25.\n"
            + _prompt_data(
                "repository_data",
                {
                    "request": request.text,
                    "map": build_repository_map(index),
                    "observations": observations[-3:],
                    "tool_calls": calls,
                },
            )
        )
        response = llm.react_decide(prompt)
        llm_calls += 1
        try:
            decision = BaselineDecision.model_validate(response.payload)
        except ValidationError as error:
            observations.append({"error": f"invalid decision: {error}"})
            continue
        if decision.tool_name == "submit_report":
            raw_paths = decision.predicted_paths
            break
        try:
            canonical = validate_tool_arguments(decision.tool_name, decision.arguments)
        except ValueError as error:
            observations.append({"error": str(error)})
            continue
        result = session.invoke(decision.tool_name, canonical)
        calls += 1
        observations.append(
            {
                "tool": decision.tool_name,
                "arguments": canonical,
                "result": json.loads(result.model_dump_json()),
            }
        )
    prediction, dropped = _validated_prediction("B4", raw_paths, index)
    return LLMBaselineRun(prediction, dropped, llm_calls=llm_calls, tool_calls=calls)
