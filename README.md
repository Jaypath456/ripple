# RIPPLE

RIPPLE is a personal developer-tool project that will eventually predict which
parts of a Python repository are likely to change for a proposed feature, then
compare that prediction with the resulting Git diff.

RIPPLE records a Git repository's current commit and dirty state, discovers its
tracked Python files, derives module names, identifies test files, and uses
Python's AST to index structural symbols and imports. Imports include aliases,
relative levels, `TYPE_CHECKING` status, and deterministic resolution to tracked
modules when possible. RIPPLE also resolves common symbol references, builds a
module dependency graph and likely test mappings, supports BM25 lexical search,
and exposes seven bounded deterministic tools. Repository code is never imported
or executed.

## Setup

RIPPLE requires Python 3.11 or newer. Install it and the development tools in an
active virtual environment:

```shell
python -m pip install -e '.[dev]'
```

## Usage

```shell
ripple scan /path/to/repository
```

The scan considers only Python files tracked by Git. Its summary reports the
repository name, abbreviated current commit, Python and test file counts, symbol,
import, and reference counts, parse errors, cache status, and elapsed scan time.
Syntax and file-reading errors are recorded without stopping the rest of the
scan.

Indexes are cached as JSON under `<repo>/.ripple/index/`. A clean index is named
for its full commit SHA. A dirty index also includes a deterministic hash of the
current contents of tracked Python files, so changed source states do not share a
cache entry. Cache files are written atomically.

Use JSON output for tooling:

```shell
ripple scan /path/to/repository --json
```

Standard output contains only the complete `RepositoryIndex` JSON in this mode.
Use `--no-cache` to perform a fresh scan without reading or writing cache files:

```shell
ripple scan /path/to/repository --no-cache
```

## Deterministic tools

Every tool accepts a repository followed by its name and one JSON argument
object. Results are JSON envelopes containing data or a structured error, an
invocation-local evidence ID, and a truncation flag.

```shell
ripple tool ./sample search_code '{"query":"user auth","limit":5}'

ripple tool ./sample inspect_symbol \
  '{"target":"app/models/user.py::User"}'

ripple tool ./sample find_references \
  '{"symbol_id":"app/models/user.py::User","limit":20}'

ripple tool ./sample get_dependencies \
  '{"path":"app/models/user.py","direction":"imported_by","depth":2}'

ripple tool ./sample find_tests \
  '{"target":"app/models/user.py::User"}'

ripple tool ./sample repo_facts \
  '{"kind":"models","filter":"User"}'

ripple tool ./sample co_changed \
  '{"path":"app/models/user.py","limit":10}'
```

Tool paths must be repository-relative. Search results are capped at 15,
reference results at 40, dependency traversal at depth 2, symbol source at 120
lines, and likely-test results at 50.

Symbol signatures and expressions are reconstructed from the AST, so they are
deterministic and readable but may normalize whitespace and quote style from the
original source.

Import resolution uses only tracked module names. Star imports are recorded but
not expanded, and ambiguous or external modules remain unresolved.

Reference resolution covers directly imported symbols and aliases, imported
module attributes, same-file top-level symbols, class-method attributes,
subclasses, and decorators. Resolved references are high confidence. Unresolved
attribute calls are retained as low confidence without guessing the receiver's
type; ordinary unresolved names are omitted.

Untracked files are intentionally absent from both the index and dirty-state
calculation. RIPPLE-owned `.ripple/` contents are always excluded.

## Deterministic evaluation

Phase 3 adds a leak-safe development harness over ten fixed official FEA-Bench
Lite tasks. It reconstructs fresh base-only checkouts, masks location-revealing
request text, classifies PR-diff gold files, runs BM25 (B0) and BM25 plus
one-hop dependency expansion (B1), and reports per-task and aggregate metrics
with deterministic repository-cluster bootstrap intervals.

```shell
ripple evaluate --tasks evaluation/data/dev_tasks.json
```

The command writes the auditable full result to
`evaluation/results/dev_baselines.json` and prints a concise aggregate table.
See [`evaluation/README.md`](evaluation/README.md) for provenance, exact
algorithms, leak controls, task IDs, results, and limitations.

