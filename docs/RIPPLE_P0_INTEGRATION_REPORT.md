# RIPPLE V2.1.1 Phase P0 — local implementation validated

Date: 2026-10-31, America/New_York.

**P0 implementation and local validation are complete.**
This report records verification immediately before the separately approved local
commit on `integration/v2.1.1-p0`; its Git-state observations describe that point.
The approved general limits are **2 MiB per source file and 32,768 characters
per source line**. The complete reader audit finds **zero new benchmark
exclusions**. Complete before/after indexes and B0–B2 outputs match for SymPy and
both affected Rich revisions. P1 has not begun. No inference calls, task commits,
pushes or merges were made during implementation. The subsequent approval
authorizes one local P0 commit only.

## Inputs and isolation

- Frozen baseline: `ripple-v2.1.1-frozen`,
  `f381d1c7c6f55e504cee3f9e6beb425d2387ec0d`.
- Delivery snapshot: `review/claude-p0-snapshot-20261031`,
  `674128c569468dd962b690baebee3a7e1a37b41f`.
- Both remote refs were verified during preflight. Before this final report,
  local commit objects, the delivery's independent root property, all 90 files,
  89 checksum entries and 85 imported-file mappings were verified again.
- Source ZIP SHA256:
  `387ce0b397848009f8475f55ca3363b74466b93fe0d000f05636feed722aebd0`.
- Integration worktree:
  an isolated linked worktree, separate from the original checkout.
  Before staging, `integration/v2.1.1-p0` points to the frozen commit and
  all implementation changes are unstaged or untracked.
- The original checkout remains on `review/ripple-accuracy` at
  `da7cf2577c26f546e9d3ac4551356df533159ab1`; its files, index and status
  match their preflight records.

The delivery manifest's inherited baseline is `358dbff…`; this records delivery
lineage. Integration and preservation comparisons use the requested frozen
`f381d1c…` baseline. [Preflight evidence](evidence/p0-integration/preflight.json).

## Changes implemented

| Area | Files under `src/ripple/` | Result |
| --- | --- | --- |
| Git diff hardening | `diffing.py` | NUL-delimited records, literal pathspecs, visible type changes, disabled external diff/textconv, commit option boundary. |
| Checkout safety | `evaluation.py` | Ownership markers; reject symlinked, unowned or edited workspaces while preserving existing user work. |
| Source indexing | `source.py`, `models.py`, `scanner.py`, `search.py`, `facts.py`, `tools.py` | Schema 4; bounded regular-file reads; path/parent-symlink/FIFO guards; 2,097,152-byte and 32,768-character limits; existing 12,000-character inspection cap retained. |
| Cache safety/freshness | `cache.py` | Guard paths and dirty-source reads; record source bounds in cache-only provenance and rebuild legacy or changed-policy schema-4 caches. Index API fields and cache filenames remain unchanged. |
| Verification security | `verification.py` | Disable checkout hooks and prevent report/output symlink redirection. |
| CLI fixes | `cli.py` | Add `verify --offline`; remove unsupported `evaluate-agent` `args.force`; retain V2 CLI and default configured-provider wiring. |

Eight production files match delivery byte-for-byte. `source.py` adds the two
approved bounds and gives its bounded in-memory encoding reader the original
filename. This preserves frozen diagnostics for an existing invalid-encoding
fixture without reopening the path. Path validation, no-follow/nonblocking open,
regular-file validation and maximum-plus-one bounded reads remain identical to
delivery safeguards. There is no fixture whitelist.

The other adaptations are cache-only policy freshness and the selective CLI
port. Caches built under 1 MiB or 16,384 characters rebuild so their omissions
cannot survive the policy change. [Production hashes and adaptations](evidence/p0-integration/source_ports_sha256.json).

Four new regression modules cover security, source boundaries, verification and
CLI behavior. The sole changed preexisting test assertion updates schema 3 to 4.
Controller, prompts, ledger, validator, provider implementation, ranking
algorithms, request preparation, prediction versions and budgets remain
byte-identical to frozen V2.1.1. No P1/P2 implementation was ported.

## Tests, lint and formatting

