# RIPPLE

RIPPLE is a personal developer-tool project that will eventually predict which
parts of a Python repository are likely to change for a proposed feature, then
compare that prediction with the resulting Git diff.

RIPPLE records a Git repository's current commit and dirty state, discovers its
tracked Python files, derives module names, identifies test files, and uses
Python's AST to index structural symbols and imports. Imports include aliases,
relative levels, `TYPE_CHECKING` status, and deterministic resolution to tracked
modules when possible. Phase 2A also resolves common same-file and imported
symbol references without executing repository code. Dependency graphs, search,
Git diff comparison, and AI-driven analysis are future work.

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
calculation. RIPPLE-owned `.ripple/` contents are always excluded. RIPPLE does
not yet build dependency graphs, perform transitive analysis, search code, or
run an AI agent.

Run the checks with:

```shell
pytest -q
ruff check .
ruff format --check .
```
