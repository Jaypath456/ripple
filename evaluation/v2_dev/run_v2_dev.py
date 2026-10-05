"""V2 DEVELOPMENT comparison: V1 protocol vs V2 protocol on development cases only.

Run from the repository root with RIPPLE_LLM_* configured:

    python evaluation/v2_dev/run_v2_dev.py            # resumes from results.json
    python evaluation/v2_dev/run_v2_dev.py --summary  # rewrite README from results

This is NOT a benchmark. No final-v1 held-out task is used: the cases are RIPPLE
itself (the known live failure), the bundled demo sample app, and four Phase 4
development tasks from repositories outside the final-v1 set. Each case runs the
frozen V1 protocol and the V2 protocol with the same live model, back to back.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from demo.build_fixtures import SAMPLE, SCENARIOS, build_sample_repo
from ripple.agent import AGENT_VARIANTS, RIPPLE_V2, AgentController
from ripple.agent_models import FeatureRequest
from ripple.evaluation import (
    classify_gold_files,
    load_tasks,
    prepare_evaluation_request,
    prepare_repository,
    score_ranking,
)
from ripple.llm import LLMError, OpenAILLM
from ripple.scanner import scan_repository

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.json"
README = HERE / "README.md"
WORKSPACE = ROOT / ".ripple" / "evaluation" / "v2-dev"
KNOWN_REQUEST = (
    "Add a CLI command that exports the latest Stage B verification report as a "
    "Markdown summary."
)
KNOWN_COMMIT = "75365943369965dc9ddca367ebe825bc79c1d8cf"
DEV_TASKS = (
    "boto__boto3-74",
    "graphql-python__graphene-1506",
    "prometheus__client_python-302",
    "falconry__falcon-640",
)
PROTOCOLS = {"v1": AGENT_VARIANTS["RIPPLE"], "v2": RIPPLE_V2}
# v2-r1 records came from the first V2 revision; r2 bounds checkpoint frequency
# (re-offer only on new strong evidence, a hard cap). V1 code is unchanged.
REVISIONS = {"v1": "v1", "v2": "v2-r2"}
COLUMNS = ("v1", "v2-r1", "v2-r2")
REPEATS = {"ripple_cli_markdown_export": 2}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _cases() -> list[dict]:
    """(id, repo path, request, gold source files or None)."""

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    cases = []
    ripple = WORKSPACE / "ripple-7536594"
    if not ripple.exists():
        subprocess.run(
            ["git", "clone", "-q", "--local", "--no-checkout", str(ROOT), str(ripple)],
            check=True,
        )
        _git(ripple, "checkout", "-q", KNOWN_COMMIT)
    cases.append(
        {
            "id": "ripple_cli_markdown_export",
            "repo": ripple,
            "request": KNOWN_REQUEST,
            "gold": None,
        }
    )
    sample = WORKSPACE / "sample_app"
    if not sample.exists():
        build_sample_repo(sample)
    for name, spec in SCENARIOS.items():
        gold = sorted(
            path.relative_to(SAMPLE / "scenarios" / name).as_posix()
            for path in (SAMPLE / "scenarios" / name).rglob("*.py")
            if "tests" not in path.parts
        )
        cases.append(
            {
                "id": f"sample_{name}",
                "repo": sample,
                "request": spec["request"],
                "gold": gold,
            }
        )
    final_ids = {
        task.id
        for task in load_tasks(ROOT / "evaluation/data/final_fea_tasks.json").tasks
    }
    tasks = {
        task.id: task
        for task in load_tasks(ROOT / "evaluation/data/mvp_tasks.json").tasks
    }
    for task_id in DEV_TASKS:
        assert task_id not in final_ids, "final-v1 held-out tasks must not be used"
        task = tasks[task_id]
        repo = WORKSPACE / "tasks" / task.id
        if not repo.exists():
            repo, _ = prepare_repository(task, WORKSPACE / "tasks", history_depth=501)
        cases.append(
            {
                "id": task.id,
                "repo": repo,
                "request": prepare_evaluation_request(task.masked_request).text,
                "gold": sorted(
                    path.as_posix() for path in task.gold_files.source_python
                ),
            }
        )
    return cases


def _run(case: dict, protocol: str, llm) -> dict:
    index = scan_repository(case["repo"])
    controller = AgentController(
        index,
        llm,
        output_root=WORKSPACE / "runs" / protocol,
        variant=PROTOCOLS[protocol],
    )
    run = controller.run(FeatureRequest(text=case["request"]))
    report = run.report
    trace = [json.loads(line) for line in run.trace_path.read_text().splitlines()]
    states = Counter(item.status for item in controller.ledger.candidates.values())
    confirmed = {item.target for item in controller.ledger.confirmed()}
    valid = {item.path.as_posix() for item in index.files} | {
        item.id for item in index.symbols
    }
    unsupported = [
        item.target
        for item in report.affected_components
        if not item.target.endswith("<proposed migration>")
        and (
            item.target not in confirmed
            or item.target not in valid
            or not item.evidence
            or any(
                evidence not in controller.ledger.evidence for evidence in item.evidence
            )
        )
    ]
    predicted = list(
        dict.fromkeys(
            item.target.partition("::")[0]
            for item in report.affected_components
            if not item.target.endswith("<proposed migration>")
        )
    )
    record = {
        "case": case["id"],
        "protocol": protocol,
        "protocol_revision": REVISIONS[protocol],
        "status": report.status,
        "stop_reason": report.run_stats.stop_reason,
        "confirmed": states["confirmed"],
        "suspected": states["suspected"],
        "rejected": states["rejected"],
        "tool_calls": report.run_stats.tool_calls,
        "duplicate_calls": report.run_stats.duplicate_calls,
        "model_calls": report.run_stats.llm_calls,
        "total_tokens": report.run_stats.total_tokens,
        "runtime_seconds": round(report.run_stats.runtime_seconds, 2),
        "invalid_tool_targets": sum(
            event["event"] == "tool_target_rejected"
            or (
                event["event"] == "validation_error"
                and event.get("operation") == "tool"
            )
            for event in trace
        )
        + sum(
            event["event"] == "tool_result"
            and not event.get("ok")
            and event.get("tool") == "find_references"
            for event in trace
        ),
        "decision_checkpoints": report.run_stats.decision_checkpoints,
        "ledger_proposals": sum(
            event["event"] in {"candidate_decision"}
            or (event["event"] == "ledger_update" and "update" in event)
            for event in trace
        ),
        "predicted_files": predicted,
        "unsupported_accepted_claims": unsupported,
        "dropped_claims": len(report.dropped_claims),
        "provider_error": next(
            (
                str(e.get("error"))[:160]
                for e in trace
                if e["event"] == "provider_error"
            ),
            None,
        ),
        "model": report.run_stats.model,
        "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if case["gold"] is not None:
        record |= {"gold_files": case["gold"]} | _score(predicted, case["gold"])
    return record


def _score(predicted: list[str], gold: list[str]) -> dict:
    """Source-file scoring, as in the frozen V1 development methodology."""

    source = [
        path.as_posix()
        for path in classify_gold_files(tuple(Path(p) for p in predicted)).source_python
    ]
    metrics = score_ranking(source, gold, prediction_k=len(source))
    return {
        "predicted_source_files": source,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1": metrics.f1,
    }


def _load() -> list[dict]:
    return json.loads(RESULTS.read_text()) if RESULTS.exists() else []


def _save(records: list[dict]) -> None:
    RESULTS.write_text(json.dumps(records, indent=2) + "\n")


def _mean(rows: list[dict], key: str) -> str:
    values = [row[key] for row in rows if row.get(key) is not None]
    return f"{fmean(values):.2f}" if values else "n/a"


def write_readme(records: list[dict]) -> None:
    lines = [
        "# RIPPLE V2 development results",
        "",
        "> **Development only. Not a benchmark.** These runs compare the frozen V1",
        "> agent protocol with the V2 protocol on development cases chosen after V1's",
        "> final results were known. They do not replace or reinterpret the frozen",
        "> final-v1 held-out benchmark (`evaluation/results/`). Each case/protocol pair",
        "> ran once (the known case twice) with no provider seed control, so differences",
        "> are noisy. A V2 benchmark would need a new untouched held-out set.",
        "",
        "Generated by `python evaluation/v2_dev/run_v2_dev.py --summary` from",
        "`results.json`. Model: "
        + ", ".join(sorted({f"`{row['model']}`" for row in records}))
        + " through the BullsAI OpenAI-compatible gateway.",
        "",
        "## Method",
        "",
        "- **Cases** ("
        + str(len({row["case"] for row in records}))
        + "): "
        + ", ".join(
            f"`{case}`" for case in dict.fromkeys(row["case"] for row in records)
        )
        + ".",
        "  - `ripple_cli_markdown_export`: the live V1 failure that motivated V2, run",
        "    on RIPPLE at commit `7536594` (no gold; predictions are listed below).",
        "  - `sample_*`: the three bundled demo scenarios; gold is each scenario's",
        "    changed source files.",
        "  - Four Phase 4 development tasks from repositories outside the final-v1",
        "    set; gold is the PR's source files. No final-v1 held-out task is used.",
        "- **Protocols.** `v1` is the frozen final-v1 agent (`AGENT_VARIANTS['RIPPLE']`,",
        "  unchanged code). `v2-r1` is the first V2 protocol: decision checkpoints,",
        "  tool-target pre-validation, limit-insensitive dedup, and a ledger-stall bound.",
        "  `v2-r2` is the single follow-up revision, made after r1 showed unbounded",
        "  checkpoint frequency (12 checkpoints in one run) and a confirmed *test* file:",
        "  candidates are re-offered only on new non-lexical evidence, checkpoints are",
        "  capped per run, and only source candidates are offered. No further tuning",
        "  was done after r2.",
        "- **Scoring.** Source files only, at k equal to the number of predicted",
        "  source files, as in the frozen V1 development methodology. Abstention",
        "  scores zero. Provider-failed runs are excluded from behaviour rows, listed",
        "  separately, and retried at most once.",
        "",
        "## Safety findings",
        "",
        (
            f"- Unsupported claims accepted across all {len(records)} runs: "
            f"{sum(len(row['unsupported_accepted_claims']) for row in records)}."
        ),
        "- Deterministic adversarial tests (`tests/test_v2_protocol.py`) show that",
        "  fabricated or unoffered evidence IDs, evidence that did not touch the",
        "  target, lexical-only confirmations, action-phase confirm/reject updates,",
        "  and invented report components are all refused, and that keeping every",
        "  candidate still ends in abstention within the stall bound.",
        "- Wrong-target calls are refused before execution with guidance listing",
        "  real indexed symbols; nothing is silently corrected.",
        "",
        "## Per run",
        "",
        (
            "| Case | Protocol | Status | Stop | Conf/Susp/Rej | Tools | Dups | Model calls "
            "| Tokens | Runtime s | Invalid targets | Checkpoints | Unsupported accepted "
            "| P / R |"
        ),
        "|" + "---|" * 14,
    ]
    for row in records:
        score = (
            f"{row['precision']:.2f} / {row['recall']:.2f}"
            if "precision" in row
            else "n/a"
        )
        lines.append(
            f"| {row['case']} | {row['protocol_revision']} | {row['status']} "
            f"| {row['stop_reason']} "
            f"| {row['confirmed']}/{row['suspected']}/{row['rejected']} | {row['tool_calls']} "
            f"| {row['duplicate_calls']} | {row['model_calls']} | {row['total_tokens']} "
            f"| {row['runtime_seconds']} | {row['invalid_tool_targets']} "
            f"| {row['decision_checkpoints']} | {len(row['unsupported_accepted_claims'])} "
            f"| {score} |"
        )
    lines += [
        "",
        "## By protocol revision",
        "",
        "Behaviour rows exclude runs that ended on a provider failure; those are",
        "counted separately and were retried at most once.",
        "",
        "| Metric | V1 | V2-r1 | V2-r2 |",
        "|---|---:|---:|---:|",
    ]
    groups = {
        name: [row for row in records if row["protocol_revision"] == name]
        for name in COLUMNS
    }
    clean = {
        name: [row for row in rows if not row["provider_error"]]
        for name, rows in groups.items()
    }
    rows = [
        ("Runs (all)", lambda g, c: str(len(g))),
        ("Provider failures", lambda g, c: str(len(g) - len(c))),
        ("Completed", lambda g, c: str(sum(r["status"] == "completed" for r in c))),
        (
            "Partial (confirmed, not submitted)",
            lambda g, c: str(sum(r["status"] == "partial" for r in c)),
        ),
        ("Abstained", lambda g, c: str(sum(r["status"] == "abstained" for r in c))),
        (
            "Runs with ≥1 confirmed candidate",
            lambda g, c: str(sum(r["confirmed"] > 0 for r in c)),
        ),
        (
            "Unsupported claims accepted",
            lambda g, c: str(sum(len(r["unsupported_accepted_claims"]) for r in g)),
        ),
        ("Mean tool calls", lambda g, c: _mean(c, "tool_calls")),
        ("Mean model calls", lambda g, c: _mean(c, "model_calls")),
        ("Mean decision checkpoints", lambda g, c: _mean(c, "decision_checkpoints")),
        ("Mean duplicate calls", lambda g, c: _mean(c, "duplicate_calls")),
        (
            "Mean invalid tool-target attempts",
            lambda g, c: _mean(c, "invalid_tool_targets"),
        ),
        ("Mean tokens", lambda g, c: _mean(c, "total_tokens")),
        ("Mean runtime (s)", lambda g, c: _mean(c, "runtime_seconds")),
        ("Mean precision (cases with gold)", lambda g, c: _mean(c, "precision")),
        ("Mean recall (cases with gold)", lambda g, c: _mean(c, "recall")),
    ]
    for label, compute in rows:
        lines.append(
            f"| {label} | "
            + " | ".join(compute(groups[name], clean[name]) for name in COLUMNS)
            + " |"
        )
    known = [row for row in records if row["case"] == "ripple_cli_markdown_export"]
    lines += [
        "",
        "## Known case: CLI Markdown export on RIPPLE itself",
        "",
        "| Revision | Status | Stop | Predicted files | Includes `src/ripple/cli.py` |",
        "|---|---|---|---|---|",
    ]
    for row in known:
        files = ", ".join(f"`{path}`" for path in row["predicted_files"]) or "none"
        includes = "yes" if "src/ripple/cli.py" in row["predicted_files"] else "no"
        lines.append(
            f"| {row['protocol_revision']} | {row['status']} | {row['stop_reason']} "
            f"| {files} | {includes} |"
        )
    failures = [row for row in records if row["provider_error"]]
    lines += ["", "## Provider failures", ""]
    lines += [
        f"- {row['case']} ({row['protocol_revision']}): {row['provider_error']}"
        for row in failures
    ] or ["- none"]
    lines += [
        "",
        "## Observations from the traces",
        "",
        "- In both r2 runs on the known case, `src/ripple/cli.py` was offered at a",
        "  checkpoint with inspect, reference, and test evidence, and the model chose",
        "  *keep*, asking for evidence that the CLI already contains the export",
        "  command. For a new feature that evidence cannot exist, so the decision",
        "  question itself appears to be the next bottleneck.",
        "- The first V2 graphene run confirmed the right class, but the model's",
        "  report draft labelled it `new_file`; the validator correctly dropped it.",
        "- V1's failures in these runs show the same pattern as the motivating live",
        "  run: few or no ledger proposals, many duplicate and wrong-target calls.",
        "",
        "## Limitations",
        "",
        "- Tiny sample: one run per case and protocol (two for the known case), no",
        "  provider seed control, and a model whose behaviour varies run to run.",
        "  V1 itself completed the known case in one of its two runs.",
        "- Cases were chosen after V1's results were known, and r2 was revised after",
        "  seeing r1. These numbers cannot support a general superiority claim.",
        "- V2 makes the model *decide*; it does not make it *investigate the right",
        "  files*. Wrong confirmations backed by real evidence (for example a",
        "  plausible but unchanged file) are still possible and lower precision.",
        "- A confirmed candidate can still be lost at the report-draft step if the",
        "  model mislabels it (the validator then correctly drops it); that step is",
        "  shared with V1 and unchanged.",
        "",
        "Invalid tool-target attempts count V2 pre-execution refusals, argument",
        "validation failures, and executed `find_references` calls that failed. An",
        "unsupported accepted claim is a report component that is not a confirmed",
        "ledger target, not an indexed file/symbol, or cites unknown evidence.",
    ]
    README.write_text("\n".join(lines) + "\n")


def main() -> int:
    records = _load()
    if "--summary" not in sys.argv:
        try:
            llm = OpenAILLM.from_env()
        except LLMError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        for case in _cases():
            wanted = REPEATS.get(case["id"], 1)
            for protocol in PROTOCOLS:
                revision = REVISIONS[protocol]
                while True:
                    attempts = [
                        row
                        for row in records
                        if row["case"] == case["id"]
                        and row["protocol_revision"] == revision
                    ]
                    successes = [row for row in attempts if not row["provider_error"]]
                    # At most one retry per required run after a provider failure.
                    if len(successes) >= wanted or len(attempts) >= 2 * wanted:
                        break
                    record = _run(case, protocol, llm)
                    records.append(record)
                    _save(records)
                    print(
                        f"{case['id']:<34} {revision:<6} {record['status']:<10} "
                        f"{record['stop_reason']:<20} conf={record['confirmed']} "
                        f"tools={record['tool_calls']} calls={record['model_calls']}",
                        flush=True,
                    )
    write_readme(records)
    shutil.rmtree(WORKSPACE / "runs", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