| Check | Result |
| --- | --- |
| Frozen baseline suite | **235 passed, 6 skipped**, 40.95 seconds |
| Baseline Ruff / formatting | Passed / passed (79 files) |
| Final complete suite | **285 passed, 6 skipped**, 62.48 seconds |
| Affected module after final test style correction | **25 passed**, 4.90 seconds |
| Final full-repository Ruff / formatting | **Passed / passed (84 files)** |

All 241 baseline test identities retain their statuses; **50 new regression
cases pass**. Six unchanged optional UI tests skip because
`streamlit.testing.v1` is unavailable. No dependency was installed.

New regressions include valid Python lines of 16,384, 27,514 and exactly 32,768
characters; a valid 32,769-character literal rejected safely; exact byte bounds
and short-line files exceeding 2 MiB; both checksum-pinned Rich fixtures with
their search string documents retained; the real 1,121,413-byte SymPy `sine.py`;
both source-policy cache migrations; symlinks, path escapes, FIFOs, stale-index
readers, inspection truncation, encoding diagnostics, hostile checkout hooks,
verification output paths and offline CLI behavior.

The real SymPy and Rich fixture tests were exercised with explicit local corpus
paths, rather than skipped. External fixture sources are not copied into this
diff. To repeat them, set `RIPPLE_P0_SYMPY_SINE_SOURCE` to the pinned
`sympy/integrals/rubi/rules/sine.py`, and `RIPPLE_P0_RICH_FIXTURES` to a directory
containing `Textualize__rich-2301` and `Textualize__rich-901` checkouts at the
commits below; tests enforce the recorded source hashes.

The first baseline run needed execution permission for a local Git hard link;
its permitted rerun passed. An early new facts test used the wrong API signature
and was corrected. The final full suite passed but Ruff reported SIM117 in one
new diagnostic test. Combining nested contexts resolved it; the entire affected
25-case module, repository-wide lint and formatting then passed. No production
code changed after the final full-suite run. No unresolved failures remain.

Test interpreters blocked socket connections, removed credential variables,
disabled Git SSH and used fresh temporary outputs. Provider-dependent CLI tests
use local mocks. [Test evidence](evidence/p0-integration/test_results.json).

## Complete corpus-impact audit

The final audit covers **85 pinned historical tasks across 32 repositories**,
including historical development, final and held-out task sets:

| Observation | Result |
| --- | ---: |
| Python-file observations across task pins, not unique files | 45,154 |
| Source bytes observed | 478,913,064 |
| Identical frozen and bounded reader inputs | 45,056 |
| Universal-newline differences with identical attributed ASTs | 97 |
| Preexisting read errors with identical type and message | 1 |
| Newly excluded files | **0** |
| Unexplained reader/AST/diagnostic changes | **0** |
| Retrieval/audit errors | **0** |

Each observation uses the exact task base commit, a clean audit checkout and a
source digest matching the prior inventory. Audit workspaces are separate from
existing benchmark workspaces. No target module was executed, and no benchmark
request or label was changed.

The earlier 16,384-character policy excluded two Rich fixture revisions and
changed scores. It is superseded by the explicit 32,768-character approval.
Both fixtures now index with their string-search documents. The larger SymPy
file remains included under 2 MiB.

| Task | Base commit | Restored file | Bytes | Longest line |
| --- | --- | --- | ---: | ---: |
| `sympy__sympy-14928` | `e53b24ad345e11769dd1989e4873380c0f94b4e2` | `sympy/integrals/rubi/rules/sine.py` | 1,121,413 | 6,553 |
| `Textualize__rich-2301` | `695256ce5316482f15818962ba8f692a460f923f` | `tests/_card_render.py` | 28,666 | 27,514 |
| `Textualize__rich-901` | `a9c0f917aed8d0bba232b7584742962f03a9a293` | `tests/_card_render.py` | 27,870 | 26,718 |

Fixture SHA256 values are enforced by the regression tests:

