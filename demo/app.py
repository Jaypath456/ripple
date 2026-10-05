"""RIPPLE interactive demo.  Run from the repository root:  streamlit run demo/app.py"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo import logic
from ripple.agent import AgentController
from ripple.agent_models import FeatureRequest
from ripple.llm import LLMError, OpenAILLM
from ripple.scanner import ScanError, scan_repository
from ripple.verification import VerificationError, verify_repository

st.set_page_config(page_title="RIPPLE Demo", page_icon="🌊", layout="wide")

TONE = {"good": "🟢", "info": "🔵", "warn": "🟠", "bad": "🔴"}
STATUS_ICON = {"confirmed": "✅", "suspected": "❔", "rejected": "❌"}
PHASE_LABEL = {
    "seed": "Seed search (Python)",
    "explore": "Agent step (model chooses, Python executes)",
    "expand": "Deterministic Expand (Python only)",
}
EXAMPLE_QUESTIONS = (
    "Why was users/service.py::delete_user selected?",
    "Why was billing/service.py::delete_invoice rejected?",
    "Why is admin/users.py classified as stale_caller?",
    "What evidence supports this prediction?",
)


@st.cache_data
def _benchmark() -> dict | None:
    try:
        return logic.load_benchmark()
    except logic.DemoError:
        return None


@st.cache_data
def _scenario(name: str) -> dict:
    return logic.load_scenario(name)


def _model_banner() -> None:
    live = logic.llm_config_status()
    left, right = st.columns(2)
    left.info(
        f"**Live demo model:** `{live['model'] or 'not configured'}` "
        f"({'key present' if live['key_present'] else 'no key'}, endpoint "
        f"`{live['endpoint']}`) — configured from environment variables."
    )
    right.warning(
        f"**Final benchmark model:** `{logic.benchmark_model()}` (frozen final-v1). "
        "Live demo behavior is not the benchmark result. Published metrics come "
        "only from frozen final-v1."
    )


# --------------------------------------------------------------------------- pages


def page_overview() -> None:
    st.title("🌊 RIPPLE")
    st.subheader(
        "Predict a change's blast radius before you code — then check it after."
    )
    st.markdown(
        """
**The problem.** Before implementing a feature in an unfamiliar Python codebase you
want to know *which files will change, which tests matter, and which callers might
break*. After implementing it you want to know *whether the diff matches the plan*.

**RIPPLE answers both, in two stages:**

| | Stage A — before coding | Stage B — after coding |
|---|---|---|
| Input | A feature request + a Git repository | The saved Stage A report + a Git range |
| Who decides | A bounded LLM agent **proposes**; Python **validates** | Deterministic Git + AST rules only |
| Output | Likely affected files/symbols, tests, regression areas, order | expected / adjacent / unexpected / missing_test / stale_caller |

**The key idea:** the language model never gets the last word. It can only propose
tool calls and claims; Python runs the tools, owns all evidence, enforces budgets, and
drops any claim that is not backed by evidence it actually produced.
"""
    )
    st.markdown("### Architecture")
    st.graphviz_chart(
        """
