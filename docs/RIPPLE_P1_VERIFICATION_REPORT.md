# RIPPLE P1 — evidence protocol and reliability verification

Completed: 2026-11-01, America/New_York. Work began 2026-10-31.

**The local deterministic P1 candidate passes final review.**
This report records checks immediately before the separately authorized local
commit on `integration/v2.1.1-p1`; Git-state observations describe that point.
The full suite passes **342 tests with six unchanged optional skips**. All three
fresh P0/P1 B0–B2 comparisons complete with identical indexes and ranking outputs.
No inference calls, task commits, merges or pushes were made during implementation
and precommit review. The subsequent authorization allows one local commit only.
No P2 work began.
Provider request replay is implemented but remains **off by default** because a
BullsAI duplicate-billing/idempotency guarantee was not verified.

## Baseline and isolation

- Parent and precommit branch HEAD: `fef4c3d0958b64f4add2a5b71f8f9a9bffb9f108`.
- New branch: `integration/v2.1.1-p1`, in a separate linked worktree.
- Frozen tag remains `ripple-v2.1.1-frozen` at
  `f381d1c7c6f55e504cee3f9e6beb425d2387ec0d`.
- Required baseline before implementation: **285 passed, six skipped**, with
  Ruff and formatting passing. Both real Rich fixtures and SymPy sine.py ran.
- The original checkout and the clean P0 worktree were preserved. Existing
  untracked files in the original checkout were not moved or removed.
- Before staging, nothing is staged; the only new user ref is the authorized P1
  branch. The approved commit advances only that branch.

## Investigation: confirmed causes and limits

The P0 implementation selected the most recent observation containing a result,
without checking `ok`. A failed search after a successful inspect therefore made
the failed evidence ID the ID that `CandidateLedger.apply` required. A valid
update citing the successful inspect alone was refused. The original P0 code was
run in an isolated synthetic repository to reproduce this result before accepting
the fix. Both observations remain present in the new regression.

This is an **action-update-path defect**. V2 checkpoint eligibility already uses
`ledger.support`, which filters successful records, and is separate from that
latest-ID requirement. It is not evidence that every historical V2.1.1 abstention
had the same cause. Missing historical event traces still prevent that conclusion.

A further P0 reproduction confirms that an unoffered checkpoint keep decision
was refused yet still stored a missing-evidence note. P1 refuses that note write
and reruns the complete suite after the correction.

A second reproduction confirms that a repeated rejection returned `False` while
incrementing `rejection_count`. A zero-token-ceiling reproduction also confirms
that P0 still made its first interpretation request before checking that ceiling.
See [baseline reproductions](evidence/p1-integration/baseline_reproduction.json).

## Deterministic changes

| Area | Implemented behavior |
| --- | --- |
| Required evidence | Select the latest observed successful evidence that actually exists in the Python ledger. Failed and unknown observations cannot become required support; they remain recorded. |
| Evidence storage | Refuse duplicate IDs and mismatched result IDs before insertion. Freeze record fields and copy caller-owned data on insertion, preserving the original observation from later caller mutations. |
| Update and checkpoint guards | Refuse cited failed evidence. Preserve indexed-target, provenance, latest-ID and checkpoint-presented-ID checks. Action-phase confirm/reject remains forbidden in V2. |
| Structured outcomes | Add `LedgerOutcome` plus `apply_checked` and `decide_checked`. Existing `apply` boolean and `decide` tuple interfaces remain. `keep` remains an unchanged, non-refused checkpoint decision. |
| Atomic refusal | Validate before changing candidate state, history or rejection counters. Refused checkpoint keep decisions cannot write missing-evidence notes. Repeated rejection is refused without mutation. The reopening limit counts accepted rejection transitions. |
| Claim validation | Failed observations cannot support affected components, schema/API/config claims, risks or suggested tests, even if a forged record claims to be strong. |
| Checkpoint visibility | Add selected-count/stall/forced/bound diagnostics and deferred reason codes. Existing offered evidence, checkpoint instructions, selection policy and hard limits remain. |
| Request-boundary budgets | Check before interpretation, action, checkpoint, draft and repair requests. Zero budget blocks the first request. Missing usage stops the next request when a ceiling is configured. Input+output usage can enforce admission if total usage is missing, without rewriting existing serialized counters. |

