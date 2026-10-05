"""RIPPLE showcase.  Run from the repository root:  streamlit run demo/app.py"""

from __future__ import annotations

import html
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo import logic
from ripple.agent import RIPPLE_V2, AgentController
from ripple.agent_models import FeatureRequest
from ripple.llm import LLMError, OpenAILLM
from ripple.scanner import ScanError, scan_repository
from ripple.verification import VerificationError, verify_repository

st.set_page_config(page_title="RIPPLE", page_icon="🌊", layout="wide")

CSS = """
<style>
:root { --r-line: rgba(128,128,128,.25); --r-soft: rgba(128,128,128,.07);
        --r-accent: #3b82f6; }
.block-container { max-width: 1120px; padding-top: 4.5rem; padding-bottom: 4rem; }
.r-hero { padding: 2.2rem 0 1.2rem; }
.r-hero h1 { font-size: 3.4rem; font-weight: 800; letter-spacing: -.03em;
             margin: 0 0 .4rem; padding: 0; }
.r-tagline { font-size: 1.75rem; font-weight: 650; line-height: 1.25;
             letter-spacing: -.01em; margin-bottom: .7rem; }
.r-sub { font-size: 1.08rem; opacity: .72; max-width: 44rem; line-height: 1.55; }
.r-eyebrow { font-size: .72rem; letter-spacing: .14em; text-transform: uppercase;
             font-weight: 700; opacity: .55; margin-bottom: .35rem; }
.r-h2 { font-size: 1.55rem; font-weight: 750; letter-spacing: -.01em;
        margin: 0 0 .6rem; }
.r-section { margin-top: 2.4rem; }
.r-muted { opacity: .68; }
.r-feature { min-height: 3.4rem; }
.r-small { font-size: .85rem; opacity: .65; }
.r-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          font-size: .93rem; word-break: break-all; }
.r-title { font-size: 1.05rem; font-weight: 700; margin: .1rem 0 .25rem; }
.r-badge { display: inline-block; font-size: .68rem; font-weight: 750;
           letter-spacing: .07em; padding: .16rem .55rem; border-radius: 999px;
           margin-bottom: .45rem; }
.r-ok   { background: rgba(34,197,94,.14);  color: #16a34a; }
.r-warn { background: rgba(234,179,8,.18);  color: #b7791f; }
.r-bad  { background: rgba(239,68,68,.14);  color: #dc2626; }
.r-info { background: rgba(59,130,246,.14); color: #2563eb; }
.r-neutral { background: var(--r-soft); }
.r-request { font-size: 1.4rem; font-weight: 650; line-height: 1.4;
             border-left: 4px solid var(--r-accent); background: rgba(59,130,246,.06);
             border-radius: 8px; padding: .9rem 1.2rem; margin: 1.2rem 0 .4rem; }
.r-statement { text-align: center; font-size: 2rem; font-weight: 800;
               letter-spacing: -.02em; padding: 1.6rem 1rem; margin: 1.6rem 0;
               border: 1px solid var(--r-line); border-radius: 16px;
               background: var(--r-soft); }
.r-statement span { color: var(--r-accent); }
.r-flow { display: flex; flex-direction: column; align-items: center; margin: 1rem 0; }
.r-node { border: 1px solid var(--r-line); border-radius: 12px; padding: .7rem 1.3rem;
          min-width: 18rem; max-width: 100%; text-align: center; font-weight: 650; }
.r-node small { display: block; font-weight: 400; opacity: .65; font-size: .82rem; }
.r-node.r-key { border-color: var(--r-accent); background: rgba(59,130,246,.07); }
.r-node.r-human { border-style: dashed; font-weight: 500; opacity: .8; }
.r-arrow { opacity: .4; font-size: 1.1rem; line-height: 1.6rem; }
.r-stat { font-size: 2.8rem; font-weight: 800; letter-spacing: -.03em;
          line-height: 1.1; }
.r-statlabel { opacity: .65; font-size: .95rem; margin-bottom: .35rem; }
.r-tl { border-left: 2px solid var(--r-line); margin-left: .45rem;
        padding: .1rem 0 .9rem 1.1rem; position: relative; }
.r-tl:before { content: ""; position: absolute; left: -.42rem; top: .38rem;
               width: .7rem; height: .7rem; border-radius: 50%;
               background: var(--r-accent); }
.r-tl.r-py:before { background: rgba(128,128,128,.6); }
.r-tl b { font-weight: 650; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

BADGE_TONE = {"CONFIRMED": "r-ok", "TEST IMPACT": "r-info", "PREDICTED": "r-neutral"}
CARD_TONE = {"good": "r-ok", "info": "r-warn", "warn": "r-bad", "bad": "r-bad"}
SCENARIO_LABEL = {
    "soft_delete": ("Add soft-delete", "A data-model change"),
    "required_argument": ("Change a function signature", "One caller gets missed"),
    "deletion_behavior": ("Change deletion behavior", "A test and scope slip"),
}
PHASE_ACTOR = {
    "seed": "Python",
    "explore": "model chose · Python ran",
    "expand": "Python only",
}


def esc(value: object) -> str:
    return html.escape(str(value))


def badge(text: str, tone: str) -> str:
    return f'<span class="r-badge {tone}">{esc(text)}</span>'


def heading(eyebrow: str, title: str, *, first: bool = False) -> None:
    st.markdown(
        f'<div class="{"" if first else "r-section"}">'
        f'<div class="r-eyebrow">{esc(eyebrow)}</div>'
        f'<div class="r-h2">{esc(title)}</div></div>',
        unsafe_allow_html=True,
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


# --------------------------------------------------------------------------- shared


def impact_grid(cards: list[dict], key: str) -> None:
    columns = st.columns(2)
    for position, card in enumerate(cards):
        with columns[position % 2].container(border=True):
            symbol = (
                f' <span class="r-small">· {esc(card["symbol"])}</span>'
                if card["symbol"]
                else ""
            )
            st.markdown(
                badge(card["badge"], BADGE_TONE.get(card["badge"], "r-info"))
                + f'<div class="r-title r-mono">{esc(card["path"])}{symbol}</div>'
                + f'<div class="r-muted">{esc(card["reason"])}</div>',
                unsafe_allow_html=True,
            )
            with st.popover("Why?", key=f"why-{key}-{position}"):
                st.markdown("**Deterministic evidence behind this card**")
                for item in card["evidence"]:
                    st.markdown(
                        f"`{item['evidence_id']}` · {esc(item['purpose'])}  \n"
                        f"<span class='r-small'>tool: {esc(item['tool'])}</span>",
                        unsafe_allow_html=True,
                    )
                if card["checks"]:
                    refs = "✓" if card["checks"]["references"] else "✗"
                    tests = "✓" if card["checks"]["tests"] else "✗"
                    st.markdown(f"References checked {refs} · Tests checked {tests}")
                if card["confidence"]:
                    st.caption(f"Confidence: {card['confidence']}")
                if not card["evidence"]:
                    st.caption("No evidence recorded for this item.")


def stage_b_section(analysis: dict, key: str) -> None:
    cards = logic.stage_b_cards(analysis)
    flagged = [card for card in cards if card["flagged"]]
    matched = [card for card in cards if not card["flagged"]]
    if flagged:
        plural = "s" if len(flagged) != 1 else ""
        st.markdown(
            f'<div class="r-tagline" style="font-size:1.35rem">RIPPLE flagged '
            f"{len(flagged)} thing{plural} to review.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("**No suspicious changes found.**")
    for group, title in (
        (flagged, "Needs review"),
        (matched, "As predicted or related"),
    ):
        if not group:
            continue
        st.markdown(f'<div class="r-eyebrow">{title}</div>', unsafe_allow_html=True)
        columns = st.columns(2)
        for position, card in enumerate(group):
            with columns[position % 2].container(border=True):
                st.markdown(
                    badge(
                        f"{card['icon']} {card['label'].upper()}",
                        CARD_TONE[card["tone"]],
                    )
                    + f'<div class="r-title r-mono">{esc(card["path"])}</div>'
                    + f'<div class="r-muted">{esc(card["one_liner"])}</div>',
                    unsafe_allow_html=True,
                )
                if card["caveat"]:
                    st.caption(f"⚠️ {card['caveat']}")
                with st.expander("Details"):
                    st.markdown(card["explanation"])
                    if card["evidence"]:
                        st.caption("Evidence: " + " · ".join(card["evidence"]))
                    st.caption(f"Verdict: {card['verdict']}")


def narrowing_section(scenario: dict) -> None:
    groups = logic.narrowing_columns(scenario)
    tones = {"Investigating": "r-neutral", "Confirmed": "r-ok", "Rejected": "r-bad"}
    for column, (name, rows) in zip(st.columns(3), groups.items()):
        column.markdown(
            badge(f"{name} · {len(rows)}", tones[name]), unsafe_allow_html=True
        )
        for row in rows:
            column.markdown(
                f'<div class="r-mono">{esc(row["target"])}</div>'
                f'<div class="r-small" style="margin-bottom:.6rem">{esc(row["reason"])}'
                "</div>",
                unsafe_allow_html=True,
            )
    st.caption(
        "The model can only propose a move between columns; Python accepts it only "
        "if the target exists and the cited evidence actually touched it. Internally, "
        "RIPPLE calls this the candidate ledger."
    )
    dropped = scenario["report"].get("dropped_claims", [])
    if dropped:
        st.markdown("**Removed by the validator before the report was written**")
        for claim in dropped:
            st.markdown(f"- `{claim}`")


def timeline_section(scenario: dict) -> None:
    for step in logic.investigation_timeline(scenario):
        evidence = (
            f" · evidence {esc(step['evidence_id'])}" if step["evidence_id"] else ""
        )
        cached = " · cached result" if step["duplicate"] else ""
        reason = (
            f'<div class="r-small">“{esc(step["reason"])}”</div>'
            if step["reason"]
            else ""
        )
        notes = "".join(
            f'<div class="r-small">↳ {esc(note)}</div>' for note in step["notes"]
        )
        st.markdown(
            f'<div class="r-tl {"" if step["phase"] == "explore" else "r-py"}">'
            f"<b>{esc(step['purpose'])}</b>"
            f'<div class="r-small">{esc(step["tool"])} · '
            f"{PHASE_ACTOR[step['phase']]}{evidence}{cached}</div>"
            f"{reason}<div>{esc(step['summary'])}</div>{notes}</div>",
            unsafe_allow_html=True,
        )
    with st.expander("Raw trace (JSONL events)"):
        st.json(scenario["trace"], expanded=False)


def outcome_card(payload: dict) -> None:
    """Compact, deterministic diagnosis for a run that did not complete."""

    summary = logic.outcome_summary(payload)
    title = "RIPPLE abstained" if summary["status"] == "abstained" else "Run failed"
    with st.container(border=True):
        st.markdown(
            badge(
                title.upper(), "r-warn" if summary["status"] == "abstained" else "r-bad"
            )
            + f'<div class="r-title">{esc(summary["why"])}</div>',
            unsafe_allow_html=True,
        )
        columns = st.columns(3)
        columns[0].metric("Stop reason", summary["stop_reason"])
        columns[1].metric("Candidate areas investigated", summary["investigated"])
        columns[2].metric("Confirmed", summary["confirmed"])
        if st.button("Explain this run", key="explain-run"):
            st.markdown(logic.diagnose_run(payload))
            st.caption("Answered from run metadata · no model call")


def explainer_section(scenario: dict, key: str) -> None:
    heading("Optional", "Ask RIPPLE about this result")
    st.caption(
        "Run-status questions (why it stopped, calls, tokens, runtime, provider "
        "errors) are answered directly from run metadata with no model call. Other "
        "questions go to a model that may use only this result's report, evidence, "
        "trace, and post-change findings; answers citing anything else are replaced "
        f"with: “{logic.REFUSAL}”"
    )
    picked = st.pills(
        "Suggested questions", logic.suggested_questions(scenario), key=f"pills-{key}"
    )
    question = st.text_input("Question", value=picked or "", key=f"question-{key}")
    if not st.button("Ask", key=f"ask-{key}"):
        return
    diagnostic = logic.answer_diagnostic(question, scenario)
    if diagnostic is not None:
        st.markdown(diagnostic)
        st.caption("Answered from run metadata · no model call")
        return
    if not logic.llm_config_status()["configured"]:
        st.info(
            "That question needs the grounded explainer, which requires "
            "RIPPLE_LLM_API_KEY and RIPPLE_LLM_MODEL. Run-status questions work "
            "without it."
        )
        return
    context = logic.build_explainer_context(scenario, _benchmark())
    try:
        with st.spinner("Answering from the saved evidence…"):
            answer = logic.ask_explainer(question, context, OpenAILLM.from_env())
    except (LLMError, logic.DemoError) as error:
        st.error(logic.friendly_error(error))
        return
    st.markdown(logic.redact(answer.answer))
    if answer.citations:
        st.caption("Citations: " + " · ".join(answer.citations))


# --------------------------------------------------------------------------- Demo


def page_demo() -> None:
    st.markdown(
        '<div class="r-hero"><h1>RIPPLE</h1>'
        '<div class="r-tagline">Know what a code change can break before you make '
        "it.</div>"
        '<div class="r-sub">RIPPLE explores a Python codebase before implementation, '
        "predicts the likely blast radius, and checks the actual Git diff "
        "afterward.</div></div>",
        unsafe_allow_html=True,
    )
    actions = st.container(horizontal=True)
    if actions.button("Try the demo", type="primary", key="cta-try"):
        st.session_state.started = True
    if actions.button("See how it works", key="cta-how"):
        st.switch_page(HOW_PAGE)
    features = (
        ("Before coding", "Predict the blast radius", "Affected files and tests."),
        (
            "Evidence, not guesses",
            "The LLM proposes, Python decides",
            "Suggestions must pass deterministic validation.",
        ),
        (
            "After coding",
            "Check the real diff",
            "Unexpected edits, missing tests, and stale callers.",
        ),
    )
    for column, (eyebrow, title, text) in zip(st.columns(3), features):
        with column.container(border=True):
            st.markdown(
                f'<div class="r-eyebrow">{eyebrow}</div>'
                f'<div class="r-title">{title}</div>'
                f'<div class="r-muted r-feature">{text}</div>',
                unsafe_allow_html=True,
            )
    if st.session_state.get("started"):
        guided_demo()


def guided_demo() -> None:
    names = logic.list_scenarios()
    if not names:
        st.error("No replay fixtures found. Run `python demo/build_fixtures.py`.")
        return
    try:
        scenarios = {name: _scenario(name) for name in names}
    except logic.DemoError as error:
        st.error(str(error))
        return
    st.session_state.setdefault("scenario", names[0])
    heading("Step 1", "Pick a change to make")
    for column, name in zip(st.columns(len(names)), names):
        title, subtitle = SCENARIO_LABEL.get(name, (scenarios[name]["title"], ""))
        chosen = st.session_state.scenario == name
        with column.container(border=True):
            st.markdown(
                f'<div class="r-title">{esc(title)}</div>'
                f'<div class="r-small">{esc(subtitle)}</div>',
                unsafe_allow_html=True,
            )
            if st.button(
                "Selected" if chosen else "Select",
                key=f"pick-{name}",
                disabled=chosen,
                width="stretch",
            ):
                st.session_state.scenario = name
                st.session_state.stage = "request"
                st.rerun()
    scenario = scenarios[st.session_state.scenario]
    stage = st.session_state.setdefault("stage", "request")
    st.markdown(
        f'<div class="r-request">“{esc(scenario["request"])}”</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Replay of a saved, real RIPPLE execution on a small bundled repository. "
        "No API calls are made."
    )
    if stage == "request":
        if st.button("Analyze change", type="primary", key="analyze"):
            st.session_state.stage = "analyzing"
            st.rerun()
        return
    heading("Step 2", "RIPPLE analyzes the repository")
    progress = logic.replay_progress(scenario)
    if stage == "analyzing":
        with st.status("Replaying the saved run…", expanded=True) as status:
            for title, detail in progress:
                st.markdown(f"**{title}** — {detail}")
                time.sleep(0.35)
            status.update(label="Impact report ready", state="complete")
        st.session_state.stage = "analyzed"
        st.rerun()
    with st.expander("Analysis steps (replayed from the saved run)"):
        for title, detail in progress:
            st.markdown(f"✓ **{title}** — {detail}")
    heading("Step 3", "Predicted impact")
    impact_grid(logic.impact_cards(scenario), scenario["id"])
    with st.expander("How RIPPLE narrowed the search"):
        narrowing_section(scenario)
    with st.expander("See how RIPPLE investigated"):
        timeline_section(scenario)
    if stage == "analyzed":
        st.markdown('<div class="r-section"></div>', unsafe_allow_html=True)
        if st.button("See what happened after coding →", type="primary", key="after"):
            st.session_state.stage = "after"
            st.rerun()
        return
    heading("Step 4", "After the code was changed")
    st.markdown(
        '<div class="r-muted" style="margin-bottom:.8rem">What the developer actually '
        f"did: {esc(scenario['implementation'])}</div>",
        unsafe_allow_html=True,
    )
    stage_b_section(scenario["verification"], scenario["id"])
    with st.expander("View the actual diff"):
        st.code(scenario["diff"], language="diff")
    st.caption(
        "Categories come from deterministic Git and AST rules; no model took part in "
        "this check."
    )
    with st.expander("How this replay was produced"):
        st.markdown(
            f"{scenario['generation']['note']} Scripted model id: "
            f"`{scenario['generation']['model']}`. Regenerate with "
            "`python demo/build_fixtures.py`."
        )
    explainer_section(scenario, scenario["id"])


# --------------------------------------------------------------------------- How it works


def page_how() -> None:
    heading("How it works", "Two checks around every change", first=True)
    nodes = (
        ("Change request + Python repo", "", ""),
        ("Scan code", "Python AST: symbols, imports, references, tests", ""),
        ("LLM investigates", "using seven controlled, read-only tools", "r-key"),
        ("Python validates", "every claim against evidence it produced", "r-key"),
        ("Impact report", "files, tests, regression areas, order", ""),
        ("Developer implements the change", "", "r-human"),
        ("Git diff", "", ""),
        (
            "Post-change check",
            "expected · adjacent · unexpected · missing test · stale caller",
            "",
        ),
    )
    parts = []
    for position, (title, detail, tone) in enumerate(nodes):
        if position:
            parts.append('<div class="r-arrow">↓</div>')
        small = f"<small>{esc(detail)}</small>" if detail else ""
        parts.append(f'<div class="r-node {tone}">{esc(title)}{small}</div>')
    st.markdown(f'<div class="r-flow">{"".join(parts)}</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="r-statement">The LLM <span>proposes</span>. '
        "Python <span>decides</span>.</div>",
        unsafe_allow_html=True,
    )
    with st.expander("Static analysis with the Python AST"):
        st.markdown(
            "RIPPLE reads only Git-tracked Python files and parses them with Python's "
            "`ast` module to index symbols, imports, references, and test mappings. "
            "It never imports, installs, or runs the target project."
        )
        for item in logic.supported_capabilities()["supported"]:
            st.markdown(f"- {item}")
    with st.expander("The seven tools the agent may use"):
        tools = logic.tool_guide()
        for start in range(0, len(tools), 2):
            for column, tool in zip(st.columns(2), tools[start : start + 2]):
                with column.container(border=True):
                    st.markdown(
                        f'<div class="r-title r-mono">{esc(tool["name"])}</div>'
                        f'<div class="r-muted">{esc(tool["purpose"])}</div>'
                        f'<div class="r-small">e.g. “{esc(tool["example"])}”</div>',
                        unsafe_allow_html=True,
                    )
        st.caption(
            "Post-change only (optional model investigation; cannot change a "
            "category): "
            + "; ".join(f"{t['name']}: {t['purpose']}" for t in logic.stage_b_tools())
        )
    with st.expander("Candidate ledger"):
        st.markdown(
            "Every candidate file or symbol is **suspected**, **confirmed**, or "
            "**rejected**. The model may request a move; Python accepts it only when "
            "the target exists, the update cites the latest evidence, and that "
            "evidence actually touched the target. A report can be submitted only "
            "after every confirmed target has had its references and tests checked."
        )
    with st.expander("Deterministic validator"):
        st.markdown(
            "After submission, Python adds tests, regression areas, migration "
            "proposals, and an implementation order, then removes any component that "
            "was never confirmed and any claim citing evidence that does not exist. "
            "Everything removed is listed in the report."
        )
        if "soft_delete" in logic.list_scenarios():
            example = _scenario("soft_delete")["report"]["dropped_claims"]
            st.markdown(
                "Example from the soft-delete replay: "
                + ", ".join(f"`{item}`" for item in example)
            )
    with st.expander("Safety controls"):
        for item in logic.SAFETY_MECHANISMS:
            st.markdown(f"**{item['title']}** — {item['plain']}")
            st.caption(item["where"])
    with st.expander("Post-change categories"):
        for name, info in logic.CATEGORY_INFO.items():
            st.markdown(
                f"{logic.STAGE_B_ICON[name]} **{info['label']}** — {info['meaning']}"
            )
    with st.expander("Prompts"):
        st.markdown("**System instruction sent with every model call**")
        st.code(logic.provider_instructions(), language="text")
        names = logic.list_scenarios()
        prompts = logic.captured_prompts(_scenario(names[0])) if names else {}
        for role in logic.PROMPT_ROLES:
            st.markdown(f"**{role['role']}** — {role['purpose']}")
            text = (
                logic.EXPLAINER_INSTRUCTIONS
                if role["operation"] == "explain"
                else prompts.get(role["operation"])
            )
            if text:
                st.code(text, language="text")


# --------------------------------------------------------------------------- Results


def page_results() -> None:
    benchmark = _benchmark()
    if not benchmark:
        st.error("evaluation/results/final_summary.json is missing or malformed.")
        return
    head = logic.showcase_headline(benchmark)
    heading("Final evaluation", "Tested on real open-source changes", first=True)
    stats = (
        (head["tasks"], "Held-out changes"),
        (head["repositories"], "Open-source repositories"),
        (f"{head['runs']:,}", "Evaluation runs"),
    )
    for column, (value, label) in zip(st.columns(3), stats):
        with column.container(border=True):
            st.markdown(
                f'<div class="r-stat">{esc(value)}</div>'
                f'<div class="r-statlabel">{esc(label)}</div>',
                unsafe_allow_html=True,
            )
    heading("Prediction quality", "Which files will change?")
    systems = (
        ("One-shot LLM baseline (B3)", head["b3_f1"]),
        ("RIPPLE", head["ripple_f1"]),
    )
    for column, (name, value) in zip(st.columns(2), systems):
        with column.container(border=True):
            st.markdown(
                f'<div class="r-eyebrow">{esc(name)}</div>'
                f'<div class="r-stat">F1 {logic.fmt_metric(value)}</div>',
                unsafe_allow_html=True,
            )
    st.error("**The full agent did not beat the simpler baseline.**")
    st.markdown(
        f"**Why?** Under the frozen `{benchmark['summary']['model']}` configuration, "
        f"RIPPLE abstained on {head['ripple_abstained']}/{head['ripple_runs']} runs "
        "because its deterministic evidence gate rejected unsupported predictions. "
        "This interpretation is consistent with the saved traces; it is not proven to "
        "be the only cause."
    )
    heading("What was demonstrated", "Engineering results")
    for item in (
        f"A reproducible {head['runs']:,}-run benchmark",
        f"{head['baselines']} baselines and {head['ablations']} ablations",
        "A deterministic trust boundary around the LLM",
        "A post-change verifier for Git diffs",
        "Planted-anomaly testing of that verifier",
    ):
        st.markdown(f"- {item}")
    with st.container(border=True):
        st.markdown(
            '<div class="r-eyebrow">Post-change verifier</div>'
            f'<div class="r-stat">{head["stage_b_min_recall"]:.0%}</div>'
            '<div class="r-statlabel">planted-anomaly detection recall across '
            "unrelated-file, dropped-test, and stale-caller variants on "
            f"{head['stage_b_tasks']} tasks with oracle Stage-A predictions</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Oracle Stage-A result; not end-to-end RIPPLE accuracy. Untouched "
            f"controls raised a false alarm {head['stage_b_false_alarm']:.0%} of the "
            "time."
        )
    with st.expander("View full research metrics"):
        research_metrics(benchmark)
    v2_development_section()


def v2_development_section() -> None:
    try:
        dev = logic.load_v2_dev()
    except logic.DemoError as error:
        st.error(str(error))
        return
    if not dev:
        return
    with st.expander("V2 development results (not a benchmark)"):
        st.warning(
            "Development-only comparison of the V1 and V2 agent protocols on "
            f"{len(dev['cases'])} development cases chosen after V1's results were "
            "known. It does not replace the frozen final-v1 benchmark above; a V2 "
            "benchmark would need a new untouched held-out set."
        )
        labels = (
            ("runs", "Runs"),
            ("provider_failures", "Provider failures (excluded below)"),
            ("completed", "Completed"),
            ("partial", "Partial (confirmed, not submitted)"),
            ("abstained", "Abstained"),
            ("with_confirmed", "Runs with a confirmed candidate"),
            ("unsupported_accepted", "Unsupported claims accepted"),
            ("tool_calls", "Mean tool calls"),
            ("model_calls", "Mean model calls"),
            ("duplicate_calls", "Mean duplicate calls"),
            ("invalid_tool_targets", "Mean invalid tool-target attempts"),
        )
        protocols = dev["protocols"]
        st.dataframe(
            [
                {"Metric": label}
                | {
                    name.upper(): (
                        f"{protocols[name][key]:.1f}"
                        if isinstance(protocols[name][key], float)
                        else protocols[name][key]
                    )
                    for name in protocols
                }
                for key, label in labels
            ],
            hide_index=True,
            width="stretch",
        )
        st.caption("Source: evaluation/v2_dev/results.json · details in its README.")


def research_metrics(benchmark: dict) -> None:
    summary = benchmark["summary"]
    st.markdown("**Baselines and RIPPLE** (seed-averaged per task, then over tasks)")
    st.dataframe(
        logic.benchmark_rows(benchmark, logic.HEADLINE_SYSTEMS),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "B0 BM25 · B1 BM25 + dependencies · B2 BM25 + co-change · B3 one-shot LLM · "
        "B4 plain ReAct agent. B0–B2 set metrics use RIPPLE's predicted-file count for "
        "the same task and seed, so they are near zero when RIPPLE abstains."
    )
    st.markdown("**Ablations**")
    st.dataframe(
        logic.benchmark_rows(benchmark, logic.ABLATIONS),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "A1 without co_changed · A2 without get_dependencies/find_references · "
        "A3 without the report validator."
    )
    st.markdown("**Run status by system**")
    st.dataframe(
        [
            {"System": system} | counts
            for system, counts in summary["status_mix_by_system"].items()
        ],
        hide_index=True,
    )
    totals = summary["totals"]
    st.markdown(
        f"**Totals:** {logic.fmt_count(totals['model_calls'])} model calls · "
        f"{logic.fmt_count(totals['total_tokens'])} tokens · "
        f"{logic.fmt_count(totals['tool_calls'])} tool calls · "
        f"{totals['runtime_seconds'] / 3600:.2f} hours recorded runtime"
    )
    ripple = summary["stage_b_ripple"]["metrics"]
    if ripple:
        st.markdown(
            "**Post-change check with RIPPLE's own seed-17 reports:** detection "
            f"recall {logic.fmt_metric(ripple['unrelated_detection_recall'])} / "
            f"{logic.fmt_metric(ripple['drop_tests_detection_recall'])} / "
            f"{logic.fmt_metric(ripple['stale_caller_detection_recall'])}, control "
            f"false-alarm rate {logic.fmt_metric(ripple['control_false_alarm_rate'])}. "
            "Uninformative: those reports mostly abstained, so every changed file "
            "looked unexpected."
        )
    st.caption(
        f"Source: evaluation/results/final_summary.json · config "
        f"`{summary['config_version']}` · hash `{summary['config_hash'][:16]}…`. "
        "Confidence intervals and paired comparisons: evaluation/results/README.md."
    )


# --------------------------------------------------------------------------- Live


def page_live() -> None:
    heading("Live analysis", "Run RIPPLE on your own repository", first=True)
    live = logic.llm_config_status()
    st.caption(
        f"Runs locally. Live model `{live['model'] or 'not configured'}` from your "
        f"environment · benchmark model `{logic.benchmark_model()}` (frozen final-v1). "
        "Live results are not benchmark results. RIPPLE never edits the repository or "
        "runs its code."
    )
    repo_text = st.text_input("Repository", placeholder="/path/to/python/repository")
    request_text = st.text_input(
        "Feature request", placeholder="Add soft-delete support to users."
    )
    allow_dirty = st.checkbox("Allow uncommitted changes")
    if st.button("Analyze", type="primary", key="live-analyze"):
        _run_live(repo_text, request_text, allow_dirty)
    if st.session_state.get("live"):
        _show_live(st.session_state.live)


def _run_live(repo_text: str, request_text: str, allow_dirty: bool) -> None:
    try:
        repo = logic.validate_live_repo(repo_text)
        request = FeatureRequest(text=request_text)
        llm = OpenAILLM.from_env()
        index = scan_repository(repo)
    except logic.DemoError as error:
        st.error(str(error))
        return
    except (LLMError, ScanError) as error:
        st.error(logic.friendly_error(error))
        return
    except ValueError as error:  # FeatureRequest validation
        st.error(f"Feature request rejected: {logic.redact(str(error))[:300]}")
        return
    if index.dirty and not allow_dirty:
        st.error(
            "The repository has uncommitted changes. Commit or stash them, or allow "
            "uncommitted changes."
        )
        return
    controller = AgentController(
        index, llm, output_root=logic.LIVE_OUTPUT / repo.name, variant=RIPPLE_V2
    )
    started = time.perf_counter()
    with st.status("Analyzing…") as status:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(controller.run, request)
            while not future.done():
                status.update(
                    label=f"Analyzing… {time.perf_counter() - started:.0f}s · "
                    f"{controller.tool_calls} tool calls · "
                    f"{controller.llm_calls} model calls"
                )
                time.sleep(0.5)
            run = future.result()
        status.update(label="Analysis finished", state="complete")
    st.session_state.live = {
        "repo": str(repo),
        "payload": logic.capture_run(controller, run),
        "report_path": str(run.report_path),
    }


def _show_live(live: dict) -> None:
    payload = live["payload"]
    report = payload["report"]
    if report["status"] in {"abstained", "failed"}:
        outcome_card(payload)
    heading("Result", "Predicted impact")
    cards = logic.impact_cards(payload)
    if cards:
        impact_grid(cards, "live")
    else:
        st.markdown("No validated components.")
    heading("After coding", "Verify the actual diff")
    git_range = st.text_input("Git range", value=f"{report['commit']}..HEAD")
    if st.button("Verify current diff", key="live-verify"):
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
            st.error(f"Could not verify: {logic.redact(str(error))[:300]}")
        else:
            if not verification.analysis.changes:
                st.info("No committed changes in that range yet.")
            else:
                if verification.analysis.base_warning:
                    st.warning(verification.analysis.base_warning)
                payload["verification"] = verification.analysis.model_dump(mode="json")
    if payload.get("verification"):
        stage_b_section(payload["verification"], "live")
    with st.expander("Run details"):
        stats = logic.report_view(report)["stats"]
        st.markdown(
            f"Status `{report['status']}` · stop reason `{stats['stop_reason']}` · "
            f"{stats['tool_calls']} tool calls · {stats['llm_calls']} model calls · "
            f"{logic.fmt_count(stats['total_tokens'])} tokens · "
            f"{stats['runtime_seconds'] or 0:.1f}s · model `{stats['model']}`"
        )
        st.markdown("**How RIPPLE narrowed the search**")
        narrowing_section(payload)
        st.markdown("**Investigation**")
        timeline_section(payload)
    explainer_section(payload, "live")


# --------------------------------------------------------------------------- Details


def page_about() -> None:
    heading("Technical details", "Scope, metrics, and provenance", first=True)
    capabilities = logic.supported_capabilities()
    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown("**Supported**")
        for item in capabilities["supported"]:
            st.markdown(f"- {item}")
    with right.container(border=True):
        st.markdown("**Not supported / not claimed**")
        for item in capabilities["not_supported"]:
            st.markdown(f"- {item}")
    with st.expander("What the metrics mean"):
        st.markdown(
            "Example: the real change touched A, B, C; a system predicted A, B, X, Y."
        )
        for name, meaning in (
            ("Precision", "When RIPPLE predicts a file, how often is it right? (2/4)"),
            ("Recall", "Of the files that really changed, how many were found? (2/3)"),
            ("F1", "One number balancing precision and recall."),
            ("Recall@5 / @10", "Coverage of the real change in the top 5 / 10."),
            ("MRR", "How high the first correct suggestion appears in the ranking."),
            ("FP/task", "Wrong file predictions per task on average (2 here)."),
            ("Abstention", "No prediction for lack of evidence; it scores zero."),
        ):
            st.markdown(f"**{name}** — {meaning}")
    with st.expander("About the demo replays"):
        st.markdown(
            "The three Demo scenarios replay runs on a bundled sample repository. The "
            "model's decisions were scripted with RIPPLE's own test double; every tool "
            "result, ledger decision, dropped claim, report, and post-change category "
            "was produced by the real RIPPLE code. See demo/README.md."
        )
    st.caption(
        "The full developer/research UI lives on the `ripple-demo` branch; the "
        "complete write-up is in README.md and evaluation/results/README.md."
    )


DEMO_PAGE = st.Page(page_demo, title="Demo", default=True)
HOW_PAGE = st.Page(page_how, title="How it works", url_path="how-it-works")
RESULTS_PAGE = st.Page(page_results, title="Results", url_path="results")
LIVE_PAGE = st.Page(page_live, title="Live analysis", url_path="live")
ABOUT_PAGE = st.Page(page_about, title="Technical details", url_path="details")
NAVIGATION = (DEMO_PAGE, HOW_PAGE, RESULTS_PAGE, LIVE_PAGE, ABOUT_PAGE)

st.navigation(list(NAVIGATION), position="top").run()
