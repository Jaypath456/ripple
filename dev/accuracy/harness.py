"""Offline P3 accuracy matrix over synthetic development tasks.

A deterministic simulated model drives the real AgentController, tools, ledger,
validator and report retention. The model never receives gold labels: gold is read
only when scoring. Because configurations change tool selection, each configuration
produces its own trajectory; this is a controlled synthetic comparison, not replay
of recorded provider responses and not a measurement of live model accuracy.

Usage: python dev/accuracy/harness.py [--set development|confirmation] [--output DIR]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dataclasses import replace

import fixtures
import holdout
import request_size

from ripple.agent import (
    RIPPLE_V2,
    RIPPLE_V2_BOUNDED,
    RIPPLE_V2_DIVERSE,
    RIPPLE_V2_GUARD,
    RIPPLE_V2_P3,
    RIPPLE_V2_PROOF,
    RIPPLE_V2_REVIEW,
    RIPPLE_V2_TIERS,
    AgentController,
)
from ripple.agent_models import FeatureRequest
from ripple.llm import LLMResponse
from ripple.proof import DIRECT_TOOLS
from ripple.scanner import scan_repository
from ripple.search import tokenize

RIPPLE_V2_ALL = replace(
    RIPPLE_V2_P3,
    name="RIPPLE-v2-all",
    bounded_context=True,
    review_progression=True,
    precision_guard=True,
)
CONFIGS = {
    "A-baseline": (RIPPLE_V2, False),
    "B-retention": (RIPPLE_V2, True),
    "C-proof-selection": (RIPPLE_V2_PROOF, False),
    "D-diverse-discovery": (RIPPLE_V2_DIVERSE, False),
    "E-review-progression": (RIPPLE_V2_REVIEW, False),
    "F-bounded-context": (RIPPLE_V2_BOUNDED, False),
    "H-precision-guard": (RIPPLE_V2_GUARD, False),
    "G-without-guard": (replace(RIPPLE_V2_ALL, precision_guard=False), True),
    "G-combined": (RIPPLE_V2_ALL, True),
    "G0-combined-no-retention": (RIPPLE_V2_ALL, False),
    "P3a-proof+diverse+retention": (RIPPLE_V2_P3, True),
    "T-confidence-tiers": (RIPPLE_V2_TIERS, False),
    "GT-combined+tiers": (replace(RIPPLE_V2_ALL, confidence_tiers=True), True),
}
OPERATION_KEYS = (
    ("search_terms", "interpret"),
    ("tool_name", "choose_next_action"),
    ("decisions", "decide_candidates"),
    ("affected_components", "draft_report"),
)
TASK_SETS = {
    "development": (fixtures.REPOSITORIES, fixtures.TASKS),
    "confirmation": (holdout.REPOSITORIES, holdout.TASKS),
}
POLICIES = ("observed", "guided")
STOP = {
    "a",
    "an",
    "the",
    "to",
    "of",
    "in",
    "on",
    "for",
    "and",
    "or",
    "when",
    "so",
    "it",
    "is",
    "at",
    "by",
    "use",
    "used",
    "using",
    "instead",
    "always",
    "add",
    "new",
    "show",
    "make",
    "fix",
    "from",
    "with",
    "that",
    "this",
    "be",
    "its",
    "s",
}
GENERIC = {
    "py",
    "init",
    "tests",
    "test",
    "file",
    "defines",
    "symbols",
    "symbol",
    "function",
    "class",
    "none",
    "lines",
    "def",
    "more",
    "method",
}


def _norm(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def terms(text: str) -> set[str]:
    return {_norm(t) for t in tokenize(text)} - STOP - GENERIC


def _data(prompt: str) -> dict:
    match = re.search(r"<repository_data>\n(.*)\n</repository_data>", prompt, re.DOTALL)
    return json.loads(match.group(1)) if match else {}


class SimulatedModel:
    """Deterministic stand-in for the live model; it never sees gold labels."""

    model = "simulated-p3-v1"

    def __init__(self, task: dict, policy: str) -> None:
        self.task = task
        self.policy = policy
        self.request_terms = terms(task["request"])
        self.visits: dict[str, int] = {}
        self.done: set[str] = set()
        self.searched = False
        self.prompt_bytes = 0
        self.estimates: list[int] = []

    def _reply(self, prompt: str, payload) -> LLMResponse:
        self.prompt_bytes += len(prompt.encode())
        operation = next(op for key, op in OPERATION_KEYS if key in payload)
        self.estimates.append(request_size.estimate(operation, prompt))
        return LLMResponse(payload=payload, model=self.model)

    def interpret(self, prompt: str) -> LLMResponse:
        return self._reply(
            prompt,
            {
                "summary": self.task["request"][:200],
                "change_kinds": ["business_logic"],
                "search_terms": self.task["search_terms"],
            },
        )

    # ------------------------------------------------------------ action policies

    def _observed(self, data: dict):
        """Recorded live failure modes: fixation, repeats, file-path references."""

        query = " ".join(self.task["search_terms"][:3])
        for item in data.get("ledger", []):
            target = item["target"]
            if item["status"] == "rejected" or target.startswith("tests/"):
                continue
            step = self.visits.get(target, 0)
            if step >= 5:
                continue
            self.visits[target] = step + 1
            path = target.partition("::")[0]
            return [
                ("inspect_symbol", {"target": target}),
                (
                    "find_references",
                    {"symbol_id": target if "::" in target else path, "limit": 10},
                ),
                ("search_code", {"query": query, "limit": 10}),
                ("inspect_symbol", {"target": target}),
                ("find_tests", {"target": path}),
            ][step]
        return ("search_code", {"query": query, "limit": 10})

    def _guided(self, data: dict):
        """Follows the controller's needs hints in ledger order."""

        patterns = (
            (r"^inspect_symbol\(target=([^)\s]+)\)", "inspect_symbol", "target"),
            (r"^find_references\(symbol_id=([^)\s|]+)", "find_references", "symbol_id"),
            (r"^find_tests\(target=([^)\s]+)\)", "find_tests", "target"),
        )
        for item in data.get("ledger", []):
            if item["status"] == "rejected":
                continue
            for need in item.get("needs", []):
                for pattern, tool, key in patterns:
                    match = re.match(pattern, need)
                    if match:
                        call = (tool, {key: match.group(1)})
                        marker = json.dumps(call, sort_keys=True)
                        if marker not in self.done:
                            self.done.add(marker)
                            return call
        if not self.searched:
            self.searched = True
            return ("search_code", {"query": " ".join(self.task["search_terms"])})
        return None

    def choose_next_action(self, prompt: str) -> LLMResponse:
        data = _data(prompt)
        if data.get("submission_ready"):
            call = None
        elif self.policy == "observed":
            call = self._observed(data)
        else:
            call = self._guided(data)
        if call is None:
            payload = {"tool_name": "submit_report", "arguments": {}, "reason": "done"}
        else:
            payload = {
                "tool_name": call[0],
                "arguments": call[1],
                "reason": "simulated investigation",
                "ledger_updates": [],
            }
        return self._reply(prompt, payload)

    # ------------------------------------------------------------------- judge

    def decide_candidates(self, prompt: str) -> LLMResponse:
        """Fixed lexical relevance judge over the evidence Python presented."""

        decisions = []
        for item in _data(prompt).get("candidates", []):
            target = item["target"]
            path = target.partition("::")[0]
            evidence = item["evidence"]
            inspected = [
                e
                for e in evidence
                if e["tool"] == "inspect_symbol"
                and str(e["arguments"].get("target", "")).partition("::")[0] == path
            ]
            relevance = terms(target.replace("/", " "))
            for record in inspected:
                relevance |= terms(record["shows"])
            # Like a model reading the whole prompt, the judge also reads context-only
            # symbol details; it still cites only listed evidence.
            for detail in item.get("symbol_details", {}).get("items", []):
                relevance |= terms(detail["shows"])
            score = len(relevance & self.request_terms)
            strong = [e["evidence_id"] for e in evidence if e["strong"]]
            direct = [e["evidence_id"] for e in inspected if e["strong"]]
            if score >= 2 and strong:
                decisions.append(
                    {
                        "target": target,
                        "decision": "confirm",
                        "evidence_ids": (direct or strong)[:1],
                        "reason": "evidence matches the requested behaviour",
                    }
                )
            elif score == 0 and inspected:
                decisions.append(
                    {
                        "target": target,
                        "decision": "reject",
                        "evidence_ids": [inspected[0]["evidence_id"]],
                        "reason": "inspected content is unrelated to the request",
                    }
                )
            else:
                decisions.append(
                    {
                        "target": target,
                        "decision": "keep",
                        "reason": "relevance unresolved",
                        "missing_evidence": "a caller or test relationship",
                    }
                )
        return self._reply(prompt, {"decisions": decisions})

    def draft_report(self, prompt: str) -> LLMResponse:
        """Lossy drafting: at most two components, citing the latest ledger ID."""

        confirmed = [
            item
            for item in _data(prompt).get("ledger", [])
            if item.get("status") == "confirmed"
        ]
        return self._reply(
            prompt,
            {
                "affected_components": [
                    {
                        "target": item["target"],
                        "change_type": "modify",
                        "change_kind": "business_logic",
                        "reason": "implements the requested change",
                        "confidence": "medium",
                        "evidence": item["evidence_ids"][-1:],
                    }
                    for item in confirmed[:2]
                ]
            },
        )


