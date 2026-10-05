"""V2 agent protocol: decision checkpoints, target checks, dedup, and stall bound.

The regression scenario reproduces the V1 live failure on RIPPLE's own repository:
"Add a CLI command that exports the latest Stage B verification report as a Markdown
summary." Discovery worked, evidence existed, but no candidate left SUSPECTED.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ripple.agent import (
    AGENT_VARIANTS,
    CHECKPOINT_INTERVAL,
    RIPPLE_V2,
    AgentController,
)
from ripple.agent_models import CandidateUpdate, FeatureRequest
from ripple.ledger import CandidateLedger, EvidenceRecord
from ripple.llm import LLMResponse, ScriptedLLM
from ripple.scanner import scan_repository
from ripple.tools import ToolResult

FIXTURES = Path(__file__).parent / "fixtures"
REQUEST = (
    "Add a CLI command that exports the latest Stage B verification report as a "
    "Markdown summary."
)
INTENT = {
    "summary": "Export the latest Stage B verification report as Markdown",
    "change_kinds": ["api"],
    "search_terms": ["verification", "markdown", "cli", "latest", "export"],
}
CLI = "src/app/cli.py"
RENDER = "src/app/verification_render.py"
# Mirrors the recorded live order, including its wrong-target and repeated calls.
LIVE_LIKE_PLAN = (
    ("inspect_symbol", {"target": CLI}),
    ("find_references", {"symbol_id": f"{CLI}::_run_verify", "limit": 10}),
    ("search_code", {"query": "render_verification_markdown", "kind": "symbol"}),
    ("find_tests", {"target": CLI}),
    ("find_references", {"symbol_id": CLI, "limit": 40}),
    ("inspect_symbol", {"target": CLI}),
    ("find_references", {"symbol_id": f"{CLI}::main", "limit": 40}),
    ("inspect_symbol", {"target": RENDER}),
    ("find_references", {"symbol_id": f"{RENDER}::render_verification_markdown"}),
    ("find_tests", {"target": f"{RENDER}::render_verification_markdown"}),
    ("find_references", {"symbol_id": f"{CLI}::main", "limit": 20}),
    ("find_references", {"symbol_id": CLI, "limit": 10}),
    ("inspect_symbol", {"target": CLI}),
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit_fixture(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    _git(destination, "init", "-q")
    _git(destination, "config", "user.name", "RIPPLE Tests")
    _git(destination, "config", "user.email", "ripple@example.test")
    _git(destination, "add", ".")
    _git(destination, "commit", "-qm", "fixture")
    return destination


@pytest.fixture
def cli_repo(tmp_path: Path):
    return scan_repository(
        _commit_fixture(FIXTURES / "cli_export_app", tmp_path / "repo")
    )


def _data(prompt: str) -> dict:
    match = re.search(r"<repository_data>\n(.*)\n</repository_data>", prompt, re.DOTALL)
    assert match, "prompt lacks repository_data"
    return json.loads(match.group(1))


class ObservedPolicyLLM:
    """Test double modelled on the recorded live gpt-oss-20b behaviour.

    Action selection never fills ledger_updates (0 of 51 real live decisions did)
    and follows a fixed plan, repeating its last step when the plan runs out. It
    submits only when the prompt reports submission_ready. At a V2 checkpoint it
    confirms targets it considers relevant with a listed strong record, rejects
    listed distractors, and keeps the rest with a stated missing-evidence note.
    """

    model = "observed-policy"

    def __init__(
        self,
        plan=LIVE_LIKE_PLAN,
        relevant=frozenset({CLI, RENDER}),
        distractors=frozenset({"src/app/render.py"}),
        cite_any=False,
    ) -> None:
        self.plan = plan
        self.relevant = relevant
        self.distractors = distractors
        self.cite_any = cite_any
        self.position = 0
        self.checkpoint_prompts: list[dict] = []

    def _reply(self, payload) -> LLMResponse:
        return LLMResponse(payload=payload, model=self.model)

    def interpret(self, prompt: str) -> LLMResponse:
        return self._reply(INTENT)

    def choose_next_action(self, prompt: str) -> LLMResponse:
        if _data(prompt).get("submission_ready"):
            return self._reply(
                {"tool_name": "submit_report", "arguments": {}, "reason": "done"}
            )
        tool, arguments = self.plan[min(self.position, len(self.plan) - 1)]
        self.position += 1
        return self._reply(
            {
                "tool_name": tool,
                "arguments": arguments,
                "reason": "investigate",
                "ledger_updates": [],
            }
        )

    def decide_candidates(self, prompt: str) -> LLMResponse:
        data = _data(prompt)
        self.checkpoint_prompts.append(data)
        decisions = []
        for candidate in data["candidates"]:
            path = candidate["target"].partition("::")[0]
            listed = [item["evidence_id"] for item in candidate["evidence"]]
            strong = [
                item["evidence_id"] for item in candidate["evidence"] if item["strong"]
            ]
            if path in self.relevant:
                cited = listed if self.cite_any else strong[:1]
                decisions.append(
                    {
                        "target": candidate["target"],
                        "decision": "confirm",
                        "evidence_ids": cited,
                        "reason": "The new command and its rendering live here.",
                    }
                )
            elif path in self.distractors:
                decisions.append(
                    {
                        "target": candidate["target"],
                        "decision": "reject",
                        "evidence_ids": listed[:1],
                        "reason": "Renders Stage A reports, not verification reports.",
                    }
                )
            else:
                decisions.append(
                    {
                        "target": candidate["target"],
                        "decision": "keep",
                        "reason": "Not yet clear.",
                        "missing_evidence": "a caller or test linking it to the CLI",
                    }
                )
        return self._reply({"decisions": decisions})

    def draft_report(self, prompt: str) -> LLMResponse:
        confirmed = [
            item for item in _data(prompt)["ledger"] if item["status"] == "confirmed"
        ]
        return self._reply(
            {
                "affected_components": [
                    {
                        "target": item["target"],
                        "change_type": "modify",
                        "change_kind": "api",
                        "reason": "Add the export command path.",
                        "confidence": "high",
                        "evidence": item["evidence_ids"][-1:],
                    }
                    for item in confirmed
                ]
            }
        )


def _run(index, llm, tmp_path: Path, variant):
    controller = AgentController(
        index, llm, output_root=tmp_path / variant.name, variant=variant
    )
    run = controller.run(FeatureRequest(text=REQUEST))
    trace = [json.loads(line) for line in run.trace_path.read_text().splitlines()]
    return controller, run, trace


def _states(controller) -> dict[str, list[str]]:
    states: dict[str, list[str]] = {"suspected": [], "confirmed": [], "rejected": []}
    for item in controller.ledger.candidates.values():
        states[item.status].append(item.target)
    return states


# ----------------------------------------------------------------- regression


def test_v1_protocol_reproduces_the_live_failure(cli_repo, tmp_path: Path) -> None:
    v1, run, trace = _run(
        cli_repo, ObservedPolicyLLM(), tmp_path, AGENT_VARIANTS["RIPPLE"]
    )
    states = _states(v1)
    assert run.report.status == "abstained"
    assert run.report.run_stats.stop_reason == "no_progress"
    assert states["confirmed"] == [] and len(states["suspected"]) >= 3
    # The evidence existed: candidates passed both gate checks yet stayed suspected.
    ready = [
        item
        for item in v1.ledger.candidates.values()
        if item.checked_refs and item.checked_tests
    ]
    assert ready and all(item.status == "suspected" for item in ready)
    assert not any(event["event"] == "checkpoint_started" for event in trace)


def test_v2_protocol_converts_the_same_evidence_into_a_report(
    cli_repo, tmp_path: Path
) -> None:
    _v1, v1_run, _ = _run(
        cli_repo, ObservedPolicyLLM(), tmp_path, AGENT_VARIANTS["RIPPLE"]
    )
    v2, v2_run, trace = _run(cli_repo, ObservedPolicyLLM(), tmp_path, RIPPLE_V2)
    report = v2_run.report
    assert report.status == "completed"
    assert report.run_stats.stop_reason == "submitted"
    assert report.run_stats.config_version == "full-report-v2.1"
    states = _states(v2)
    confirmed_paths = {target.partition("::")[0] for target in states["confirmed"]}
    assert CLI in confirmed_paths
    assert {item.target for item in report.affected_components} <= set(
        states["confirmed"]
    ) | {item.target for item in report.affected_components if "<" in item.target}
    # Fewer model and tool calls than V1 on the same scenario.
    assert report.run_stats.llm_calls < v1_run.report.run_stats.llm_calls
    assert report.run_stats.tool_calls <= v1_run.report.run_stats.tool_calls
    assert report.run_stats.decision_checkpoints >= 1
    assert any(event["event"] == "candidate_decision" for event in trace)


def test_recorded_live_decisions_reproduce_v1_on_ripple_itself(tmp_path: Path) -> None:
    recorded = json.loads((FIXTURES / "v1_live_cli_export.json").read_text())
    root = Path(__file__).resolve().parent.parent
    clone = tmp_path / "ripple"
    try:
        _git(root, "cat-file", "-e", f"{recorded['commit']}^{{commit}}")
    except subprocess.CalledProcessError:
        pytest.skip("recorded commit is not in this clone's history")
    subprocess.run(
        ["git", "clone", "-q", "--local", "--no-checkout", str(root), str(clone)],
        check=True,
    )
    _git(clone, "checkout", "-q", recorded["commit"])
    llm = ScriptedLLM(
        interpretations=[recorded["interpretation"]],
        decisions=recorded["decisions"],
        model=recorded["model"],
    )
    controller = AgentController(
        scan_repository(clone), llm, output_root=tmp_path / "out"
    )
    stats = controller.run(FeatureRequest(text=recorded["request"])).report.run_stats
    outcome = recorded["recorded_outcome"]
    assert (stats.stop_reason, stats.tool_calls, stats.duplicate_calls) == (
        outcome["stop_reason"],
        outcome["tool_calls"],
        outcome["duplicate_calls"],
    )
    assert stats.llm_calls == outcome["llm_calls"]
    assert all(not item["ledger_updates"] for item in recorded["decisions"])
    checked = [
        item
        for item in controller.ledger.candidates.values()
        if item.checked_refs and item.checked_tests
    ]
    assert len(checked) >= 3 and all(item.status == "suspected" for item in checked)


# ----------------------------------------------------------------- safety


def _ledger_with(index, records):
    ledger = CandidateLedger(index)
    for record in records:
        ledger.add_evidence(record)
    return ledger


def _record(evidence_id, tool, touched, strong):
    return EvidenceRecord(
        evidence_id=evidence_id,
        tool_name=tool,
        arguments={},
        result=ToolResult(ok=True, evidence_id=evidence_id, data={}),
        touched_targets=frozenset(touched),
        strong=strong,
    )


def test_checkpoint_decisions_are_verified_by_python(cli_repo) -> None:
    lexical = _record("e1", "search_code", {CLI, RENDER}, False)
    inspect = _record("e2", "inspect_symbol", {CLI}, True)
    ledger = _ledger_with(cli_repo, [lexical, inspect])
    ledger.seed(CLI, "seed", "e1")
    ledger.seed(RENDER, "seed", "e1")
    offered = frozenset({"e1", "e2"})
    # Lexical-only support cannot confirm.
    assert ledger.decide(CLI, "confirm", ("e1",), "match", offered) == (
        False,
        "confirmation needs non-lexical evidence",
    )
    # Fabricated or unoffered evidence IDs are refused.
    assert ledger.decide(CLI, "confirm", ("e999",), "made up", offered)[1] == (
        "cited evidence was not presented for this target"
    )
    # Evidence that did not touch the target is refused even if offered.
    assert ledger.decide(RENDER, "confirm", ("e2",), "wrong", offered)[1] == (
        "cited evidence did not touch this target"
    )
    assert ledger.decide(CLI, "confirm", (), "no evidence", offered)[1] == (
        "no evidence cited"
    )
    assert ledger.decide("src/app/nope.py", "confirm", ("e2",), "x", offered)[1] == (
        "target is not a suspected candidate"
    )
    assert ledger.decide(CLI, "keep", (), "unsure", offered) == (False, None)
    assert ledger.candidates[CLI].status == "suspected"
    assert ledger.decide(CLI, "confirm", ("e2",), "inspected", offered) == (True, None)
    assert ledger.candidates[CLI].status == "confirmed"
    assert ledger.candidates[CLI].history[-1]["via"] == "checkpoint"


def test_v2_action_updates_cannot_confirm_or_reject(cli_repo, tmp_path: Path) -> None:
    confirm = CandidateUpdate(
        target=CLI, status="confirmed", reason="lexical match", evidence_ids=("e1",)
    )
    llm = ScriptedLLM(
        interpretations=[INTENT],
        decisions=[
            {
                "tool_name": "search_code",
                "arguments": {"query": "verification markdown"},
                "reason": "search",
                "ledger_updates": [confirm.model_dump()],
            }
        ]
        * 5,
    )
    _controller, run, trace = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    refused = [
        event
        for event in trace
        if event["event"] == "ledger_update" and "update" in event
    ]
    assert refused and not any(event["accepted"] for event in refused)
    assert refused[0]["refusal"] == "status changes are decided at checkpoints"
    assert run.report.status == "abstained"


def test_unsupported_report_claims_are_still_dropped_under_v2(
    cli_repo, tmp_path: Path
) -> None:
    class Inventive(ObservedPolicyLLM):
        def draft_report(self, prompt: str) -> LLMResponse:
            response = super().draft_report(prompt)
            components = list(response.payload["affected_components"])
            components.append(
                {
                    "target": "src/app/invented.py::export",
                    "change_type": "modify",
                    "change_kind": "api",
                    "reason": "Invented.",
                    "confidence": "high",
                    "evidence": ["e999"],
                }
            )
            return self._reply({"affected_components": components})

    _, run, _ = _run(cli_repo, Inventive(), tmp_path, RIPPLE_V2)
    targets = {item.target for item in run.report.affected_components}
    assert "src/app/invented.py::export" not in targets
    assert any("invented.py" in item for item in run.report.dropped_claims)


def test_citing_lexical_evidence_at_a_checkpoint_cannot_confirm(
    cli_repo, tmp_path: Path
) -> None:
    class LexicalOnly(ObservedPolicyLLM):
        def decide_candidates(self, prompt: str) -> LLMResponse:
            decisions = [
                {
                    "target": candidate["target"],
                    "decision": "confirm",
                    "evidence_ids": [
                        item["evidence_id"]
                        for item in candidate["evidence"]
                        if not item["strong"]
                    ][:1],
                    "reason": "Search matched.",
                }
                for candidate in _data(prompt)["candidates"]
            ]
            return self._reply({"decisions": decisions})

    controller, run, trace = _run(cli_repo, LexicalOnly(), tmp_path, RIPPLE_V2)
    assert _states(controller)["confirmed"] == []
    assert run.report.status == "abstained"
    refusals = {
        event["refusal"] for event in trace if event["event"] == "candidate_decision"
    }
    assert refusals <= {
        "confirmation needs non-lexical evidence",
        "no evidence cited",
    }


# ----------------------------------------------------------------- targets, dedup


def test_file_path_given_to_find_references_is_refused_with_real_symbols(
    cli_repo, tmp_path: Path
) -> None:
    llm = ScriptedLLM(
        interpretations=[INTENT],
        decisions=[
            {
                "tool_name": "find_references",
                "arguments": {"symbol_id": CLI},
                "reason": "references",
            }
        ]
        * 4,
    )
    _controller, run, trace = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    rejected = [event for event in trace if event["event"] == "tool_target_rejected"]
    assert len(rejected) == 4
    guidance = rejected[0]["guidance"]
    assert "requires a symbol_id" in guidance and f"{CLI}::main" in guidance
    known = {item.id for item in cli_repo.symbols}
    hinted = re.findall(r"src/app/cli\.py::\w+", guidance)
    assert hinted and set(hinted) <= known  # only real indexed symbols
    assert run.report.run_stats.tool_calls == 1  # only the seed search ran
    assert run.report.run_stats.invalid_tool_targets == 4
    assert run.report.run_stats.stop_reason == "no_progress"


def test_v1_still_executes_wrong_target_calls(cli_repo, tmp_path: Path) -> None:
    llm = ScriptedLLM(
        interpretations=[INTENT],
        decisions=[
            {
                "tool_name": "find_references",
                "arguments": {"symbol_id": CLI, "limit": limit},
                "reason": "references",
            }
            for limit in (40, 10, 20, 30)
        ],
    )
    _, run, _ = _run(cli_repo, llm, tmp_path, AGENT_VARIANTS["RIPPLE"])
    assert run.report.run_stats.tool_calls == 5
    assert run.report.run_stats.invalid_tool_targets == 0


def test_symbol_id_tools_reject_symbols_for_path_arguments(
    cli_repo, tmp_path: Path
) -> None:
    llm = ScriptedLLM(
        interpretations=[INTENT],
        decisions=[
            {
                "tool_name": "get_dependencies",
                "arguments": {"path": f"{CLI}::main", "direction": "imports"},
                "reason": "deps",
            }
        ]
        * 4,
    )
    _, _, trace = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    guidance = next(e for e in trace if e["event"] == "tool_target_rejected")[
        "guidance"
    ]
    assert f"the file part of {CLI}::main is {CLI}" in guidance


def test_limit_only_repeats_reuse_the_result_in_v2(cli_repo, tmp_path: Path) -> None:
    decisions = [
        {
            "tool_name": "find_references",
            "arguments": {"symbol_id": f"{CLI}::main", "limit": limit},
            "reason": "references",
        }
        for limit in (40, 20, 10, 5)
    ]
    llm = ScriptedLLM(interpretations=[INTENT], decisions=decisions)
    _, v2_run, _ = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    llm = ScriptedLLM(interpretations=[INTENT], decisions=list(decisions))
    _, v1_run, _ = _run(cli_repo, llm, tmp_path, AGENT_VARIANTS["RIPPLE"])
    assert v2_run.report.run_stats.tool_calls == 2  # seed + one reference call
    assert v2_run.report.run_stats.duplicate_calls == 3
    assert v1_run.report.run_stats.tool_calls > v2_run.report.run_stats.tool_calls


# ----------------------------------------------------------------- progress


def test_decision_opportunity_comes_within_the_checkpoint_interval(
    cli_repo, tmp_path: Path
) -> None:
    llm = ObservedPolicyLLM()
    _, _, trace = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    first = next(
        index
        for index, event in enumerate(trace)
        if event["event"] == "checkpoint_started"
    )
    explore_calls = [
        event
        for event in trace[:first]
        if event["event"] == "tool_call" and event["tool"] != "search_code"
    ]
    assert 1 <= len(explore_calls) <= CHECKPOINT_INTERVAL
    offered = llm.checkpoint_prompts[0]["candidates"]
    assert offered and all(
        any(item["strong"] for item in candidate["evidence"]) for candidate in offered
    )


def test_keeping_everything_still_abstains_within_the_stall_bound(
    cli_repo, tmp_path: Path
) -> None:
    llm = ObservedPolicyLLM(relevant=frozenset(), distractors=frozenset())
    controller, run, trace = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    assert run.report.status == "abstained"
    assert _states(controller)["confirmed"] == []
    assert run.report.run_stats.stop_reason in {"no_ledger_progress", "no_progress"}
    assert run.report.run_stats.tool_calls < 25
    notes = [
        event["decision"]["missing_evidence"]
        for event in trace
        if event["event"] == "candidate_decision"
    ]
    assert notes and all(notes)


def test_stall_bound_stops_exploration_without_ledger_changes(
    cli_repo, tmp_path: Path
) -> None:
    words = ("verification", "markdown", "render", "report", "latest", "repo")
    searches = [
        {
            "tool_name": "search_code",
            "arguments": {"query": word, "kind": kind},
            "reason": "search",
        }
        for kind in ("any", "string", "symbol", "file")
        for word in words
    ]
    llm = ScriptedLLM(interpretations=[INTENT], decisions=searches)
    _, v2_run, _ = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    llm = ScriptedLLM(interpretations=[INTENT], decisions=list(searches))
    _, v1_run, _ = _run(cli_repo, llm, tmp_path, AGENT_VARIANTS["RIPPLE"])
    assert v2_run.report.run_stats.stop_reason == "no_ledger_progress"
    assert v2_run.report.run_stats.tool_calls == 1 + 8
    assert v1_run.report.run_stats.tool_calls > v2_run.report.run_stats.tool_calls


def test_rejected_distractor_and_submit_guidance(cli_repo, tmp_path: Path) -> None:
    controller, run, _trace = _run(
        cli_repo,
        ObservedPolicyLLM(distractors=frozenset({"src/app/render.py", RENDER})),
        tmp_path,
        RIPPLE_V2,
    )
    states = _states(controller)
    assert not any(target.startswith(RENDER) for target in states["confirmed"])
    assert run.report.status == "completed"


def test_v1_variants_and_harnesses_stay_on_the_v1_protocol() -> None:
    assert all(variant.protocol == "v1" for variant in AGENT_VARIANTS.values())
    assert RIPPLE_V2.protocol == "v2"


# ----------------------------------------------------------------- V1 history

FROZEN_V1 = {
    "evaluation/final_config.json": (
        "c6a406a05b739dc09f774a1aadaf722ecd2ca9642597b2d38fcf26f1fd21c3e9"
    ),
    "evaluation/final_schedule.json": (
        "51c1d7cbb3238fa81a31071b095606793e26ba8ce9499884949014ae22b7be7a"
    ),
    "evaluation/data/final_fea_tasks.json": (
        "8a0a3df0e9c484f0485c7e45cdea8389dc92b26e3536e338b8da8fb3e4303398"
    ),
    "evaluation/results/final_summary.json": (
        "bc432077e34da057171c42d60cc7764a09513fa3ba1a012ed7137a7b1560571d"
    ),
    "evaluation/results/stage_b_anomalies.json": (
        "e44e1495a2e10fc1d2c70b969a930a4013cf2a31ec363012378c83c0e57afa6f"
    ),
    "evaluation/results/stage_b_ripple_reports.json": (
        "54be2155bac7a2633b9a5ae826ab904666d6b08af64299167efffd4772af4c99"
    ),
}
FROZEN_V1_RAW_TREE = "74250df87f09a1839b773606690b7fb2ea744ca7da30d1d2cffbc6773a03f060"


def test_frozen_final_v1_artifacts_are_byte_identical() -> None:
    import hashlib

    root = Path(__file__).resolve().parent.parent
    for path, digest in FROZEN_V1.items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest, path
    tree = hashlib.sha256()
    for path in sorted((root / "evaluation" / "raw").rglob("*")):
        if path.is_file():
            tree.update(
                path.relative_to(root / "evaluation" / "raw").as_posix().encode()
            )
            tree.update(hashlib.sha256(path.read_bytes()).digest())
    assert tree.hexdigest() == FROZEN_V1_RAW_TREE


def test_lexical_evidence_alone_never_reopens_a_checkpoint(
    cli_repo, tmp_path: Path
) -> None:
    plan = (
        ("inspect_symbol", {"target": CLI}),
        ("find_tests", {"target": CLI}),
        ("find_references", {"symbol_id": f"{CLI}::main"}),
        *(
            ("search_code", {"query": word, "kind": "any"})
            for word in ("verify", "parser", "latest", "command", "argparse")
        ),
    )
    llm = ObservedPolicyLLM(plan=plan, relevant=frozenset(), distractors=frozenset())
    _, _, trace = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    starts = [i for i, e in enumerate(trace) if e["event"] == "checkpoint_started"]
    assert starts
    expand = next(i for i, e in enumerate(trace) if e["event"] == "expand_started")
    # Every exploration call after the last checkpoint was lexical search only.
    later_tools = {
        e["tool"] for e in trace[starts[-1] : expand] if e["event"] == "tool_call"
    }
    assert later_tools == {"search_code"}
    for index in starts:
        offered = trace[index]["candidates"]
        assert offered


def test_checkpoint_calls_have_a_hard_cap(cli_repo, tmp_path: Path) -> None:
    from ripple.agent import MAX_CHECKPOINTS

    controller = AgentController(
        cli_repo, ObservedPolicyLLM(), output_root=tmp_path / "x", variant=RIPPLE_V2
    )
    controller.checkpoints = MAX_CHECKPOINTS
    request = FeatureRequest(text=REQUEST)
    assert controller._checkpoint(request, stall=99, force=True) is False
    assert controller.llm_calls == 0


def test_checkpoints_offer_only_source_candidates(cli_repo, tmp_path: Path) -> None:
    plan = (
        ("inspect_symbol", {"target": "tests/test_verification.py"}),
        ("find_tests", {"target": CLI}),
        ("inspect_symbol", {"target": CLI}),
        ("find_references", {"symbol_id": f"{CLI}::main"}),
    )
    llm = ObservedPolicyLLM(plan=plan, relevant=frozenset({CLI, "tests"}))
    controller, _, trace = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    tests = {item.path.as_posix() for item in cli_repo.files if item.is_test}
    assert any(
        item.target.partition("::")[0] in tests
        for item in controller.ledger.candidates.values()
    ), "fixture should seed at least one test candidate"
    offered = [
        target
        for event in trace
        if event["event"] == "checkpoint_started"
        for target in event["candidates"]
    ]
    assert offered and not any(t.partition("::")[0] in tests for t in offered)


# ----------------------------------------------------------------- V2.1 framing


class RecordingPolicy(ObservedPolicyLLM):
    """ObservedPolicyLLM that also keeps the raw checkpoint prompts."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.raw_checkpoints: list[str] = []

    def decide_candidates(self, prompt: str) -> LLMResponse:
        self.raw_checkpoints.append(prompt)
        return super().decide_candidates(prompt)