The trusted low-level `seed` API retains its historical ability to create a
suspected placeholder for fixture builders. Known failed/untouched records are
refused there; unknown placeholders cannot support model updates, checkpoint
confirmation or validated claims. Production lexical seeding still requires a
successful tool result. A new regression checks this compatibility boundary.

Reason codes include `invalid_target`, `latest_evidence_required`,
`unknown_evidence`, `failed_evidence`, `untouched_target`, `invalid_transition`,
`candidate_cap`, `already_confirmed`, `already_rejected`, `rejection_limit`,
`not_suspected`, `invalid_decision`, `no_evidence`, `not_presented`,
`strong_evidence_required`, `target_not_offered` and `checkpoint_required`.
Legacy checkpoint refusal strings remain available alongside the new codes.

## API reliability and duplicate-billing investigation

The installed OpenAI-compatible SDK is version 3.41.0. Local SDK inspection showed
that generating an internal idempotency key does not itself establish a provider
deduplication contract; its default idempotency-header setting is unset. No verified
BullsAI billing/deduplication guarantee was available in the inspected repository,
SDK or primary-documentation search. This is an evidence gap, not proof that the
provider lacks such support.

HTTP guidance cautions against automatic replay of non-idempotent requests without
knowledge that replay is safe or the original request was unapplied. Accordingly,
P1 does not treat a 429/503 response or a request ID as proof of no charge.
[RFC 9110 §9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2).

The client now provides:

