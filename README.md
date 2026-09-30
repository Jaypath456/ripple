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
and exposes five bounded deterministic tools. Repository code is never imported
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

## Evidence-led agent

Phase 4 adds a bounded LLM controller over the same five deterministic tools.
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
index cache and marks both the report and trace. Reports are atomically written
to `.ripple/reports/`; JSONL traces go to `.ripple/runs/`. JSON mode emits only
the validated report on standard output and never includes the API key.

The fixed Phase 4 MVP comparison retains the original ten development tasks and
adds ten eligible tasks from distinct repositories:

```shell
ripple evaluate-agent --tasks evaluation/data/mvp_tasks.json
```

B0 and B1 are scored at each task's `agent_k`, the number of validated RIPPLE
source components. An empty agent report therefore gives every system exact-zero
set metrics for that task; full rankings still determine R@5, R@10, and MRR.

Phase 4 remains conservative static analysis. It does not execute target code,
edit repositories, infer routes/ORM/settings/migrations, use co-change history,
or implement Stage B Git-diff verification, B2–B4, `verify`, or `show-run`.

Run the checks with:

```shell
pytest -q
ruff check .
ruff format --check .
```