WIRING_PLAN = (
    ("inspect_symbol", {"target": CLI}),
    ("find_references", {"symbol_id": f"{CLI}::main"}),
    ("find_tests", {"target": CLI}),
)


def test_new_cli_feature_target_is_confirmable_without_the_feature_existing(
    cli_repo, tmp_path: Path
) -> None:
    # The requested export command does not exist anywhere in the repository.
    assert not any("export" in item.id.casefold() for item in cli_repo.symbols)
    llm = RecordingPolicy(plan=WIRING_PLAN, relevant=frozenset({CLI}))
    controller, run, _ = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    assert CLI in {
        target.partition("::")[0] for target in _states(controller)["confirmed"]
    }
    assert run.report.status == "completed"
    prompt = llm.raw_checkpoints[0]
    assert "may not exist yet" in prompt
    assert "never ask for proof that the requested feature already exists" in prompt
    assert "Confirm means plausible affected target, not a proven future diff" in prompt
    assert "must change to implement" not in prompt  # the V2-r2 framing defect


def test_new_render_feature_module_is_confirmable(cli_repo, tmp_path: Path) -> None:
    plan = (
        ("inspect_symbol", {"target": f"{RENDER}::render_verification_markdown"}),
        ("find_references", {"symbol_id": f"{RENDER}::render_verification_markdown"}),
        ("find_tests", {"target": f"{RENDER}::render_verification_markdown"}),
    )
    llm = RecordingPolicy(plan=plan, relevant=frozenset({RENDER}))
    controller, run, _ = _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    assert f"{RENDER}::render_verification_markdown" in _states(controller)["confirmed"]
    assert run.report.status == "completed"


