# RIPPLE P2B final correctness review

2026-11-05, America/New_York. Worktree: `/Users/jayz/Documents/Projects/Ripple/ripple-p2b-hardening`.
Branch: `integration/v2.1.1-p2b-hardening`; HEAD/base:
`cf4e6b29f98dccf8c2280e49be803e431c11c56d`. All changes remain unstaged and uncommitted.
This report supersedes completion/conflict/schema claims in the prior hardening report;
that report and its evidence are retained byte-for-byte as historical review material.

## Implementation

This pass changes `src/ripple/pilot_admission.py`, `dev/p2b/check_offline.py`, and one
setup event in `tests/test_p2b_pilot.py`; adds `tests/test_p2b_final_correctness.py`;
and adds this report plus `docs/evidence/p2b-final-correctness/{initial-state,
preservation,regression_results,test_results,verification}.json`.
The complete cumulative inventory against the base is included in the ZIP.
All 15 preexisting uncommitted files and the original tracked diff were backed up
under `.ripple/final-correctness` before implementation. The other 12 existing
review files remain byte-identical. No prior hardening assertions were weakened:
the old torn-journal test's unsupported `fixture` marker was replaced by a valid
`task_started` setup event while retaining the test identity and corruption assertions.

- **Completion evidence:** `finish()` writes an immutable `COMPLETION_PREPARED`
  summary with `evaluation_eligible=false`, fsyncs it and its directory, then appends
  a `task_finished` record binding its SHA256 and protocol version. This is still
  the existing journal/artifact architecture. Eligibility is computed only after
  verifying the matching commit and exact artifact; it is never inferred from a
  status string. The order avoids circular evidence generation. Missing, corrupt,
  mismatched or unsafe artifacts fail closed; committed artifacts are checked,
  never silently regenerated. `stop()` retains explicit/last task identity even
  when `current` is None. Independent immutable failure markers cover failures
  to write a journal failure event. Existing terminal artifacts are never overwritten.
- **Conflicting usage:** reconstruction retains distinct component observations and
  their maximum reported totals. A lower later observation or settlement cannot
  clear conflict/uncertainty or lower conservative accounting. Inconsistent triples
  and missing usage do not become authoritative corrections. Consistent observations
  still settle normally. Conflicted tasks remain ineligible after interruption.
- **Journal validation:** required and allowed fields, types (including bool versus
  int), finite/bounded numbers, counters, accounting relationships and event order
  are checked for every recognized event. Unknown events and impossible transitions
  are rejected. Reader and writer checks share the same rules. Inspection reports
  structured CORRUPT with fixed reason codes, sequence and recognized event type
  where available; it never echoes arbitrary event contents. Journal and artifact
  readers consume exactly the bounded stat size and reject premature EOF.
- **Fixtures:** before pytest starts, the runner reads the unchanged
  `docs/evidence/p2b-hardening/fixture_sources.json` and verifies each of its three
  fixtures' SHA256. Missing/modified files abort with a concise fixed message.
  It neither downloads fixtures nor regenerates expected hashes or substitutes data.

Production prompts, model behavior, evidence/report schemas, validators, evaluation
artifacts and protected checkouts are unchanged. No dependency was installed or
upgraded, and no network/model-provider request or live task command was performed.
Tests exercised scripted/in-memory transports and disposable synthetic Git fixtures.

## Verification

- Full suite: **572 passed, 0 failed, 6 unchanged optional UI skips**.
- Complete P2B harness: **193 passed, 0 failed, 0 skipped**.
- All 485 prior passing identities/statuses are preserved, including the original
  443 passes and all 64 original P2B harness identities. There are 87 new correctness
  cases; the prior 42 hardening regressions remain intact.
- Ruff, full formatting checks, dependency consistency and `git diff --check`: pass.
- All 870 frozen evaluation files match their initial SHA256 values and the verified
  frozen manifest; the evaluation file set has no additions or removals.
- All 1,018 inherited P2A files match their Git blobs. Protected/user refs, both
  protected checkout HEADs/statuses/files, and the original index are unchanged.
- No configured credential files were opened. Review privacy checks use fixed
  credential/private-key patterns, structured-evidence checks and strict file
  allowlisting; they do not claim exhaustive secret detection.

## Regression evidence

Before implementation changes, the new primary reproductions produced **40 failures
and 17 passing controls** against the preserved implementation. They reproduced the
summary eligibility bug, 400001-to-150 accounting loss, malformed hash-valid journals
(including uncaught KeyError/TypeError), and fixture mutation reaching pytest.
Two additional short-read cases were then demonstrated failing against the unchanged
reader before that reader was fixed. All 87 final correctness cases now pass.
`regression_results.json` maps every issue to exact test identities and final outcomes.

## Compatibility and limitations

Valid legacy journals can remain eligible only when their original owner-only,
canonical terminal summary matches every legacy accounting field. A legacy
`task_finished` record without that evidence is intentionally no longer sufficient.
New protocol-2 summaries are preparation records: only the journal inspector's
verified combined view determines eligibility. Event-only reconstruction cannot
verify filesystem evidence and therefore never independently affirms eligibility.
The same reducer with the same artifact root is used by live reconstruction and
inspection. Unknown future event/schema versions are rejected until reviewed.

SIGKILL, storage loss or disk failure may prevent new failure records from being
written. Inspection can only assess evidence that survived; it does not repair,
resume or guess missing data. A valid recovered commit requires its matching durable
artifact. These checks do not defend against an owner deliberately rewriting an
entire hash chain and all artifacts. Raw provider bodies remain private run artifacts
and are not included in this review package.

The locally reproduced completion, conflicting-usage, malformed-journal and fixture
issues are fixed and covered by offline tests. BullsAI context, tokenizer/framing,
reasoning-inclusive output control, authoritative usage/billing, rate limits and
cancellation/provider retry guarantees remain unverified. The provider contract is
still UNVERIFIED. **Live P2B admission remains NO-GO.**

## Review package

The privacy-scanned ZIP includes the complete tracked diff against the base, every
new/uncommitted review file, current inventory, initial/final Git state, regression
and verification results, and preservation evidence. It excludes ignored backups,
credentials, .env files, environments, fixture caches, raw responses/prompts, private
traces and unrelated files. Its outer checksum is supplied outside the ZIP to avoid
self-reference. Packaging does not modify repository content. Review the diff and
regression evidence independently before any commit or provider-verification step.
