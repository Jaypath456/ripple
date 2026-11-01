# RIPPLE P2A — offline report retention and accuracy analysis

Date: 2026-11-01, America/New_York.

**The default-off P2A candidate is implemented and locally validated.**
It retains missing, independently supported V2 checkpoint confirmations after
report validation, or explains why retention was refused. In three constructed
loss cases, one eligible component survived where P1 emitted none, without extra
model/tool calls. **Historical accuracy improvement has not been measured.**
No BullsAI or other inference calls, pushes or merges were made. This report records
verification immediately before the separately authorized single local P2A commit;
the commit SHA is reported externally to avoid a self-referential hash.

Baseline: `3ddf26245b5c932dfbfa22ea806fa8eac7f3a55e`.
Branch: `integration/v2.1.1-p2`, created in a separate linked worktree from the
baseline. The authorized commit contains only the 12 paths listed below. Original
and P1 checkouts, protected refs and frozen benchmark artifacts are preserved.

## What the actual implementation showed

P1 already has a deterministic `_fallback_draft`. It is used for malformed drafts,
non-submission and controller failures. The uncovered gap is narrower: a
**parseable** report can omit confirmed targets, classify them incorrectly, or
cite unusable evidence. Normal validation can then leave zero components while
the ledger still contains confirmations. The existing malformed-draft fallback
does not fill that post-validation coverage gap.

P2A preserves the existing draft, repair, checkpoint and validation paths. It adds
an optional final retention pass after normal validation. It does not assume the
historical counters contain enough evidence to reconstruct every loss.

## Investigation of the 12 confirmed/report-lost runs

The previously verified sanitized records contain **12 runs across nine tasks and
nine repositories**, all `abstained/submitted`, with positive confirmed counts and
zero reported source files. The distribution is six runs with one confirmation,
two with two, three with three, and one with six: **25 confirmation occurrences**,
not 25 distinct files. Five runs recorded one report repair; seven recorded none.

Only **eight raw checkpoints** are available; all eight hashes were verified.
They show mixed symptoms:

| Available checkpoint observation | Runs | Recorded component-drop messages |
| --- | ---: | --- |
| Existing-target/new-file classification symptoms | 3 | Five invalid migration/new-file drops total |
| Unconfirmed model-proposed components | 1 | Three unconfirmed-component drops |
| Unsupported/duplicate component symptoms | 1 | One unsupported and one duplicate-component drop |
| No affected-component-drop message in saved warnings | 3 | Other config/risk/narrative warnings only |

These patterns are consistent with multiple loss mechanisms, including omissions,
classification errors and citation failures. Warning counters cannot identify the
exact missing target or prove the cause for a particular run. Duplicate-component
ordering is an investigation hypothesis, not a separately implemented change.

All 12 records lack replayable agent JSONL/report context, confirmed target IDs,
ledger histories, presented evidence and raw drafts. Four also lack individual raw
checkpoints. **Zero historical counterfactual replays were possible.** No recovery
rate, source-file precision/recall/F1 gain, or success on all 12 is claimed.
[Per-run availability and observed counters](evidence/p2a-integration/saved_evidence_analysis.json).

## Proposed and implemented mechanism

Enable explicitly with `RIPPLE_RETAIN_CONFIRMED_REPORT=1`, or the boolean
`AgentController(..., retain_confirmed_report=True)`. Other environment values
leave it off. Truthy constructor strings are refused. The pass is inactive for
historical V1 and validator-disabled variants, even when the flag is supplied.

For every confirmed target absent from the validated report, the pass requires:

1. A repository-relative, indexed source file or real indexed symbol; no test file,
   path escape, missing target or parse-error file.
2. An accepted V2 checkpoint confirmation in candidate history, with nonempty IDs
   retained in the candidate's citations and actually presented for that target.
3. Existing evidence records with matching record/result IDs, successful results,
   and target-touch provenance for every confirmation citation.
4. Strong non-lexical evidence under the existing tool rules; reference evidence
   needs a high-confidence reference. Failed, lexical-only, unknown-tool or
   malformed support cannot satisfy this condition.
5. Completed reference/test flags **and** recorded successful reference/test tool
   observations for the target's file.

The pass constructs an **independent low-confidence component** using only those
accepted checkpoint citations. It uses the existing deterministic fallback's
conservative `modify` / `business_logic` defaults, rather than copying an invalid
model claim or inventing a specific API/schema change. It then submits that
proposal to the **unchanged ordinary validator**. Validator refusal remains final.

Existing valid components, order, citations, deterministic expansion and original
validator-drop messages remain intact. No new target is discovered or confirmed.
No tool, model request, prompt change, ranking heuristic or budget increase is
introduced. Ledger/evidence/history objects are not modified. Repeated retention
does not duplicate components or refusal notices.

Successful recovery adds an observed-condition blind spot and a
`report_retention_evaluated` trace event. The report becomes `partial`; a prior
`failed` status is preserved. Original stop reasons remain. Ineligible absent
confirmations remain excluded, with a reason-coded trace entry and a visible
`Confirmed report retention refused [...]` dropped-claim note. Thus the guarantee
is **retain eligible confirmations or explain refusal**, not publish every positive
counter regardless of provenance.

