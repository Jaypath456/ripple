# RIPPLE architecture-review package

Branch: `review/ripple-accuracy`. Baseline:
`358dbff3f63b1ed3b1048d5c032f66702cee0f6f`.

This package commits the existing reliability/security implementation, its tests,
and engineering evidence for review. Review preparation changes ignore rules and
documentation only; implementation and test contents are unchanged from the
start of preparation. No inference requests or push are part of preparation.

## Reading order and review focus

1. [README](../README.md): supported workflows, limits, setup and historical results.
2. [Delivery record](RIPPLE_DELIVERY.md): original defects, matched regressions,
   bounded-source protections, evidence validation, provider handling and limitations.
3. [Prediction-quality review](PREDICTION_QUALITY_REVIEW.md): every final abstention,
   diagnosis limits, offline opportunities and a proposed cost-capped experiment.
4. [Controller](../src/ripple/agent.py), [ledger](../src/ripple/ledger.py),
   [validator](../src/ripple/validate.py), [source reads](../src/ripple/source.py),
   [provider boundary](../src/ripple/llm.py) and
   [diff verification](../src/ripple/verification.py).
5. [Security tests](../tests/test_delivery_security.py),
   [resilience tests](../tests/test_delivery_resilience.py), and the existing suite.

Stage A scans a Git checkout, indexes Python, interprets a feature request, gathers
static tool observations, advances a Python-owned candidate ledger, and validates
a report. Stage B compares a Git range to a saved report and applies deterministic
classifications; optional model explanations cannot change those classifications.

The most useful review questions are whether evidence provenance and target
support survive every state transition, whether repository text can cross tool
or filesystem boundaries, whether partial/failure statuses are honest, and how
the controller can advance candidates without weakening validation. Static
provenance does not certify natural-language entailment. Parent-directory races,
aggregate repository memory, dynamic Python behavior and live prompt-injection
behavior remain outside the demonstrated guarantees.

Historical file F1 remains **0.015873**; the current controller has no measured
live benchmark improvement. All 105 historical RIPPLE runs generated candidates,
but 102 abstained. The scripted demo demonstrates pipeline execution with supplied
proposals. These limitations are preserved in the review, not hidden by passing
unit tests or by changing labels.

## Verification performed during preparation

- Existing suite: **219 passed in 45.21 seconds**.
- Ruff lint: passed. Ruff format check: **72 files already formatted**.
- Dependency consistency: passed; pip emitted only a local cache-permission warning.
- Whitespace, explicit staging-list equality, ignored-secret paths, staged-content
  secret/privacy scan and source/artifact preservation are required before commit.
- No paid API calls. The proposed live pilot remains unapproved and unexecuted.

The content scan uses credential signatures, private-key markers, credential
assignments, authenticated URLs, JWTs, high-entropy strings, local paths and email
locations, plus exact matching against locally available credential values without
printing those values. The inherited benchmark archive is inspected in memory.
No dedicated secret-scanner executable is installed; this is a documented local
scan and manual review, not a claim of exhaustive secret detection or a remote
GitHub security scan.

Reviewed matches are explicit test/example placeholders, synthetic `example.test`
identities, public benchmark run IDs/hashes, or metadata inherited from public PRs.
No live credential or private source trace was identified. The existing frozen
benchmark artifacts are preserved byte-for-byte. They retain public PR-author
emails, public upstream author strings, and legacy `/home/<user>/...` report-path
metadata in three MVP result files. Those inherited paths do not resolve to files
in this package; they are not newly collected private traces. This package does
not rewrite existing benchmark provenance or Git history.

## Included and excluded evidence

Included evidence is the abstention audit, frozen-regeneration hashes, environment
versions, short test results, sanitized installation/stress/final-check records,
a baseline-failure summary, and the invoice demo's synthetic reports/trace. The
demo trace contains only generated fixture code and placeholder workspace paths.
Existing frozen benchmark inputs, gold, summaries and archived traces remain
tracked and unchanged; those inherited artifacts were also scanned.

The five originals below remain on disk and are ignored. Four have sanitized
publication copies; the verbose baseline traceback has a concise result summary.

| Local-only original | Published evidence |
| --- | --- |
| `docs/evidence/final_checks.json` | [Sanitized checks](evidence/sanitized/final_checks.json) |
| `docs/evidence/fresh_install.json` | [Sanitized install record](evidence/sanitized/fresh_install.json) |
| `docs/evidence/security_baseline.txt` | [Baseline failure summary](evidence/sanitized/security_baseline.json) |
| `docs/evidence/stress_baseline.json` | [Sanitized baseline stress](evidence/sanitized/stress_baseline.json) |
| `docs/evidence/stress_current.json` | [Sanitized current stress](evidence/sanitized/stress_current.json) |

`.env` and its local variants, `.venv/`, `.ripple/`, caches, build output and the
five local evidence originals are excluded. `.env.example` contains a placeholder
API-key value and public endpoint/model identifiers. No local credential file,
virtual environment, wheelhouse or evaluation workspace is added.

## Exact publication inventory

[REVIEW_PUBLICATION_FILES.txt](REVIEW_PUBLICATION_FILES.txt) lists every file in
the proposed branch tree, one repository-relative path per line, including the
unchanged files inherited from the baseline. A Git push also transfers any
required ancestor history; the file list describes the current tree, not a
history rewrite. The files added or modified by this review commit are listed
below. All other entries in the full inventory are unchanged baseline files.

**51 files** added or modified; **956 files** in the branch tree.

```text
.env.example
.github/workflows/ci.yml
.gitignore
README.md
docs/ARCHITECTURE_REVIEW.md
docs/PREDICTION_QUALITY_REVIEW.md
docs/REVIEW_PUBLICATION_FILES.txt
docs/RIPPLE_DELIVERY.md
docs/evidence/abstentions.json
docs/evidence/changes.json
docs/evidence/demo/anomaly.json
docs/evidence/demo/control.json
docs/evidence/demo/negative_report.json
docs/evidence/demo/report.json
docs/evidence/demo/report.md
docs/evidence/demo/summary.json
docs/evidence/demo/trace.jsonl
docs/evidence/environment.json
docs/evidence/frozen_regeneration.json
docs/evidence/sanitized/final_checks.json
docs/evidence/sanitized/fresh_install.json
docs/evidence/sanitized/security_baseline.json
docs/evidence/sanitized/stress_baseline.json
docs/evidence/sanitized/stress_current.json
docs/evidence/security_current.txt
docs/evidence/tests_current.txt
evaluation/check_fresh_install.py
evaluation/diagnose_abstentions.py
evaluation/stress_index.py
src/ripple/agent.py
src/ripple/agent_models.py
src/ripple/cache.py
src/ripple/cli.py
src/ripple/demo.py
src/ripple/diffing.py
src/ripple/evaluation.py
src/ripple/facts.py
src/ripple/ledger.py
src/ripple/llm.py
src/ripple/models.py
src/ripple/scanner.py
src/ripple/search.py
src/ripple/source.py
src/ripple/tools.py
src/ripple/validate.py
src/ripple/verification.py
tests/test_delivery_resilience.py
tests/test_delivery_security.py
tests/test_phase5_baselines_show_e2e.py
tests/test_phase5_expand_render.py
tests/test_references.py
```