digraph {
  rankdir=TB; node [shape=box, style="rounded,filled", fillcolor="#eef3fb",
  fontname="Helvetica"]; edge [color="#5a6b85"];
  input [label="Feature request + Python Git repository", fillcolor="#fff7e6"];
  scan [label="Repository scanner\\n(reads tracked .py files, Python ast)"];
  index [label="Repository index\\nsymbols · imports · references · tests"];
  bm25 [label="BM25 search"]; graph [label="Dependency graph"];
  hist [label="Git co-change history"];
  agent [label="Bounded LLM agent\\nproposes 1 of 7 tools per step", fillcolor="#e8f6ee"];
  ledger [label="Candidate ledger (Python)\\nsuspected → confirmed / rejected"];
  validator [label="Deterministic validator\\ndrops unsupported claims"];
  report [label="Stage A report", fillcolor="#fff7e6"];
  dev [label="Developer changes code", shape=note, fillcolor="#ffffff"];
  diff [label="Git diff"]; stageb [label="Stage B verification (deterministic)"];
  out [label="expected · adjacent · unexpected\\nmissing_test · stale_caller",
       fillcolor="#fff7e6"];
  input -> scan -> index; index -> bm25; index -> graph; index -> hist;
  bm25 -> agent; graph -> agent; hist -> agent; agent -> ledger -> validator -> report;
  report -> dev -> diff -> stageb -> out; report -> stageb [style=dashed];
}
"""
    )
    st.markdown("### Where to go next")
    st.markdown(
        "- **Guided Replay** – click through three real RIPPLE runs (no API key).\n"
        "- **Tools** and **Trust boundary** – what the agent may do, and what stops it.\n"
        "- **Stage B explained** – the five post-change categories.\n"
        "- **Benchmark results** – the honest held-out result, including where "
        "RIPPLE lost.\n"
        "- **Live analysis** – run RIPPLE on your own local repository."
    )
    benchmark = _benchmark()
    if benchmark:
        b3 = benchmark["aggregates"]["B3"]
        ripple = benchmark["aggregates"]["RIPPLE"]
        st.error(
            f"**Honest headline (final-v1):** the one-shot LLM baseline B3 "
            f"(F1 {logic.fmt_metric(b3['f1'])}) outperformed RIPPLE "
            f"(F1 {logic.fmt_metric(ripple['f1'])}) on the held-out pre-change "
            f"benchmark; RIPPLE abstained on {benchmark['ripple_abstained']}/"
            f"{benchmark['ripple_runs']} runs. See **Benchmark results**."
        )


def _render_trace(scenario: dict) -> None:
    steps = logic.trace_steps(scenario)
    phases: dict[str, list] = {}
    for step in steps:
        phases.setdefault(step["phase"], []).append(step)
    number = 0
    for phase in ("seed", "explore", "expand"):
        if phase not in phases:
            continue
        st.markdown(f"**{PHASE_LABEL[phase]}**")
        for step in phases[phase]:
            number += 1
            with st.container(border=True):
                head = f"**Step {number} · `{step['tool']}`**"
                if step["evidence_id"]:
                    head += f" → evidence `{step['evidence_id']}`"
                if step["duplicate"]:
                    head += " _(duplicate: cached result reused)_"
                st.markdown(head)
                if step["reason"]:
                    st.markdown(f"🧠 *Model's decision:* “{step['reason']}”")
                if step["arguments"]:
                    st.code(json.dumps(step["arguments"]), language="json")
                st.markdown(f"🔎 *Result:* {step['summary']}")
                for note in step["ledger"]:
                    st.markdown(f"📒 *Ledger:* {note}")
                if step["result"]:
                    with st.expander("Raw tool result"):
                        st.json(step["result"], expanded=False)
    with st.expander("Show raw trace (JSONL events)"):
        st.json(scenario["trace"], expanded=False)


def _render_ledger(scenario: dict) -> None:
    st.caption(
        "The LLM may propose candidates, but only deterministic evidence can "
        "validate them. Python accepts a status change only when the target exists "
        "and the cited latest evidence actually touched it."
    )
    rows = logic.ledger_rows(scenario)
    columns = st.columns(3)
    for column, status in zip(columns, ("confirmed", "suspected", "rejected")):
        chosen = [row for row in rows if row["status"] == status]
        column.markdown(f"#### {STATUS_ICON[status]} {status.upper()} ({len(chosen)})")
        for row in chosen:
            with column.container(border=True):
                st.markdown(f"`{row['target']}`")
                st.caption(row["reason"])
                st.markdown(
                    "Evidence: " + ", ".join(f"`{item}`" for item in row["evidence"])
                )
                if status == "confirmed":
                    st.markdown(
                        f"References checked: {'✅' if row['references_checked'] else '❌'}"
                        f" · Tests checked: {'✅' if row['tests_checked'] else '❌'}"
                    )


def _render_validation(view: dict) -> None:
    st.markdown(
        "After the agent submits, Python runs **Expand** (tests, regression areas, "
        "migration proposals, implementation order) and then the **validator**, which "
        "removes anything the evidence does not support."
    )
    if view["dropped_claims"]:
        st.markdown("**Dropped or corrected by the validator:**")
        for claim in view["dropped_claims"]:
            st.markdown(f"- ✂️ `{claim}`")
    else:
        st.success("Nothing had to be dropped in this run.")


def _render_report(view: dict) -> None:
    st.markdown(f"**Status:** `{view['status']}`")
    st.markdown("**Affected components (predicted change surface)**")
    st.dataframe(view["components"], hide_index=True, width="stretch")
    left, right = st.columns(2)
    with left:
        st.markdown("**Suggested tests**")
        st.dataframe(view["tests"] or [{"test": "none"}], hide_index=True)
        st.markdown("**Implementation order**")
        for position, item in enumerate(view["implementation_order"], start=1):
            st.markdown(f"{position}. `{item}`")
    with right:
        st.markdown("**Regression areas (callers to re-check, not predicted edits)**")
        st.dataframe(view["regression_areas"] or [{"target": "none"}], hide_index=True)
        if view["schema_changes"]:
            st.markdown("**Schema changes**")
            for item in view["schema_changes"]:
                st.markdown(f"- {item}")
        if view["risks"]:
            st.markdown("**Risks**")
            for item in view["risks"]:
                st.markdown(f"- {item}")
    stats = view["stats"]
    cols = st.columns(5)
    cols[0].metric("Tool calls", stats["tool_calls"])
    cols[1].metric("Model calls", stats["llm_calls"])
    cols[2].metric("Tokens", logic.fmt_count(stats["total_tokens"]))
    cols[3].metric("Stop reason", stats["stop_reason"])
    cols[4].metric("Runtime", f"{stats['runtime_seconds'] or 0:.2f}s")


def _render_stage_b(analysis: dict) -> None:
    rows = logic.stage_b_rows(analysis)
    if not rows:
        st.info("Stage B produced no findings.")
    for row in rows:
        with st.container(border=True):
            st.markdown(
                f"{TONE[row['tone']]} **{row['label'].upper()}** · `{row['path']}` "
                f"· verdict `{row['verdict']}`"
            )
            st.caption(logic.CATEGORY_INFO[row["category"]]["meaning"])
            st.markdown(row["explanation"])
            if row["evidence"]:
                st.markdown("Evidence: " + ", ".join(f"`{e}`" for e in row["evidence"]))
    left, right = st.columns(2)
    left.metric(
        "File precision vs prediction", logic.fmt_metric(analysis.get("file_precision"))
    )
    right.metric(
        "File recall vs prediction", logic.fmt_metric(analysis.get("file_recall"))
    )


def _explainer(scenario: dict, key: str) -> None:
    with st.expander(
        "💬 Ask RIPPLE about this analysis (optional, uses the live model)"
    ):
        st.caption(
            "Answers come only from this run's report, trace, ledger, evidence, "
            "Stage B result, and saved benchmark metadata. Answers whose citations "
            f"are not found in that context are replaced with: “{logic.REFUSAL}”"
        )
        status = logic.llm_config_status()
        if not status["configured"]:
            st.info(
                "Not configured: set RIPPLE_LLM_API_KEY and RIPPLE_LLM_MODEL to enable "
                "the explainer. Everything else on this page works without it."
            )
            return
        choice = st.selectbox(
            "Example questions", ("", *EXAMPLE_QUESTIONS), key=f"q{key}"
        )
        question = st.text_input("Your question", value=choice, key=f"t{key}")
        if st.button("Ask", key=f"b{key}"):
            context = logic.build_explainer_context(scenario, _benchmark())
            try:
                with st.spinner("Asking the model, grounded in the saved evidence…"):
                    answer = logic.ask_explainer(
                        question, context, OpenAILLM.from_env()
                    )
            except (LLMError, logic.DemoError) as error:
                st.error(logic.friendly_error(error))
                return
            st.markdown(logic.redact(answer.answer))
            if answer.citations:
                st.caption(
                    "Citations: " + ", ".join(f"`{c}`" for c in answer.citations)
                )


REPLAY_STEPS = (
    "Feature request",
    "Repository scan",
    "Agent investigation",
    "Candidate ledger",
    "Deterministic validation",
    "Stage A prediction",
    "Actual implementation diff",
    "Stage B classification",
    "Final verification report",
)


def page_replay() -> None:
    st.title("▶️ Guided Replay")
    st.caption(
        "Saved RIPPLE runs on a bundled sample repository. No API calls are made."
    )
    names = logic.list_scenarios()
    if not names:
        st.error("No replay fixtures found. Run `python demo/build_fixtures.py`.")
        return
    try:
        scenarios = {name: _scenario(name) for name in names}
    except logic.DemoError as error:
        st.error(str(error))
        return
    name = st.radio(
        "Scenario",
        names,
        format_func=lambda item: scenarios[item]["title"],
        horizontal=True,
    )
    scenario = scenarios[name]
    st.info(f"**What this scenario shows:** {scenario.get('teaches', '')}")
    with st.expander("How this replay was produced"):
        st.markdown(
            f"{scenario['generation']['note']} The scripted model id is "
            f"`{scenario['generation']['model']}`; regenerate with "
            "`python demo/build_fixtures.py`."
        )
    if st.session_state.get("replay_name") != name:
        st.session_state.replay_name = name
        st.session_state.replay_step = 0
    show_all = st.toggle("Show all steps at once")
    step = st.session_state.replay_step
    if not show_all:
        st.progress(
            (step + 1) / len(REPLAY_STEPS),
            text=f"Step {step + 1} of {len(REPLAY_STEPS)}: {REPLAY_STEPS[step]}",
        )
        back, forward, _ = st.columns([1, 1, 6])
        if back.button("◀ Back", disabled=step == 0):
            st.session_state.replay_step -= 1
            st.rerun()
        if forward.button("Next ▶", disabled=step == len(REPLAY_STEPS) - 1):
            st.session_state.replay_step += 1
            st.rerun()
    indices = range(len(REPLAY_STEPS)) if show_all else (step,)
    view = logic.report_view(scenario["report"])
    for index in indices:
        st.markdown(f"## {index + 1}. {REPLAY_STEPS[index]}")
        _replay_section(index, scenario, view)
    _explainer(scenario, name)


def _replay_section(index: int, scenario: dict, view: dict) -> None:
    repository = scenario["repository"]
    if index == 0:
        st.markdown(f"> “{scenario['request']}”")
        st.caption(
            "Stage A sees only this request and the base commit "
            f"`{scenario['base_commit'][:10]}`; it never sees the implementation."
        )
    elif index == 1:
        st.markdown(
            f"RIPPLE indexed **{len(repository['files'])} tracked Python files** "
            f"({len(repository['tests'])} tests), **{repository['symbols']} symbols**, "
            f"**{repository['imports']} imports**, and **{repository['references']} "
            "references** using Python's `ast` module — without importing or running "
            "anything."
        )
        st.code("\n".join(repository["files"]), language="text")
    elif index == 2:
        st.markdown(
            "Python first seeds suspects with one search. The model then picks one "
            "tool per step; Python validates the arguments, runs the tool, and assigns "
            "an evidence ID. After submission, Python alone runs Expand."
        )
        _render_trace(scenario)
    elif index == 3:
        _render_ledger(scenario)
    elif index == 4:
        _render_validation(view)
    elif index == 5:
        _render_report(view)
        with st.expander("Rendered Markdown report"):
            st.markdown(scenario.get("report_markdown", ""))
    elif index == 6:
        st.markdown(
            f"**What the developer actually did:** {scenario['implementation']}"
        )
        st.code(scenario["diff"], language="diff")
    elif index == 7:
        st.caption(
            "Stage B compares the saved Stage A report with the real Git diff using "
            "deterministic rules; no model participated in this replay."
        )
        _render_stage_b(scenario["verification"])
    elif index == 8:
        st.markdown(scenario.get("verification_markdown", ""))
        with st.expander("Raw verification JSON"):
            st.json(scenario["verification"], expanded=False)


def page_tools() -> None:
    st.title("🧰 The seven Stage A tools")
    st.caption(
        "These are the only actions the agent can request. Each call is validated by "
        "Pydantic, executed by Python, capped, and given an evidence ID."
    )
    tools = logic.tool_guide()
    for row_start in range(0, len(tools), 3):
        columns = st.columns(3)
        for column, tool in zip(columns, tools[row_start : row_start + 3]):
            with column.container(border=True):
                st.markdown(f"#### `{tool['name']}`")
                st.markdown(tool["purpose"])
                st.caption(f"Example question: “{tool['example']}”")
    st.markdown("### Stage B only (post-change investigation)")
    st.caption(
        "Used only when a model is configured for optional Stage B investigation of "
        "unexpected files. They are not part of the seven Stage A tools, and their "
        "output cannot change a deterministic category."
    )
    for tool in logic.stage_b_tools():
        st.markdown(f"- **`{tool['name']}`** — {tool['purpose']}")


def page_safety() -> None:
    st.title("🛡️ Trust boundary")
    st.markdown(
        "RIPPLE treats the language model as an untrusted planner. These safeguards "
        "exist in the code today (location shown for each):"
    )
    for item in logic.SAFETY_MECHANISMS:
        with st.container(border=True):
            st.markdown(f"**{item['title']}**")
            st.markdown(item["plain"])
            st.caption(f"Implemented in `{item['where']}`")
    st.markdown(
        "### Why this matters even though the benchmark model often abstained\n"
        "With `openai/gpt-oss-20b` the evidence requirements made RIPPLE refuse far "
        "more often than it answered. That is a real cost (see **Benchmark results**). "
        "The upside is the failure mode: when RIPPLE did not have validated evidence it "
        "said nothing, instead of confidently listing files it could not support. The "
        "boundary is model-independent, so a stronger model can be dropped in without "
        "loosening it."
    )


def page_stage_b() -> None:
    st.title("🔍 Stage B explained")
    st.markdown(
        "After implementation, Stage B compares the saved prediction with the real Git "
        "diff. Categories come from Git and AST rules, never from a model."
    )
    for name, info in logic.CATEGORY_INFO.items():
        st.markdown(
            f"{TONE[info['tone']]} **{info['label']}** (`{name}`) — {info['meaning']}"
        )
    benchmark = _benchmark()
    if not benchmark:
        st.error("final_summary.json could not be loaded.")
        return
    summary = benchmark["summary"]
    oracle = summary["stage_b"]["metrics"]
    st.markdown(
        f"### Planted-anomaly experiment ({summary['stage_b']['task_count']} tasks)"
    )
    st.warning(
        "These results use **oracle** Stage-A predictions (every gold source file). "
        "They do NOT mean the complete RIPPLE pipeline had 100% accuracy."
    )
    cols = st.columns(4)
    cols[0].metric(
        "Unrelated file → unexpected",
        logic.fmt_metric(oracle["unrelated_detection_recall"]),
    )
    cols[1].metric(
        "Dropped tests → missing_test",
        logic.fmt_metric(oracle["drop_tests_detection_recall"]),
    )
    cols[2].metric(
        "Stale caller → stale_caller",
        logic.fmt_metric(oracle["stale_caller_detection_recall"]),
    )
    cols[3].metric(
        "Control false-alarm rate", logic.fmt_metric(oracle["control_false_alarm_rate"])
    )
    ripple = summary["stage_b_ripple"]["metrics"]
    if ripple:
        with st.expander("With RIPPLE's actual seed-17 Stage A reports"):
            st.markdown(
                f"Detection recall {logic.fmt_metric(ripple['unrelated_detection_recall'])}"
                f" / {logic.fmt_metric(ripple['drop_tests_detection_recall'])} / "
                f"{logic.fmt_metric(ripple['stale_caller_detection_recall'])}, control "
                f"false-alarm rate {logic.fmt_metric(ripple['control_false_alarm_rate'])}."
            )
            st.caption(
                "Uninformative: those reports mostly abstained, and an empty prediction "
                "makes every changed file look unexpected."
            )


def page_benchmark() -> None:
    st.title("📊 Benchmark results (final-v1)")
    _model_banner()
    benchmark = _benchmark()
    if not benchmark:
        st.error("evaluation/results/final_summary.json is missing or malformed.")
        return
    summary = benchmark["summary"]
    cols = st.columns(4)
    cols[0].metric("Held-out tasks", summary["task_count"])
    cols[1].metric("Repositories", summary["repository_count"])
    cols[2].metric("Total runs", f"{summary['run_count']:,}")
    cols[3].metric(
        "RIPPLE abstained",
        f"{benchmark['ripple_abstained']} / {benchmark['ripple_runs']}",
    )
    st.error(
        "**The one-shot LLM baseline B3 outperformed RIPPLE on the final pre-change "
        "benchmark.**"
    )
    st.dataframe(
        logic.benchmark_rows(benchmark, logic.HEADLINE_SYSTEMS),
        hide_index=True,
        width="stretch",
    )
    st.markdown(
        "The bounded evidence requirements made the selected gpt-oss-20b configuration "
        "extremely conservative, so the full agent usually abstained rather than "
        "making unsupported predictions. This is an interpretation consistent with the "
        "saved traces (most runs stopped on the no-progress or tool-budget limit), not "
        "a proven sole cause."
    )
    st.markdown(
        "| System | What it is |\n|---|---|\n"
        "| B0 | BM25 lexical search |\n| B1 | BM25 + one-hop dependency expansion |\n"
        "| B2 | BM25 + co-change expansion |\n| B3 | One-shot LLM call |\n"
        "| B4 | Plain ReAct agent with the same tools |\n| RIPPLE | Full bounded controller |"
    )
    st.caption(
        "B0–B2 set metrics use RIPPLE's own predicted-file count for the same task "
        "and seed, so they are also near zero when RIPPLE abstains; their ranked "
        "metrics (Recall@5/10, MRR) are independent."
    )
    st.markdown("### Ablations")
    st.caption(
        "A1 removes co_changed · A2 removes get_dependencies and find_references · "
        "A3 removes the report validator."
    )
    st.dataframe(
        logic.benchmark_rows(benchmark, logic.ABLATIONS),
        hide_index=True,
        width="stretch",
    )
    st.markdown("### Run status by system")
    st.dataframe(
        [
            {"System": system} | counts
            for system, counts in summary["status_mix_by_system"].items()
        ],
        hide_index=True,
    )
    totals = summary["totals"]
    st.markdown("### Final evaluation totals")
    cols = st.columns(4)
    cols[0].metric("Model calls", logic.fmt_count(totals["model_calls"]))
    cols[1].metric("Tokens", logic.fmt_count(totals["total_tokens"]))
    cols[2].metric("Tool calls", logic.fmt_count(totals["tool_calls"]))
    cols[3].metric("Recorded runtime", f"{totals['runtime_seconds'] / 3600:.2f} h")
    st.caption(
        f"Source: evaluation/results/final_summary.json · config "
        f"`{summary['config_version']}` · hash `{summary['config_hash'][:16]}…`. "
        "Full tables, confidence intervals, and paired comparisons are in "
        "evaluation/results/README.md."
    )


def page_metrics() -> None:
    st.title("📏 Metrics explained")
    st.markdown(
        "Example: the real change touched **3 files** (A, B, C). A system predicts "
        "**4 files** (A, B, X, Y)."
    )
    st.code(
        "Real change:  A  B  C\nPredicted:    A  B  X  Y   → 2 right, 2 wrong, 1 missed"
    )
    rows = (
        (
            "Precision",
            "When RIPPLE predicts a file, how often was that prediction correct?",
            "2 of 4 predictions correct → 0.50",
        ),
        (
            "Recall",
            "Of all files that really changed, how many did RIPPLE find?",
            "found 2 of 3 → 0.67",
        ),
        (
            "F1",
            "One number balancing precision and recall.",
            "harmonic mean of 0.50 and 0.67 → 0.57",
        ),
        (
            "Recall@5",
            "If we inspect the top five suggestions, how much of the real change is covered?",
            "all of A, B in top 5 → 0.67",
        ),
        ("Recall@10", "Same idea using the top ten.", "same list → 0.67"),
        (
            "MRR",
            "How high in the ranked list does the first correct result appear?",
            "first hit at rank 1 → 1.0; at rank 2 → 0.5",
        ),
        (
            "FP/task",
            "How many wrong file predictions are produced per task on average?",
            "X and Y → 2",
        ),
        (
            "Abstention",
            "The agent decides it does not have enough validated evidence to make a prediction.",
            "predicts nothing → precision, recall, F1 all 0, still counted",
        ),
    )
    for name, meaning, example in rows:
        with st.container(border=True):
            st.markdown(f"**{name}** — {meaning}")
            st.caption(f"In the example: {example}")


def page_prompts() -> None:
    st.title("🗣️ How the AI is instructed")
    st.caption(
        "Credentials are never part of a prompt. The frozen prompts are shown, not changed."
    )
    st.markdown("**System instruction sent with every model call:**")
    st.code(logic.provider_instructions(), language="text")
    names = logic.list_scenarios()
    prompts = logic.captured_prompts(_scenario(names[0])) if names else {}
    for role in logic.PROMPT_ROLES:
        with st.container(border=True):
            st.markdown(f"**{role['role']}**")
            st.markdown(f"Purpose: {role['purpose']}")
            if role["operation"] == "explain":
                with st.expander("View actual explainer instructions"):
                    st.code(logic.EXPLAINER_INSTRUCTIONS, language="text")
            elif role["operation"] in prompts:
                with st.expander(
                    "View actual sanitized prompt (captured from a replay run)"
                ):
                    st.code(prompts[role["operation"]], language="text")


def page_supports() -> None:
    st.title("✅ What RIPPLE supports")
    capabilities = logic.supported_capabilities()
    left, right = st.columns(2)
    left.markdown("### Supported")
    for item in capabilities["supported"]:
        left.markdown(f"- ✅ {item}")
    right.markdown("### Not supported / not claimed")
    for item in capabilities["not_supported"]:
        right.markdown(f"- ❌ {item}")


def page_live() -> None:
    st.title("🧪 Live analysis (local only)")
    _model_banner()
    st.caption(
        "Runs the real RIPPLE Stage A on a Git repository on *this* machine. A hosted "
        "demo cannot read a visitor's files, so use Guided Replay there. RIPPLE never "
        "edits the repository or runs its code; outputs go to "
        f"`{logic.LIVE_OUTPUT.relative_to(logic.ROOT)}/`."
    )
    repo_text = st.text_input(
        "Repository path", placeholder="/path/to/python/repository"
    )
    request_text = st.text_area(
        "Feature request", placeholder="Add soft-delete support to users."
    )
    allow_dirty = st.checkbox(
        "Allow uncommitted changes (the report will be marked dirty)"
    )
    if st.button("Analyze impact", type="primary"):
        _run_live(repo_text, request_text, allow_dirty)
    live = st.session_state.get("live")
    if live:
        _show_live(live)


def _run_live(repo_text: str, request_text: str, allow_dirty: bool) -> None:
    try:
        repo = logic.validate_live_repo(repo_text)
        request = FeatureRequest(text=request_text)
        llm = OpenAILLM.from_env()
        with st.spinner("Scanning repository…"):
            index = scan_repository(repo)
    except logic.DemoError as error:
        st.error(str(error))
        return
    except ValueError as error:  # FeatureRequest validation
        st.error(f"Feature request rejected: {logic.redact(str(error))[:300]}")
        return
    except (LLMError, ScanError) as error:
        st.error(logic.friendly_error(error))
        return
    if index.dirty and not allow_dirty:
        st.error(
            "The repository has uncommitted changes. Commit/stash them or tick "
            "“Allow uncommitted changes”."
        )
        return
    output = logic.LIVE_OUTPUT / repo.name
    controller = AgentController(index, llm, output_root=output)
    placeholder = st.empty()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(controller.run, request)
        while not future.done():
            events = []
            if controller.trace.path.exists():
                events = controller.trace.path.read_text(encoding="utf-8").splitlines()
            last = json.loads(events[-1])["event"] if events else "starting"
            placeholder.info(
                f"⏳ {time.perf_counter() - started:.0f}s · tool calls "
                f"{controller.tool_calls} · model calls {controller.llm_calls} · "
                f"tokens {controller.total_tokens if controller.usage_known else 'n/a'} · "
                f"last event `{last}`"
            )
            time.sleep(0.5)
        run = future.result()
    placeholder.empty()
    st.session_state.live = {
        "repo": str(repo),
        "payload": logic.capture_run(controller, run),
        "report_path": str(run.report_path),
    }


def _show_live(live: dict) -> None:
    payload = live["payload"]
    report = payload["report"]
    failure = logic.run_failure(payload) if report["status"] == "failed" else None
    if failure:
        st.error(failure)
    elif report["status"] == "abstained":
        st.warning(
            "RIPPLE abstained: it did not gather enough validated evidence to make a "
            "prediction. This is the intended conservative behavior, not a crash."
        )
    else:
        st.success(f"Stage A finished with status `{report['status']}`.")
    view = logic.report_view(report)
    tabs = st.tabs(
        ["Report", "Agent trace", "Candidate ledger", "Validation", "Verify diff"]
    )
    with tabs[0]:
        _render_report(view)
    with tabs[1]:
        _render_trace(payload)
    with tabs[2]:
        _render_ledger(payload)
    with tabs[3]:
        _render_validation(view)
    with tabs[4]:
        st.caption(
            "Stage B compares the saved prediction with committed changes in a Git "
            "range. The report's base commit is the expected starting point."
        )
        git_range = st.text_input("Git range", value=f"{report['commit']}..HEAD")
        if st.button("Verify current diff"):
            try:
                verification = verify_repository(
                    Path(live["repo"]),
                    report_value=live["report_path"],
                    requested_range=git_range,
                    output_root=logic.LIVE_OUTPUT
                    / Path(live["repo"]).name
                    / "verifications",
                )
            except (VerificationError, ValueError) as error:
                st.error(f"Stage B could not run: {logic.redact(str(error))[:300]}")
            else:
                if not verification.analysis.changes:
                    st.info(
                        "No committed changes in that range — nothing to verify yet."
                    )
                else:
                    if verification.analysis.base_warning:
                        st.warning(verification.analysis.base_warning)
                    payload["verification"] = verification.analysis.model_dump(
                        mode="json"
                    )
                    _render_stage_b(payload["verification"])
    _explainer(payload, "live")


pages = [
    st.Page(page_overview, title="Overview", icon="🌊", default=True),
    st.Page(page_replay, title="Guided Replay", icon="▶️", url_path="replay"),
    st.Page(page_tools, title="Tools", icon="🧰", url_path="tools"),
    st.Page(page_safety, title="Trust boundary", icon="🛡️", url_path="safety"),
    st.Page(page_stage_b, title="Stage B explained", icon="🔍", url_path="stage-b"),
    st.Page(page_benchmark, title="Benchmark results", icon="📊", url_path="results"),
    st.Page(page_metrics, title="Metrics explained", icon="📏", url_path="metrics"),
    st.Page(
        page_prompts, title="How the AI is instructed", icon="🗣️", url_path="prompts"
    ),
    st.Page(
        page_supports, title="What RIPPLE supports", icon="✅", url_path="supports"
    ),
    st.Page(page_live, title="Live analysis", icon="🧪", url_path="live"),
]
st.navigation(pages).run()