The trace identifies active policy `confirmed-checkpoint-v1`. Public report schema
and version fields are unchanged; pilot outputs must retain flag/source metadata
and remain separate from frozen benchmark series.

## The 25 zero-confirmation runs

The cohort spans **25 runs, 15 tasks and ten repositories**. All have zero
confirmations and no reported source predictions. Twenty-one stopped on
`no_ledger_progress`; four on `no_progress`. Nine raw checkpoints are available
and hash-verified. Their saved drops only show the unsupported-blind-spot warning;
none supplies the missing investigation-to-confirmation event chain.

The summaries do not establish whether discovery missed useful files, evidence
was weak, tool targets were poor, or model checkpoint decisions stalled despite
sufficient support. Those are distinct hypotheses requiring captured evidence.
The new retention pass **cannot help a run with no confirmations**. The paired
zero-confirmation control remains abstained with identical prompts and call counts.

## Multi-file opportunities — analysis only

Six of the 12 loss runs have at least two confirmations. They may refer to multiple
symbols in one file; distinct-file coverage cannot be inferred without targets.
Retaining two independently confirmed files is covered by a deterministic test,
including the case where a valid draft already covers one of them.

Future investigations could measure candidate recall across files, structural
caller/dependency coverage, and why offered source candidates remain unresolved.
They require new trajectories and predeclared evaluation criteria. **No speculative
multi-file discovery, propagation, score adjustment, checkpoint calibration,
prompt experiment or expansion policy is implemented.**

## Offline measurements and regression results

The experiment imports the actual P1 source separately and compares it with P2A
disabled/enabled, using the existing saved-protocol reproduction helpers on one
shared synthetic CLI fixture. This is not a reconstruction of the 37 historical
trajectories. Normalization removes only report ID and runtime for report equality.

| Scripted case | P1 / flag-off components | Flag-on components | Flag-on status | Model calls off/on |
| --- | ---: | ---: | --- | ---: |
| Omitted confirmed component | 0 | 1 | partial | 7 / 7 |
| Misclassification survives one repair | 0 | 1 | partial | 8 / 8 |
| Model cites fabricated evidence | 0 | 1 | partial | 7 / 7 |
| Valid model report | 1 | 1 | completed | 7 / 7 |
| Zero confirmations | 0 | 0 | abstained | 9 / 9 |

P1 and flag-off outputs match exactly across all five cases. Every model prompt,
ledger confirmation and original validator-drop message matches in the paired
runs. Tool calls are four in every case. The fabricated citation stays rejected;
the retained component cites separate accepted checkpoint evidence instead.
Valid and zero-confirmation normalized report hashes also match with the flag on.

Three empty reports were prevented in these constructed loss cases. This measures
**retention behavior**, not target correctness or held-out accuracy. Three existing
V1 saved demo-result fixtures were inventoried and hash-checked as historical
reference material, not converted into V2 checkpoint histories or rescored.
[Experiment records and hashes](evidence/p2a-integration/offline_comparison.json).

| Check | Result |
| --- | --- |
| Exact P1 baseline suite | 342 passed, six skipped; 64.44 seconds |
| Final precommit P2A full suite | **379 passed, six skipped; 81.83 seconds** |
| New regression cases | **37 passed** |
| Ruff / formatting | **Passed / passed** |
| Original test identities/statuses | All 348 preserved |

Tests cover omissions, invalid final drafts, fabricated citations, idempotence,
multiple already-confirmed files, absent/malformed provenance, failed observations,
source/test/path/parse-error guards, incomplete or forged check flags, unchanged
validator refusal, visible refusal notices, explicit flag parsing and V1/ablation
controls. The six unchanged skips require optional Streamlit UI testing support.

The first combined targeted run encountered the known sandbox-denied local Git
hard link in the legacy replay test; its new retention cases passed. The complete
runs used the permitted local hard-link execution and passed. Test interpreters
blocked sockets and removed API credentials. All provider operations were scripted
or mocked. The actual SymPy and Rich P0 fixture tests ran in both full suites.

Recorded command flags, with machine-specific paths replaced:

```sh
python -m pytest -q -p no:cacheprovider --basetemp FRESH_TEST_TEMP --junitxml RESULTS_XML
ruff check --no-cache .
ruff format --check --no-cache .
```

The runner supplied the socket guard, isolated source imports, disabled pytest
plugin autoload, disabled Git SSH, fresh outputs and existing Python 3.13.16.
[Complete test metadata](evidence/p2a-integration/test_results.json).

## First controlled BullsAI pilot — estimate only

Among 90 selected historical V2.1.1 slots, **38** have available raw checkpoints
with recorded token totals. Fifty-two lack those samples. The available values
range from 20,154 to 112,101 tokens; median **54,282**, mean **57,500**, and
nearest-rank 90th percentile **97,188**. This incomplete, selected sample is a
planning reference, not a representative confidence bound.