- SymPy: `843a524e21ba0ceff1a4e4756108f37ad61d0e6bb42c319973dfb913bfd33576`.
- Rich-2301: `9c11d1c7189972e98a865e4e3bdf877282839f8c9e7843a6f9a59a31c201b680`.
- Rich-901: `048ba0f89f19f8e77cefcc77a0678f6b0ad2bafde7816667c4a5dc05a70b622b`.

The intentional invalid-encoding Pylint fixture remains a per-file error with
the same filename-bearing diagnostic as frozen V2.1.1. The audit checks this
error branch as well as successful reads.

[Complete per-task audit](evidence/p0-integration/corpus_reader_audit.json) and
[passing corpus gate](evidence/p0-integration/corpus_impact.json).

## Completed B0–B2 comparisons

Both implementations used the same pinned source, prepared request and 501-level
history. Every comparison completed; no equality is inferred from a timeout.
SymPy received 900 seconds per implementation because an earlier 240-second
probe timed out. Rich received 300 seconds each.

| Corpus | Budget per implementation | Frozen / ported elapsed | Result |
| --- | ---: | ---: | --- |
| SymPy-14928, 1,251 files | 900 seconds | 361.012 / 364.575 seconds | Complete normalized index and all B0/B1/B2 objects identical. |
| Rich-2301, 187 files | 300 seconds | 7.763 / 7.811 seconds | Complete normalized index and all B0/B1/B2 objects identical. |
| Rich-901, 155 files | 300 seconds | 6.809 / 6.925 seconds | Complete normalized index and all B0/B1/B2 objects identical. |

Index normalization excludes only expected `schema_version`, `created_at` and
checkout-specific `repo_root`. All remaining fields are hashed and compared.
Rank comparisons include every source/test prediction object, score and order.
SymPy retains 24,987 symbols, 11,084 imports, 248,832 references, 1,251 graph nodes
and 2,720 test mappings. Both Rich revisions retain the complete frozen evidence.

Full ranking replay covers these **three affected benchmark pins**, not all 85
tasks. The complete 85-task reader audit establishes source coverage and
AST/error compatibility; it is not a substitute full-ranking replay. Acceptance
is limited to that stated coverage.
[Comparison evidence and hashes](evidence/p0-integration/ranking_comparisons.json).

## Preservation and final state

Final hashes verify **8,758 original checkout file contents**, original
status/index, all **870 frozen-worktree evaluation files**, and all other
baseline tracked files outside approved production ports and the schema test
assertion. Held-out manifests, datasets, labels, historical outputs and frozen
performance artifacts were neither changed nor regenerated. Held-out F1 was not
reevaluated.

Main, frozen tags and all preexisting user branch/remote refs retain their SHAs.
At precommit verification the only new user ref is the authorized integration
branch, still at the frozen commit; the approved local commit advances only it. Internal `refs/codex/` capture/checkpoint bookkeeping changes between
turns; no task command wrote or restored those refs. The recorded preservation
check precedes staging; exact staged contents receive another privacy/hash check
before the local commit.
[Preservation evidence](evidence/p0-integration/preservation.json).

Changed/new implementation files and SHA256 values are listed in the
[implementation inventory](evidence/p0-integration/implementation_inventory.json).
The adjacent `SHA256SUMS` covers implementation, tests, this final report and
structured evidence. Prior local reports and raw audit outputs are preserved
outside the worktree; raw requests, traces and source corpora are excluded from
the new report evidence.

## Remaining risks and recommendation

P0 is ready for review of the local diff. Security regressions, the complete
suite, lint, formatting, full reader audit, targeted exact B0–B2 comparisons and
frozen-artifact checks pass. No known P0 regression remains in the tested scope.

Coverage is limited to Python 3.13, with six optional UI tests unavailable; no
live inference or accuracy measurements were made. Full B0–B2 replay was not
run across all 85 tasks. Guards bound individual reads and lines; they do not
add a global analysis-memory quota or eliminate every concurrent parent-directory
replacement race. Those preexisting design limits are not expanded into P1/P2.

**Recommendation:** accept the validated P0 diff for review; P1 may be considered
only after that review and separate user direction. P1 has not started. A
push or merge still requires separate approval. The user has separately approved
one local commit with message `p0: harden source indexing, evaluation and verification`.
