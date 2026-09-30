"""Command-line interface for RIPPLE."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter

from ripple.agent import analyze_repository
from ripple.agent_models import FeatureRequest
from ripple.cache import ScanResult, scan_repository_cached
from ripple.evaluation import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_PREDICTION_K,
    EvaluationError,
    evaluate_agent_manifest,
    evaluate_manifest,
    format_results_table,
)
from ripple.llm import LLMError, OpenAILLM
from ripple.scanner import ScanError
from ripple.tools import ToolSession, error_result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ripple")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="scan the tracked Python files in a Git repository"
    )
    scan_parser.add_argument("repo", type=Path, help="path to a Git repository")
    scan_parser.add_argument(
        "--json",
        action="store_true",
        help="write the complete repository index as JSON",
    )
    scan_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="bypass cache reads and writes",
    )

    tool_parser = subparsers.add_parser(
        "tool", help="invoke a deterministic repository tool"
    )
    tool_parser.add_argument("repo", type=Path, help="path to a Git repository")
    tool_parser.add_argument("name", help="tool name")
    tool_parser.add_argument("arguments", help="tool arguments as a JSON object")

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="run the fixed deterministic development baselines"
    )
    evaluate_parser.add_argument(
        "--tasks",
        type=Path,
        default=Path("evaluation/data/dev_tasks.json"),
        help="path to the fixed evaluation task manifest",
    )
    evaluate_parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(".ripple/evaluation/repos"),
        help="evaluation-owned directory for fresh base-only checkouts",
    )
    evaluate_parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results/dev_baselines.json"),
        help="machine-readable result path",
    )
    evaluate_parser.add_argument(
        "--prediction-k", type=int, default=DEFAULT_PREDICTION_K
    )
    evaluate_parser.add_argument(
        "--bootstrap-samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES
    )
    evaluate_parser.add_argument(
        "--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED
    )
    evaluate_parser.add_argument("--verbose", action="store_true")

    agent_eval_parser = subparsers.add_parser(
        "evaluate-agent", help="run the fixed 20-task Phase 4 MVP evaluation"
    )
    agent_eval_parser.add_argument(
        "--tasks", type=Path, default=Path("evaluation/data/mvp_tasks.json")
    )
    agent_eval_parser.add_argument(
        "--workspace", type=Path, default=Path(".ripple/evaluation/mvp-repos")
    )
    agent_eval_parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results/mvp_agent_results.json"),
    )
    agent_eval_parser.add_argument("--task-limit", type=int)
    agent_eval_parser.add_argument("--verbose", action="store_true")

    analyze_parser = subparsers.add_parser(
        "analyze", help="run bounded evidence-led change-impact analysis"
    )
    analyze_parser.add_argument("repo", type=Path, help="path to a Git repository")
    analyze_parser.add_argument("request", help="feature request to analyze")
    analyze_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="analyze a dirty checkout and mark the report and trace",
    )
    analyze_parser.add_argument(
        "--json", action="store_true", help="write only the report JSON to stdout"
    )
    return parser


def _cache_summary(result: ScanResult) -> str:
    if result.cache_path is None:
        return "disabled"
    relative_path = result.cache_path.relative_to(result.index.repo_root)
    status = "hit" if result.cache_hit else "written"
    return f"{status} ({relative_path})"


def _run_scan(args: argparse.Namespace) -> int:
    started_at = perf_counter()
    try:
        result = scan_repository_cached(args.repo, use_cache=not args.no_cache)
    except ScanError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    elapsed = perf_counter() - started_at
    index = result.index

    if args.json:
        print(index.model_dump_json(indent=2))
        return 0

    test_count = sum(file.is_test for file in index.files)
    print(f"Repository: {index.repo_root.name}")
    print(f"Commit: {index.commit[:7]}")
    print(f"Dirty: {'yes' if index.dirty else 'no'}")
    print(f"Python files: {len(index.files)}")
    print(f"Test files: {test_count}")
    print(f"Symbols: {len(index.symbols)}")
    print(f"Imports: {len(index.imports)}")
    print(f"References: {len(index.references)}")
    print(f"Parse errors: {sum(file.parse_error is not None for file in index.files)}")
    print(f"Cache: {_cache_summary(result)}")
    print(f"Scan time: {elapsed:.2f}s")
    return 0


def _run_tool(args: argparse.Namespace) -> int:
    try:
        arguments = json.loads(args.arguments)
    except json.JSONDecodeError as error:
        result = error_result(
            "e1",
            "invalid_json",
            "tool arguments must be valid JSON",
            str(error),
        )
        print(result.model_dump_json(indent=2))
        return 1

    try:
        scan_result = scan_repository_cached(args.repo)
    except ScanError as error:
        result = error_result("e1", "repository_error", str(error))
        print(result.model_dump_json(indent=2))
        return 1

    result = ToolSession(scan_result.index).invoke(args.name, arguments)
    print(result.model_dump_json(indent=2))
    return 0 if result.ok else 1


def _run_evaluate(args: argparse.Namespace) -> int:
    if args.prediction_k < 1 or args.bootstrap_samples < 1:
        print(
            "error: prediction-k and bootstrap-samples must be positive",
            file=sys.stderr,
        )
        return 1
    try:
        results = evaluate_manifest(
            args.tasks,
            workspace=args.workspace,
            output_path=args.output,
            prediction_k=args.prediction_k,
            bootstrap_samples=args.bootstrap_samples,
            bootstrap_seed=args.bootstrap_seed,
            verbose=args.verbose,
        )
    except EvaluationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(format_results_table(results))
    print(
        f"Tasks: {results.valid_tasks}/{results.total_tasks} valid; "
        f"{len(results.failed_setup_tasks)} failed"
    )
    print(f"Results: {args.output}")
    return 0 if results.valid_tasks == results.total_tasks else 1


def _run_analyze(args: argparse.Namespace) -> int:
    try:
        request = FeatureRequest(text=args.request)
        scan_result = scan_repository_cached(args.repo)
        if scan_result.index.dirty and not args.allow_dirty:
            print(
                "error: repository is dirty; commit/stash changes or pass --allow-dirty",
                file=sys.stderr,
            )
            return 1
        run = analyze_repository(scan_result.index, request, OpenAILLM.from_env())
    except (ScanError, LLMError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(run.report.model_dump_json(indent=2))
    else:
        print(f"Status: {run.report.status}")
        print(f"Components: {len(run.report.affected_components)}")
        print(f"Tool calls: {run.report.run_stats.tool_calls}")
        print(f"Stop reason: {run.report.run_stats.stop_reason}")
        print(f"Report: {run.report_path}")
        print(f"Trace: {run.trace_path}")
    return 0 if run.report.status in {"completed", "partial", "abstained"} else 1


def _run_evaluate_agent(args: argparse.Namespace) -> int:
    if args.task_limit is not None and not 1 <= args.task_limit <= 20:
        print("error: task-limit must be between 1 and 20", file=sys.stderr)
        return 1
    try:
        results = evaluate_agent_manifest(
            args.tasks,
            workspace=args.workspace,
            output_path=args.output,
            llm=OpenAILLM.from_env(),
            task_limit=args.task_limit,
            verbose=args.verbose,
        )
    except (EvaluationError, LLMError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"Tasks: {results.valid_tasks}/{results.total_tasks}; "
        f"statuses: {results.status_counts}"
    )
    for aggregate in results.aggregates:
        print(
            f"{aggregate.system}: P={aggregate.mean_precision:.3f} "
            f"R={aggregate.mean_recall:.3f} F1={aggregate.mean_f1:.3f} "
            f"MRR={aggregate.mean_mrr:.3f}"
        )
    print(f"Results: {args.output}")
    return 0 if results.valid_tasks == results.total_tasks else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the RIPPLE command-line interface."""

    args = _parser().parse_args(argv)
    if args.command == "tool":
        return _run_tool(args)
    if args.command == "evaluate":
        return _run_evaluate(args)
    if args.command == "analyze":
        return _run_analyze(args)
    if args.command == "evaluate-agent":
        return _run_evaluate_agent(args)
    return _run_scan(args)


if __name__ == "__main__":
    raise SystemExit(main())