Propose **three fresh development requests**, outside the inspected held-out
cohorts: one focused change, one multi-file diagnostic opportunity and one negative
control. Predeclare expected source surfaces independently. Run one approved
trajectory per request, then finalize the identical captured state twice locally,
flag off/on. The fallback adds no model requests; expected incremental fallback
model tokens are zero. Do not pay for duplicate trajectories merely to compare
this pure finalization pass.

| Planning item | Tokens |
| --- | ---: |
| Three trajectories at observed mean | about **172,500** (round to **175k expected**) |
| Proposed per-run request-admission ceiling | **100k soft ceiling** |
| Three-run allocation plus contingency | **350k planning reserve** |
| Six independent trajectories, if later required | approximately **700k reserve** |

The 350k reserve comprises three 100k soft allocations plus 50k contingency.
Request-boundary checks cannot cap a single response or unknown failed-request
billing; this is **not a hard spend/token guarantee**. Estimates assume comparable
agent/model behavior to the historical sample. A model/provider change requires
re-estimation. The three pilot cases would be development evidence, not a new
general-accuracy benchmark.

Before any separately approved pilot, capture immutable index/source identity,
candidate histories, full evidence records, presented IDs, model draft and both
validated outputs in a fresh private pilot directory. Keep transcripts/credentials
out of exported reports. Record provider attempt and usage diagnostics, compare
retention/citation validity/statuses, and measure accuracy only against independently
defined fresh-case labels. Keep transient request replay disabled. Stop on missing
provenance, unknown usage, unsupported output or budget-admission refusal; review
costs between trajectories. **No pilot is executed or authorized by this estimate.**
[Observed tokens and budget assumptions](evidence/p2a-integration/pilot_budget.json).

## Changed-file inventory and preservation

| Path | Purpose |
| --- | --- |
| `src/ripple/agent.py` | Default-off flag, V2/validator gate, finalization call and additive diagnostics. |
| `src/ripple/report_fallback.py` | New pure retention pass with independent provenance checks and unchanged-validator enforcement. |
| `tests/test_p2a_report_retention.py` | New offline, integration, negative-control and safety regressions. |
| `docs/RIPPLE_P2A_IMPLEMENTATION_REPORT.md` | This implementation/analysis/pilot report. |
| `docs/evidence/p2a-integration/saved_evidence_analysis.json` | Safe projections and availability analysis of the 12/25 cohorts. |
| `docs/evidence/p2a-integration/offline_comparison.json` | P1/off/on experiment results and hashes. |
| `docs/evidence/p2a-integration/pilot_budget.json` | Token observations and explicit planning assumptions. |
| `docs/evidence/p2a-integration/test_results.json` | Baseline/final results, new cases and unchanged skips. |
| `docs/evidence/p2a-integration/preservation.json` | Frozen, prompt, source and Git-state checks. |
| `docs/evidence/p2a-integration/implementation_inventory.json` | Implementation/test SHA256 values. |
| `docs/evidence/p2a-integration/verification.json` | Final status, scope, limits and privacy scan. |
| `docs/evidence/p2a-integration/SHA256SUMS` | Hashes of the other changed/new files, excluding its own circular hash. |

All prior tracked files except `agent.py` remain byte-identical to P1, including
the validator, ledger, public models, provider, source/index guards and B0–B2 code.
All model prompts and existing policy/budget constants are unchanged. The **870
frozen evaluation files** and **8,758 recorded original file contents** verify
unchanged, along with original/P1 checkout state and all preexisting user refs.
The inherited three complete B0–B2 output records were hash-reverified; P2A did not
rerun those comparisons or claim a new all-85 ranking/corpus replay.

No raw requests, gold labels, provider payloads, credentials or private evidence
packages are added to the report evidence. Internal Codex capture refs may rotate;
no task command wrote or restored them. Exact staged blobs and the complete staged
diff must match the reviewed inventory, pass privacy scanning and verify these
checksums immediately before the authorized local commit.
[Preservation evidence](evidence/p2a-integration/preservation.json).

## Remaining uncertainties and recommendation

- The 12 historical losses cannot be replayed or scored without their missing
  ledgers/drafts. Strict eligibility may refuse some positive confirmation counters.
- Confirmed means a provenance-backed plausible target, not proven future change.
  Retention can expose false-positive confirmations and reduce precision. Low
  confidence, partial status and explicit policy metadata are intentional.
- Generic `modify/business_logic` fallback labels are inherited conservative defaults,
  not independently inferred semantic classifications. Manual review is required.
- Zero-confirmation discovery/decision failures remain unresolved. No speculative
  fix is implemented for the 25 runs or for multi-file discovery.
- Index/evidence snapshot freshness retains the existing trust boundary; public
  Python objects are not a sandbox against trusted code directly mutating memory.
- Report versions remain unchanged; keep pilot flag/source metadata and outputs
  separate from frozen benchmark series. UI coverage and platform validation are
  limited as recorded above.
- No live provider, latency, token-cost, precision or recall improvement is observed.
  The pilot budget is uncertain, and duplicate-billing guarantees remain unverified.

**Recommendation:** review the default-off deterministic retention candidate and
its offline evidence. A small separately approved, fully captured development pilot
can test real trajectories; the current evidence supports no broader accuracy claim.
