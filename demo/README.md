# RIPPLE showcase

A Streamlit app that explains and exercises RIPPLE in 60–120 seconds. This branch
(`ripple-showcase`) is the recruiter/client-facing version. The detailed
developer/research UI, with ten pages and every internal shown up front, lives on
the `ripple-demo` branch.

## Navigation

| Page | What it shows |
| --- | --- |
| **Demo** | Hero, then a guided story: pick a change → *Analyze change* (replayed progress with real counts) → impact cards with **Why?** evidence → *See what happened after coding* (post-change findings, flagged issues first). The investigation timeline, “How RIPPLE narrowed the search” (the candidate ledger), the raw trace, and the diff are behind expanders. |
| **How it works** | One flow diagram and “The LLM proposes. Python decides.”, with expandable sections for the AST, the seven tools, the ledger, the validator, safety controls, post-change categories, and prompts. |
| **Results** | The headline numbers from `final_summary.json`, B3 vs RIPPLE F1, the honest callout and why, what was demonstrated, and the oracle Stage-B card with its caveat. The full research tables sit behind *View full research metrics*. |
| **Live analysis** | Repository + request → one progress indicator → predicted impact, then diff verification. Tokens, calls, stop reason, the ledger, and the trace are in *Run details*. |
| **Technical details** | Supported / not supported, plain-English metrics, and replay provenance. |

The app reads `.streamlit/config.toml` (accent colour for light and dark themes, minimal
toolbar) when started from the repository root. It is a separate
layer on top of the finished core: it imports `ripple` modules directly, copies no
algorithm, and never changes the frozen `final-v1` benchmark artifacts.

## Run it locally

From the repository root:

```shell
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[demo]'
streamlit run demo/app.py
```

The default install (`pip install -e .`) does not pull in Streamlit.

## Run it in a container

The image serves Guided Replay and the explanation pages; nothing on your machine is
mounted by default.

```shell
docker build -f demo/Dockerfile -t ripple-demo .
docker run --rm -p 8501:8501 ripple-demo        # open http://localhost:8501
```

To use Live analysis inside the container, mount a repository read-only and pass the
model settings. Never bake a key into the image.

```shell
docker run --rm -p 8501:8501 --env-file .env \
  -v /path/to/repo:/repos/target:ro ripple-demo   # analyze /repos/target
```

The demo is not deployed publicly. A hosted copy should expose Guided Replay only,
because a server cannot safely read paths on a visitor's computer. Live analysis is
meant for local use.

## Two modes

**Demo / guided replay (no API key, no model calls).** Three saved runs on the bundled sample
repository `fixtures/sample_app`. Each run steps through:

1. the feature request;
2. the repository scan;
3. every tool call and the reason the model gave for it;
4. the candidate ledger;
5. validation;
6. the Stage A report;
7. the real implementation diff;
8. the Stage B categories;
9. the final verification report.

| Scenario | Request | What it demonstrates |
| --- | --- | --- |
| `soft_delete` | Add soft-delete support to users | Model and service impact, the deterministic migration proposal, mapped tests, a rejected look-alike (`billing/service.py::delete_invoice`), and an unsupported claim the validator dropped (`users/auth.py::login`) |
| `required_argument` | Add a required actor argument to `delete_user` | The signature change, the updated API route, and the admin job that still calls the old signature, which Stage B flags as `stale_caller` |
| `deletion_behavior` | Change user deletion behavior | The service change with an untouched mapped test (`missing_test`), plus an unrelated edit to `orders/service.py` (`unexpected`) |

**Live analysis (local).** This mode runs the real Stage A on a local Git repository
and then, optionally, Stage B on a Git range you choose. It needs an
OpenAI-compatible model configured through environment variables (see
`.env.example`):

```shell
export RIPPLE_LLM_API_KEY=...   # never displayed, never written to reports
export RIPPLE_LLM_MODEL=...
export RIPPLE_LLM_BASE_URL=...  # optional
```

- **What it shows.** Progress while the run is going (elapsed time, tool calls,
  model calls, tokens when the provider reports them, and the last trace event). When
  the run finishes it shows the report, trace, ledger, validation, and a
  **Verify current diff** tab.
- **What it writes.** All outputs go to `.ripple/demo-live/`, which is gitignored.
  The target repository is scanned without the index cache, and Stage B reads commits
  through a temporary clone, so nothing is written into the target repository.
- **Dirty checkouts.** Repositories with uncommitted changes are refused unless you
  explicitly allow them.
- **Errors.** These are shown as messages, not stack traces:
  - a missing path, a non-directory, a non-Git directory, or no tracked Python files;
  - missing credentials, authentication failures, timeouts, and connection errors;
  - abstention;
  - an empty diff range;
  - malformed fixtures or benchmark files.

The live model is configured separately from the benchmark. The app shows both side
by side: the live model comes from your environment, while the final benchmark model
is `openai/gpt-oss-20b`, read from `evaluation/final_config.json`. Live behavior is
not a benchmark result.

## How the replay fixtures were made

`python demo/build_fixtures.py` regenerates them. It works in four steps:

1. It builds a Git repository from `fixtures/sample_app`, using fixed authors and
   dates so commit SHAs are stable. The `history/` versions are committed first, so
   `co_changed` sees real co-change history.
2. It commits one implementation branch per scenario.
3. It runs the real `AgentController` (Stage A).
4. It runs the real `verify_repository` (Stage B).

**Only the model's decisions are scripted**, using RIPPLE's own `ScriptedLLM` test
double, with the model id `scripted-demo-policy`. Everything else comes from the core:

- every tool result and evidence ID;
- every ledger acceptance or rejection;
- every dropped claim;
- the Expand output and the report;
- every Stage B category.

The app states this on the replay page. The fixtures were scripted because they must
replay identically without an API key. `tests/test_demo.py` rebuilds the repository
and checks that the core reproduces the saved predictions and Stage B findings.

## Ask RIPPLE (optional explainer)

If a model is configured, each analysis offers an explainer. The model receives only
a JSON context containing:

- the request and the report;
- the ledger and the evidence index;
- the condensed trace;
- the Stage B findings;
- the saved benchmark metadata.

It is told to answer only from that context, to list the exact evidence IDs, paths,
or categories it relied on, and otherwise to reply "I don't have enough RIPPLE
evidence to answer that." Python then checks the answer. An unsupported answer, an
answer without citations, or an answer citing anything not present verbatim in the
context is replaced with that refusal. Replay works fully without the explainer.

## Files

| Path | Purpose |
| --- | --- |
| `app.py` | Streamlit UI only |
| `logic.py` | Testable logic: fixture and benchmark loading, view models, redaction, path validation, explainer grounding |
| `build_fixtures.py` | Regenerates `fixtures/replay/*.json` with the real core |
| `fixtures/sample_app/` | Sample repository source (`base/`, `history/`, `scenarios/`), never executed |
| `fixtures/replay/` | Saved replay runs (JSON) |
| `Dockerfile` | Guided Replay container |