def test_checkpoint_context_shows_architecture_type_intent_and_prior_gaps(
    cli_repo, tmp_path: Path
) -> None:
    plan = (
        *WIRING_PLAN,
        # New strong evidence on the kept candidate re-opens it.
        ("inspect_symbol", {"target": f"{CLI}::latest"}),
        ("find_references", {"symbol_id": f"{CLI}::latest"}),
        ("find_tests", {"target": f"{CLI}::latest"}),
    )
    llm = RecordingPolicy(plan=plan, relevant=frozenset(), distractors=frozenset())
    _run(cli_repo, llm, tmp_path, RIPPLE_V2)
    first = _data(llm.raw_checkpoints[0])
    assert first["interpreted_intent"]["summary"] == INTENT["summary"]
    candidate = next(c for c in first["candidates"] if c["target"].startswith(CLI))
    assert candidate["candidate_type"] in {"file", "function", "class", "method"}
    shows = " ".join(item["shows"] for item in candidate["evidence"])
    # The file outline is visible (V2-r2 only said "file ... exists").
    assert "defines" in shows and "_run_verify" in shows and "main" in shows
    later = [_data(prompt) for prompt in llm.raw_checkpoints[1:]]
    noted = [
        c["previously_missing"]
        for data in later
        for c in data["candidates"]
        if c["previously_missing"]
    ]
    assert noted, "keep notes must be shown back at later checkpoints"