def build_repository(files: dict[str, str], destination: Path):
    for name, text in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    }
    for args in (("init", "-q"), ("add", "."), ("commit", "-qm", "P3 dev fixture")):
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=RIPPLE P3",
                "-c",
                "user.email=ripple@example.test",
                "-C",
                str(destination),
                *args,
            ],
            env=env,
            check=True,
            capture_output=True,
        )
    return scan_repository(destination)


def _files(targets) -> list[str]:
    return sorted({t.partition("::")[0] for t in targets if not t.startswith("tests/")})


ORIGINS = ("seed", "structural", "other")


def _tiers(trace, predicted, gold) -> dict:
    """Primary vs late tier, with the controller's definition of a late file.

    A file is late when its first accepted confirmation came after a deferred
    submission. Late-tier counts also tally the mapped-tests observation, which
    was derived from development cases and is reported, never used for ranking.
    """

    deferred, early, after = False, set(), set()
    for event in trace:
        if event["event"] == "submission_deferred":
            deferred = True
        elif (
            event["event"] == "candidate_decision"
            and event["accepted"]
            and event["decision"]["decision"] == "confirm"
        ):
            (after if deferred else early).add(
                event["decision"]["target"].partition("::")[0]
            )
    late = (after - early) & predicted
    primary = predicted - late
    observed = next(
        (e["observations"] for e in trace if e["event"] == "confidence_tiers"), {}
    )
    tested = {path for path, flags in observed.items() if flags.get("mapped_tests")}
    return {
        "primary_tp": len(primary & gold),
        "primary_fp": len(primary - gold),
        "secondary_tp": len(late & gold),
        "secondary_fp": len(late - gold),
        "late_tested_tp": len(late & tested & gold),
        "late_tested_fp": len((late & tested) - gold),
        "late_untested_tp": len((late - tested) & gold) if observed else 0,
        "late_untested_fp": len((late - tested) - gold) if observed else 0,
    }