## Evidence-led agent and full reports

Phase 4 added a bounded LLM controller over the original five deterministic tools.
The model interprets the request, chooses one tool at a time, and drafts a
report; Python validates every argument, owns candidate state and evidence IDs,
enforces budgets, and drops unsupported claims. Repository text is always
delimited as untrusted data. A run stops after at most 25 unique tool
executions, four no-progress iterations, 30 candidates, accepted submission, or
an optional token ceiling. Duplicate calls reuse their earlier result.

Configure the provider with environment variables (see `.env.example`):

```shell
export RIPPLE_LLM_API_KEY=...
export RIPPLE_LLM_BASE_URL=https://api.openai.com/v1  # optional
export RIPPLE_LLM_MODEL=...
# export RIPPLE_MAX_TOKENS=50000                     # optional
```

Analyze a clean checkout with:

```shell
ripple analyze /path/to/repository "Add token refresh support"
ripple analyze /path/to/repository "Add token refresh support" --json
```

Dirty repositories are refused by default. `--allow-dirty` uses the dirty-state
index cache and marks both the report and trace.

Phase 5 keeps that Explore controller and adds deterministic expansion after it.
`repo_facts` statically extracts conservative FastAPI, Flask, and Django routes;
Django, SQLAlchemy, and Pydantic models; environment/settings reads; migration
directories; and common entry points. `co_changed` counts partners in up to 500
commits reachable from the checkout's `HEAD` and never examines another ref.

Expand adds mapped tests, ranks one-hop reverse dependencies and reference sites
as regression areas (without promoting them to predicted changed files), proposes
a migration only when a confirmed model change and an existing migration directory
are both proven, and computes dependency-first implementation order with tests
last. Schema/API/config claims and model-authored risks retain evidence IDs; the
validator removes unsupported claims. Blind spots describe observed static-analysis
limits rather than generic caveats.

Canonical JSON and deterministic Markdown reports are atomically written beside
one another under `.ripple/reports/`; JSONL traces go to `.ripple/runs/`. JSON mode
emits only the validated report on standard output and never includes the API key.
Replay a trace without making a model call:

```shell
ripple show-run <run-id> --repo /path/to/repository
```

The fixed Phase 4 MVP comparison retains the original ten development tasks and
adds ten eligible tasks from distinct repositories:

```shell
ripple evaluate-agent --tasks evaluation/data/mvp_tasks.json
```

B0 and B1 are scored at each task's `agent_k`, the number of validated RIPPLE
source components. An empty agent report therefore gives every system exact-zero
set metrics for that task; full rankings still determine R@5, R@10, and MRR.

The saved Phase 4 `mvp-v1.1` development run is frozen. With
`gemini-3.5-flash-lite`, its 20/20 valid task metrics were precision 0.2000,
recall 0.08333, F1 0.11667, R@5 0.08333, R@10 0.08333, MRR 0.2000, and 0.05
false positives per task. These are development measurements, not final held-out
results.

Phase 5 uses configuration `full-report-v1` and adds three comparison baselines:

- B2 expands the top three B0 seeds using pre-base co-change frequency.
- B3 makes one model call over the request, repository map, and bounded symbol
  outline, then drops nonexistent paths.
- B4 is plain ReAct with the same safe tools and 25-call ceiling, but no candidate
  ledger, deterministic Expand, no-progress controller, or report validator.

Run the separate six-system development comparison with:

```shell
ripple evaluate-phase5 --tasks evaluation/data/mvp_tasks.json
```

It writes `evaluation/results/phase5_dev_comparison.json` and is labeled
`DEVELOPMENT / PHASE 5 — NOT FINAL HELD-OUT RESULTS`. Phase 5 still never executes
or edits target code. Stage B diff verification, `ripple verify`, and every Phase
6/7 feature remain intentionally unimplemented. No Phase 5 aggregate is currently
claimed: the first full comparison attempt exhausted the configured provider's
daily request quota before all 20 tasks completed, so no partial aggregate was
published.

Run the checks with:

```shell
pytest -q
ruff check .
ruff format --check .
```