def test_empty_reference_results_are_summarised_explicitly(cli_repo) -> None:
    from ripple.agent import _evidence_summary, _strong_evidence, _touched_targets
    from ripple.tools import ToolSession

    session = ToolSession(cli_repo)
    arguments = {"symbol_id": "src/app/cli.py::latest", "limit": 10}
    result = session.invoke("find_references", arguments)
    record = EvidenceRecord(
        result.evidence_id,
        "find_references",
        arguments,
        result,
        _touched_targets("find_references", arguments, result),
        _strong_evidence("find_references", result),
    )
    assert _evidence_summary(record) == "no references found"


def test_absence_of_a_feature_is_not_citable_negative_evidence(cli_repo) -> None:
    from ripple.tools import ToolSession

    session = ToolSession(cli_repo)
    missing = session.invoke("search_code", {"query": "zzqqxxwv"})
    assert not missing.ok  # "feature not found"
    inspect = session.invoke("inspect_symbol", {"target": CLI})
    records = [
        EvidenceRecord(
            missing.evidence_id, "search_code", {}, missing, frozenset(), False
        ),
        EvidenceRecord(
            inspect.evidence_id, "inspect_symbol", {}, inspect, frozenset({CLI}), True
        ),
    ]
    ledger = _ledger_with(cli_repo, records)
    ledger.seed(CLI, "seed", inspect.evidence_id)
    support = {record.evidence_id for record in ledger.support(CLI)}
    assert missing.evidence_id not in support  # never presented for the target
    refused = ledger.decide(
        CLI, "reject", (missing.evidence_id,), "feature not found", frozenset(support)
    )
    assert refused == (False, "cited evidence was not presented for this target")
    assert ledger.candidates[CLI].status == "suspected"


def test_existing_behaviour_change_still_completes(cli_repo, tmp_path: Path) -> None:
    llm = RecordingPolicy(plan=WIRING_PLAN, relevant=frozenset({CLI}))
    controller = AgentController(
        cli_repo, llm, output_root=tmp_path / "modify", variant=RIPPLE_V2
    )
    run = controller.run(
        FeatureRequest(text="Change the verify command to require a --strict flag.")
    )
    assert run.report.status == "completed"
    assert any(item.target.startswith(CLI) for item in run.report.affected_components)


def test_checkpoint_framing_is_generic() -> None:
    from ripple.agent import CHECKPOINT_INSTRUCTIONS

    text = CHECKPOINT_INSTRUCTIONS.casefold()
    for specific in (r"\bcli\b", r"\.py\b", r"\bexport", r"markdown", r"verification"):
        assert not re.search(specific, text), specific