def _origin(path: str, seeds, structural) -> str:
    if path in seeds:
        return "seed"
    if path in {t.partition("::")[0] for t in structural}:
        return "structural"
    return "other"


def _direct_confirmation(candidate, ledger) -> bool:
    """Whether the confirmation cited evidence aimed at the candidate's own file."""

    path = candidate.target.partition("::")[0]
    entry = next(
        (h for h in reversed(candidate.history) if h.get("status") == "confirmed"), {}
    )
    for evidence_id in entry.get("evidence_ids", ()):
        record = ledger.evidence.get(evidence_id)
        if record is None or record.tool_name not in DIRECT_TOOLS:
            continue
        aimed = record.arguments.get("target", record.arguments.get("symbol_id", ""))
        if str(aimed).partition("::")[0] == path:
            return True
    return False


def run_task(task, config: str, policy: str, index, output: Path) -> dict:
    variant, retain = CONFIGS[config]
    model = SimulatedModel(task, policy)
    controller = AgentController(
        index,
        model,
        output_root=output,
        variant=variant,
        retain_confirmed_report=retain,
    )
    run = controller.run(FeatureRequest(text=task["request"]))
    trace = [json.loads(line) for line in run.trace_path.read_text().splitlines()]
    gold = set(task["gold"])
    candidates = controller.ledger.candidates.values()
    predicted = set(_files(c.target for c in run.report.affected_components))
    refusals = {
        "tool_target": sum(e["event"] == "tool_target_rejected" for e in trace),
        "tool_arguments": sum(
            e["event"] == "validation_error" and e.get("operation") == "tool"
            for e in trace
        ),
        "submission": sum(e["event"] == "submit_rejected" for e in trace),
        "checkpoint": sum(
            e["event"] == "candidate_decision"
            and not e["accepted"]
            and e.get("reason_code") is not None
            for e in trace
        ),
    }
    stats = run.report.run_stats
    # Seeds cite the first search_code evidence; checkpoint decisions rewrite reasons.
    seed_evidence = next(
        (e["evidence_id"] for e in trace if e["event"] == "tool_result"), None
    )
    seed_files = _files(
        c.target for c in candidates if c.evidence_ids[:1] == [seed_evidence]
    )
    return {
        "task_id": task["task_id"],
        "kind": task["kind"],
        "config": config,
        "policy": policy,
        "status": run.report.status,
        "stop_reason": stats.stop_reason,
        "gold": sorted(gold),
        "seed_files": seed_files,
        "candidate_files": _files(c.target for c in candidates),
        "confirmed_files": _files(
            c.target for c in candidates if c.status == "confirmed"
        ),
        "predicted_files": sorted(predicted),
        "true_positives": len(predicted & gold),
        "false_positives": len(predicted - gold),
        "false_negatives": len(gold - predicted),
        # Useful proof work: source candidates a checkpoint decided, and those whose
        # reference and test checks both completed.
        "decided_candidates": sum(
            c.status != "suspected" and not c.target.startswith("tests/")
            for c in candidates
        ),
        "fully_checked_candidates": sum(
            c.checked_refs and c.checked_tests and not c.target.startswith("tests/")
            for c in candidates
        ),
        "tool_calls": stats.tool_calls,
        "duplicate_calls": stats.duplicate_calls,
        "llm_calls": stats.llm_calls,
        "checkpoints": stats.decision_checkpoints,
        "proof_steps": controller.proof_steps,
        "structural_admissions": sorted(controller.structural),
        "refusals": refusals,
        "prompt_bytes": model.prompt_bytes,
        "max_request_estimate": max(model.estimates, default=0),
        "over_cap_requests": sum(e > request_size.CAP for e in model.estimates),
        "fp_by_origin": {
            origin: sum(
                _origin(path, seed_files, controller.structural) == origin
                for path in predicted - gold
            )
            for origin in ORIGINS
        },
        "tp_by_origin": {
            origin: sum(
                _origin(path, seed_files, controller.structural) == origin
                for path in predicted & gold
            )
            for origin in ORIGINS
        },
        "indirect_confirmations": sum(
            not _direct_confirmation(c, controller.ledger)
            for c in controller.ledger.confirmed()
        ),
        "retired": len(controller.retired),
        **_tiers(trace, predicted, gold),
        "tokens": stats.total_tokens,
        "censored": run.report.status == "failed"
        or stats.stop_reason in {"controller_error", "token_ceiling"},
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _operating_point(tp: int, fp: int, gold: int) -> dict:
    """Primary-tier-only precision/recall/F1 (late confirmations excluded)."""

    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, gold)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision and recall
        else 0.0
    )
    return {"primary_precision": precision, "primary_recall": recall, "primary_f1": f1}