- SDK automatic retries still disabled with `max_retries=0`; existing 90-second
  request timeout retained. This prevents SDK retries multiplying wrapper retries.
  [Official SDK retry configuration](https://github.com/openai/openai-python#retries).
- Explicit `RetryPolicy(allow_request_replay=True)`, or the exact environment
  opt-in `RIPPLE_LLM_ALLOW_REQUEST_REPLAY=1`. Default transient replay is disabled,
  including the previously implicit Chat fallback retry loop.
- At most three transport attempts **shared across Responses and Chat fallback**
  for one logical request. Responses 404 fallback remains; it consumes that budget.
- Retry eligibility limited to explicit 429/503 responses when replay is opted in.
  Ambiguous connection/timeouts, authentication errors and server retry vetoes do
  not replay. A successful malformed response is not retried by transport logic.
- Numeric and HTTP-date `Retry-After`; invalid values use bounded backoff. If the
  requested delay exceeds the 59-second per-delay budget, stop instead of retrying
  earlier than requested. Sleep, clock and backoff are injectable for tests.
  [RFC 9110 §10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3).
- Per-request context isolates attempts and diagnostics when a client is shared
  across concurrent calls. No new idempotency header or billing guarantee is invented.
- Additive `provider_diagnostic` trace events for attempts, suppression, fallback
  and scheduled retries, including `duplicate_billing_risk` when replay is scheduled.
  No credentials, request bodies or provider headers are copied into these events.

All request-create operations in the tests are mocked. No actual retry setting
was enabled against BullsAI, and no paid provider operation was attempted.

## Prompts, serialization and hash inputs

AST comparisons verify that all prompt assignments are identical to P0, including
checkpoint and report-repair prompts. Repository/context builders, tool argument
shapes, strength classification and policy/budget constants remain unchanged.
The exact Responses and Chat `create` payload ASTs are identical, including system
instructions, JSON schemas and `store=False` on Responses.

`agent_models.py`, report schemas/version constants, and the `LLMResponse` dataclass
are unchanged. Diagnostics travel through a separate channel and additive trace
fields/events. No benchmark configuration/hash inputs or frozen artifacts changed.
New private budget accounting does not silently fill or reinterpret existing
serialized usage fields. [Preservation checks](evidence/p1-integration/preservation.json).

## Tests and recorded commands

| Check | Outcome |
| --- | --- |
| Required P0 baseline | 285 passed, six skipped; 62.99 seconds |
| Corrected targeted compatibility run | 141 passed; 25.30 seconds |
| Initial P1 regression subset | 55 passed; 17.63 seconds |
| Final full suite, including concurrent retry and refused-note regressions | **342 passed, six skipped; 80.94 seconds** |
| Final Ruff | **Passed** |
| Final formatting | **Passed; 88 files checked** |

All 291 baseline test identities retain their statuses; 57 additional cases pass.
The six skips remain the optional Streamlit UI tests. One existing test was
intentionally revised: two rejections now mean two accepted rejection transitions,
not one accepted rejection plus a refused call that mutates state.

The first targeted run exposed two old fixture builders using placeholder seed
IDs; compatibility was restored without allowing those IDs to support proposals.
Its separate Git replay failure was the known sandbox-denied hard link; the
permitted rerun passed. Two import-order lint findings were corrected. Final
validation has no unresolved failures, skipped regressions or suppressed checks.

The baseline and final suite used Python 3.13.16, disabled pytest plugin autoload,
a socket-blocking sitecustomize guard, stripped API credential variables,
`PYTHONDONTWRITEBYTECODE=1`, disabled Git SSH and fresh output directories.
The existing recorded Git replay test required local hard-link execution permission.
The real P0 corpus fixture environment variables were set for both full runs.

```sh
python -m pytest -q -p no:cacheprovider --basetemp FRESH_TEST_TEMP --junitxml RESULTS_XML
ruff check --no-cache .
ruff format --check --no-cache .
```

The socket guard and actual interpreter/import paths were supplied by the isolated
audit runner; the placeholders above document flags without private absolute paths.
[Complete test metadata](evidence/p1-integration/test_results.json).

## Completed B0–B2 comparison

Fresh P0 and P1 imports used the same clean task pin, prepared request and 501-level
history. Complete normalized indexes and every B0/B1/B2 source/test prediction
object, score and order match. Normalization excludes only schema_version,
created_at and repo_root; all other fields are compared.

| Corpus | Budget per implementation | P0 / P1 elapsed | Result |
| --- | ---: | ---: | --- |
| SymPy-14928 | 900 seconds | 385.421 / 389.629 seconds | Complete and identical |
| Rich-2301 | 300 seconds | 6.618 / 6.665 seconds | Complete and identical |
| Rich-901 | 300 seconds | 5.821 / 5.893 seconds | Complete and identical |

No timeout is accepted as equivalence. Full replay covers these three pins, not
all 85 historical tasks. All indexing, search, B0–B2 algorithms and the previous
85-task source-audit evidence remain byte-identical to P0. The 2 MiB / 32,768 limits
and source guards are unchanged. [Comparison records and hashes](evidence/p1-integration/ranking_comparisons.json).

## Every changed or new file

| Path | Change |
| --- | --- |
| `src/ripple/agent.py` | Latest successful evidence, structured rejection traces, checkpoint visibility and request admission. |
| `src/ripple/ledger.py` | Append-only insertion, structured outcomes and atomic rejection handling. |
| `src/ripple/llm.py` | Opt-in bounded transport replay, Retry-After, injectable backoff and isolated diagnostics. |
| `src/ripple/validate.py` | Successful-evidence requirement for validated claims. |
| `tests/test_validation.py` | Correct rejection-count regression to require accepted transitions. |
| `tests/test_p1_evidence_protocol.py` | New provenance, atomic rejection and checkpoint regressions. |
| `tests/test_p1_provider_reliability.py` | New mocked transport, bounds, telemetry and concurrency tests. |
| `tests/test_p1_token_boundaries.py` | New zero/spent/unknown-usage and repair-boundary tests. |
| `docs/RIPPLE_P1_VERIFICATION_REPORT.md` | This report. |
| `docs/evidence/p1-integration/baseline_reproduction.json` | Confirmed P0 defect reproductions. |
| `docs/evidence/p1-integration/implementation_inventory.json` | Implementation/test hashes. |
| `docs/evidence/p1-integration/preservation.json` | Prompt, payload, schema, artifact and Git-state checks. |
| `docs/evidence/p1-integration/ranking_comparisons.json` | Completed comparison results and output hashes. |
| `docs/evidence/p1-integration/test_results.json` | Baseline/final checks, new tests and unchanged skips. |
| `docs/evidence/p1-integration/verification.json` | Final scope, safety decisions and privacy result. |
| `docs/evidence/p1-integration/SHA256SUMS` | Hashes for the other 15 changed/new files; excludes its own circular hash. |

## Preservation, remaining risks and recommendation

All 870 frozen evaluation files and 8,758 recorded original checkout file contents
match their P1-start hashes. Original/P0 checkout status and indexes are unchanged.
Main, frozen tags, P0 and other existing user refs remain at their original SHAs.
Internal Codex capture refs may rotate; no task command wrote or restored them.
The recorded precommit P1 branch remains at its P0 parent. The separately approved
local commit advances that branch; no protected ref is changed.

Remaining limits:

- Retry support is tested, but default live replay remains disabled pending a
  provider contract or explicit informed replay-risk decision. Opt-in replay can
  duplicate inference/billing; failures do not report authoritative token cost.
- The retained Responses-404 endpoint fallback has no newly claimed at-most-once
  guarantee. No live provider reliability or cost improvement is measured.
- Token checks are controller request-boundary checks, not preflight tokenization
  or a hard maximum on a single response. The first response can exceed the limit.
  Failed replay costs and concurrent runs are not a global token budget. Unknown
  usage stops subsequent controller calls when a ceiling is configured.
- Existing successful-empty-result strength rules and action/checkpoint distinctions
  remain; P1 does not replace model policy. Unchanged prompts can still lead a model
  to cite failed evidence, which is now refused with an explicit code.
- Low-level trusted seed placeholders are retained for compatibility but cannot be
  promoted or published without real successful provenance. Public Python objects
  are not a sandbox against trusted code directly mutating ledger containers.
- Historical missing traces still prevent causal reconstruction of all held-out
  failures. Exact ranking replay is scoped to three pins; UI/platform coverage is
  limited as recorded above.
- Preserved report versions/hash inputs do not alone identify P1 transport settings;
  Git provenance, source hashes and additive trace diagnostics must accompany review.

**Recommendation:** review the validated deterministic P1 diff. Keep automatic
provider replay disabled by default until billing/deduplication evidence supports
enabling it. The user separately authorized one local commit with message
`p1: harden evidence handling, retries and diagnostics`. No push, merge or P2
work is authorized or performed.

## Final review before the authorized local commit

All 16 changed/new paths were reviewed for P1 scope. The complete suite was
rerun after authorization: **342 passed, six unchanged optional skips,
67.80 seconds**; Ruff and formatting passed. All prior test identities/statuses
match. Runtime code and regression-test hashes still match the verified P1 diff.

The previously completed P0/P1 raw comparison outputs were re-read and their
complete B0/B1/B2 hashes rechecked for all three pins. Ranking/index implementation
files remain byte-identical to P0; no new replay or all-85 equivalence is claimed.
Frozen evaluation hashes, original/P0 checkout state, and preexisting user refs
were reverified. Transient retry replay remains opt-in and off by default.

Exact staged file contents and the complete staged diff receive a final credential
and privacy scan before committing. Private evidence packages, raw provider data,
benchmark datasets and unrelated local files are excluded. Precommit Git-state
metadata intentionally does not contain the future commit's own SHA.
