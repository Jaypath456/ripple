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

Current functionality is deterministic static repository analysis: structural
facts, symbol references, direct module dependencies, bounded two-hop graph
queries, likely test mapping, lexical search, and manually callable tools.
RIPPLE does not yet include an LLM agent, natural-language change-impact reports,
Stage B Git-diff verification, or evaluation results.

Run the checks with:

```shell
pytest -q
ruff check .
ruff format --check .
```
