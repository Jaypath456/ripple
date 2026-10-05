"""Command-line interface for RIPPLE."""

import argparse
import json
import os
import sys
from collections import Counter
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
    evaluate_phase5_manifest,
    format_results_table,
)
from ripple.final_evaluation import freeze_config, run_final, run_smoke
from ripple.llm import LLMError, OpenAILLM
from ripple.scanner import ScanError
from ripple.show_run import RunNotFoundError, replay_run
from ripple.tools import ToolSession, error_result
from ripple.verification import VerificationError, verify_repository


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

    phase5_eval_parser = subparsers.add_parser(
        "evaluate-phase5", help="run the six-system Phase 5 development comparison"
    )
    phase5_eval_parser.add_argument(
        "--tasks", type=Path, default=Path("evaluation/data/mvp_tasks.json")
    )
    phase5_eval_parser.add_argument(
        "--workspace", type=Path, default=Path(".ripple/evaluation/phase5-repos")
    )
    phase5_eval_parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results/phase5_dev_comparison.json"),
    )
    phase5_eval_parser.add_argument("--task-limit", type=int)
    phase5_eval_parser.add_argument("--verbose", action="store_true")

    smoke_parser = subparsers.add_parser(
        "final-smoke", help="run the five-development-task Phase 7 provider gate"
    )
    smoke_parser.add_argument(
        "--tasks", type=Path, default=Path("evaluation/data/mvp_tasks.json")
    )
    smoke_parser.add_argument(
        "--workspace", type=Path, default=Path(".ripple/evaluation/final-smoke")
    )
    smoke_parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results/bullsai_compatibility_smoke.json"),
    )
    smoke_parser.add_argument(
        "--force", action="store_true", help="intentionally replace smoke checkpoints"
    )

    freeze_parser = subparsers.add_parser(
        "freeze-final-config", help="freeze the credential-free Phase 7 configuration"
    )
    freeze_parser.add_argument(
        "--smoke",
        type=Path,
        default=Path("evaluation/results/bullsai_compatibility_smoke.json"),
    )
    freeze_parser.add_argument(
        "--output", type=Path, default=Path("evaluation/final_config.json")
    )

    final_parser = subparsers.add_parser(
        "evaluate-final", help="run or resume the frozen Phase 7 schedule"
    )
    final_parser.add_argument(
        "--tasks", type=Path, default=Path("evaluation/data/final_fea_tasks.json")
    )
    final_parser.add_argument(
        "--workspace", type=Path, default=Path(".ripple/evaluation/final-v1")
    )
    final_parser.add_argument(
        "--raw", type=Path, default=Path("evaluation/raw/final-v1")
    )
    final_parser.add_argument(
        "--schedule", type=Path, default=Path("evaluation/final_schedule.json")
    )
    final_parser.add_argument(
        "--config", type=Path, default=Path("evaluation/final_config.json")
    )
    final_parser.add_argument("--force", action="store_true")

    results_parser = subparsers.add_parser(
        "build-results", help="regenerate all Phase 7 metrics and the results README"
    )
    results_parser.add_argument(
        "--manifest", type=Path, default=Path("evaluation/data/final_fea_tasks.json")
    )
    results_parser.add_argument(
        "--raw", type=Path, default=Path("evaluation/raw/final-v1")
    )
    results_parser.add_argument(
        "--config", type=Path, default=Path("evaluation/final_config.json")
    )

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

    show_parser = subparsers.add_parser(
        "show-run", help="replay a saved investigation trace"
    )
    show_parser.add_argument("identifier", help="run ID or report ID")
    show_parser.add_argument("--repo", type=Path, default=Path.cwd())

    verify_parser = subparsers.add_parser(
        "verify", help="compare a saved prediction with an implementation Git range"
    )
    verify_parser.add_argument("repo", type=Path)
    verify_parser.add_argument(
        "--report", required=True, help="report ID, path, or latest"
    )
    verify_parser.add_argument(
        "--range", dest="git_range", required=True, help="Git range base..head"
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
        print(f"Markdown: {run.markdown_path}")
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
            force=args.force,
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


def _run_evaluate_phase5(args: argparse.Namespace) -> int:
    if args.task_limit is not None and not 1 <= args.task_limit <= 20:
        print("error: task-limit must be between 1 and 20", file=sys.stderr)
        return 1
    try:
        results = evaluate_phase5_manifest(
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
    for aggregate in results.aggregates:
        print(
            f"{aggregate.system}: P={aggregate.mean_precision:.3f} "
            f"R={aggregate.mean_recall:.3f} F1={aggregate.mean_f1:.3f} "
            f"R@5={aggregate.mean_recall_at_5:.3f} "
            f"R@10={aggregate.mean_recall_at_10:.3f} "
            f"MRR={aggregate.mean_mrr:.3f}"
        )
    print(f"Results: {args.output}")
    return 0 if results.valid_tasks == results.total_tasks else 1


def _run_final_smoke(args: argparse.Namespace) -> int:
    try:
        result = run_smoke(
            args.tasks,
            workspace=args.workspace,
            output_path=args.output,
            llm=OpenAILLM.from_env(),
        )
    except (EvaluationError, LLMError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    estimate = result["estimated_full_40_task_llm_workload"]
    print(
        f"Smoke: {result['successful_runs']}/{result['required_runs']} successful; "
        f"requests={result['totals']['requests']}, "
        f"tokens={result['totals']['total_tokens']}, "
        f"runtime={result['totals']['runtime_seconds']:.1f}s"
    )
    print(
        "Estimated 40-task LLM workload: "
        f"requests={estimate['requests']:.0f}, tokens={estimate['total_tokens']:.0f}, "
        f"runtime={estimate['runtime_seconds']:.0f}s"
    )
    print(f"Results: {args.output}")
    return 0 if result["successful_runs"] == result["required_runs"] else 1


def _run_freeze(args: argparse.Namespace) -> int:
    try:
        smoke = json.loads(args.smoke.read_text(encoding="utf-8"))
        if smoke.get("successful_runs") != smoke.get("required_runs"):
            raise EvaluationError("provider smoke has not passed")
        llm = OpenAILLM.from_env()
        result = freeze_config(
            args.output,
            model=llm.model,
            base_url=os.environ.get("RIPPLE_LLM_BASE_URL", ""),
        )
    except (EvaluationError, LLMError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Final config: {args.output}")
    print(f"Config hash: {result['config_hash']}")
    return 0


def _run_final(args: argparse.Namespace) -> int:
    try:
        runs = run_final(
            args.tasks,
            workspace=args.workspace,
            raw_root=args.raw,
            schedule_path=args.schedule,
            config_path=args.config,
            llm=OpenAILLM.from_env(),
            force=args.force,
        )
    except (EvaluationError, LLMError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    statuses = Counter(item.status for item in runs)
    print(f"Runs: {len(runs)}; statuses: {dict(statuses)}")
    print(f"Raw results: {args.raw}")
    return 0 if not statuses.get("provider_failed") else 1


def _run_build_results(args: argparse.Namespace) -> int:
    from ripple.evaluation_results import main

    try:
        return main(
            [
                "--manifest",
                str(args.manifest),
                "--raw",
                str(args.raw),
                "--config",
                str(args.config),
            ]
        )
    except (EvaluationError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _run_show(args: argparse.Namespace) -> int:
    try:
        print(replay_run(args.identifier, args.repo))
    except RunNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    try:
        llm = (
            OpenAILLM.from_env()
            if os.environ.get("RIPPLE_LLM_API_KEY")
            and os.environ.get("RIPPLE_LLM_MODEL")
            else None
        )
        run = verify_repository(
            args.repo,
            report_value=args.report,
            requested_range=args.git_range,
            llm=llm,
        )
    except (VerificationError, LLMError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if run.analysis.base_warning:
        print(f"warning: {run.analysis.base_warning}", file=sys.stderr)
    print(f"Status: {run.analysis.status}")
    print(f"Changed files: {len(run.analysis.changes)}")
    print(f"Findings: {len(run.analysis.findings)}")
    print(f"File precision: {run.analysis.file_precision:.3f}")
    print(f"File recall: {run.analysis.file_recall:.3f}")
    print(f"Verification: {run.json_path}")
    print(f"Markdown: {run.markdown_path}")
    print(f"Trace: {run.trace_path}")
    return 0 if run.analysis.status in {"completed", "partial"} else 1


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
    if args.command == "show-run":
        return _run_show(args)
    if args.command == "evaluate-phase5":
        return _run_evaluate_phase5(args)
    if args.command == "final-smoke":
        return _run_final_smoke(args)
    if args.command == "freeze-final-config":
        return _run_freeze(args)
    if args.command == "evaluate-final":
        return _run_final(args)
    if args.command == "build-results":
        return _run_build_results(args)
    if args.command == "verify":
        return _run_verify(args)
    return _run_scan(args)


if __name__ == "__main__":
    raise SystemExit(main())