def aggregate(rows: list[dict]) -> dict:
    gold = sum(len(r["gold"]) for r in rows)
    tp = sum(r["true_positives"] for r in rows)
    fp = sum(r["false_positives"] for r in rows)
    fn = sum(r["false_negatives"] for r in rows)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision and recall
        else 0.0
    )
    empty = [r for r in rows if not r["gold"]]
    return {
        "tasks": len(rows),
        "gold_files": gold,
        "seed_recall": _ratio(
            sum(len(set(r["seed_files"]) & set(r["gold"])) for r in rows), gold
        ),
        "candidate_recall": _ratio(
            sum(len(set(r["candidate_files"]) & set(r["gold"])) for r in rows), gold
        ),
        "confirmed_files": sum(len(r["confirmed_files"]) for r in rows),
        "confirmed_gold_files": sum(
            len(set(r["confirmed_files"]) & set(r["gold"])) for r in rows
        ),
        "predicted_files": sum(len(r["predicted_files"]) for r in rows),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "abstentions": sum(r["status"] == "abstained" for r in rows),
        "abstention_rate": _ratio(
            sum(r["status"] == "abstained" for r in rows), len(rows)
        ),
        "empty_gold_tasks": len(empty),
        "correct_abstentions": sum(not r["predicted_files"] for r in empty),
        "refusals": sum(sum(r["refusals"].values()) for r in rows),
        "decided_candidates": sum(r["decided_candidates"] for r in rows),
        "fully_checked_candidates": sum(r["fully_checked_candidates"] for r in rows),
        "duplicate_calls": sum(r["duplicate_calls"] for r in rows),
        "tool_calls": sum(r["tool_calls"] for r in rows),
        "proof_steps": sum(r["proof_steps"] for r in rows),
        "llm_calls": sum(r["llm_calls"] for r in rows),
        "prompt_bytes": sum(r["prompt_bytes"] for r in rows),
        "tokens": "unavailable: the simulated model reports no usage",
        "censored": sum(r["censored"] for r in rows),
        "max_request_estimate": max(r["max_request_estimate"] for r in rows),
        "over_cap_requests": sum(r["over_cap_requests"] for r in rows),
        "fp_by_origin": {o: sum(r["fp_by_origin"][o] for r in rows) for o in ORIGINS},
        "tp_by_origin": {o: sum(r["tp_by_origin"][o] for r in rows) for o in ORIGINS},
        "indirect_confirmations": sum(r["indirect_confirmations"] for r in rows),
        "retired": sum(r["retired"] for r in rows),
        **{
            key: sum(r[key] for r in rows)
            for key in (
                "primary_tp",
                "primary_fp",
                "secondary_tp",
                "secondary_fp",
                "late_tested_tp",
                "late_tested_fp",
                "late_untested_tp",
                "late_untested_fp",
            )
        },
        **_operating_point(
            sum(r["primary_tp"] for r in rows),
            sum(r["primary_fp"] for r in rows),
            gold,
        ),
        "stop_reasons": {
            reason: sum(r["stop_reason"] == reason for r in rows)
            for reason in sorted({r["stop_reason"] for r in rows})
        },
        "submitted": sum(r["stop_reason"] == "submitted" for r in rows),
    }


def run_matrix(workdir: Path, task_set: str) -> dict:
    repositories, tasks = TASK_SETS[task_set]
    indexes = {
        name: build_repository(files, workdir / "repos" / name)
        for name, files in repositories.items()
    }
    rows = []
    for policy in POLICIES:
        for config in CONFIGS:
            for task in tasks:
                rows.append(
                    run_task(
                        task,
                        config,
                        policy,
                        indexes[task["repository"]],
                        workdir / "runs" / policy / config / task["task_id"],
                    )
                )
    summary = {
        policy: {
            config: aggregate(
                [r for r in rows if r["policy"] == policy and r["config"] == config]
            )
            for config in CONFIGS
        }
        for policy in POLICIES
    }
    return {
        "scope": (
            "Synthetic development tasks authored in-sprint with a deterministic "
            "simulated model; not held out, not live inference, not comparable to "
            "historical FEA-Bench F1."
        ),
        "task_set": task_set,
        "tasks": len(tasks),
        "configs": list(CONFIGS),
        "policies": list(POLICIES),
        "summary": summary,
        "rows": rows,
    }


def markdown(results: dict) -> str:
    keys = (
        "tasks",
        "seed_recall",
        "candidate_recall",
        "confirmed_files",
        "predicted_files",
        "true_positives",
        "false_positives",
        "false_negatives",
        "precision",
        "recall",
        "f1",
        "abstentions",
        "correct_abstentions",
        "refusals",
        "decided_candidates",
        "fully_checked_candidates",
        "duplicate_calls",
        "tool_calls",
        "proof_steps",
        "submitted",
        "censored",
        "max_request_estimate",
        "over_cap_requests",
        "fp_by_origin",
        "tp_by_origin",
        "indirect_confirmations",
        "primary_tp",
        "primary_fp",
        "secondary_tp",
        "secondary_fp",
        "primary_precision",
        "primary_recall",
        "primary_f1",
        "retired",
        "stop_reasons",
    )
    lines = []
    for policy, table in results["summary"].items():
        lines += [f"### Simulated policy: {policy}", ""]
        lines.append("| metric | " + " | ".join(table) + " |")
        lines.append("|---" * (len(table) + 1) + "|")
        for key in keys:
            lines.append(
                f"| {key} | " + " | ".join(str(table[c][key]) for c in table) + " |"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write results JSON/Markdown")
    parser.add_argument("--set", choices=sorted(TASK_SETS), default="development")
    args = parser.parse_args(argv)
    for key in [k for k in os.environ if "API_KEY" in k.upper()]:
        del os.environ[key]
    with tempfile.TemporaryDirectory(prefix="ripple-p3-matrix-") as folder:
        results = run_matrix(Path(folder), args.set)
    table = markdown(results)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / f"{args.set}.json").write_text(
            json.dumps(results, indent=2, sort_keys=True) + "\n"
        )
        (args.output / f"{args.set}.md").write_text(table + "\n")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
