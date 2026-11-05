# P2B experimental UB course admission review

Base/HEAD: `8d750d89f36ae0f8f1cd7aba00d1d87e13f471fe`.
Development branch: `integration/v2.1.1-p2b-experimental-ub`.
Worktree: `/Users/jayz/Documents/Projects/Ripple/ripple-p2b-experimental-ub`.
Changes are uncommitted and unstaged for independent review. The committed
hardening branch, detached audit worktree and original checkout are unchanged.

## Implementation and changed-file inventory

1. `src/ripple/pilot_experimental.py` (new): distinct mandatory-profile CLI,
   offline unapproved templates, exact per-task operator approvals, clean reviewed
   commit requirement, pinned BullsAI model/URL, Responses-only dispatch, staged
   review, private campaign identities/results, metadata consistency checks and
   safe failures. Reuses the existing captured OFF / identical-response OFF/ON
   replay harness and ordinary evidence/report validation.
2. `src/ripple/pilot_admission.py` (modified): optional strictly validated
   experimental provenance in task-start events, terminal evidence and inspector
   rows; immutable profile/campaign identity across tasks and restarts; full
   reservation floor for experimental settlement and reconstruction. Observed
   overruns remain visible. Strict settlement and the `Contract` implementation
   retain their existing behavior; strict journals cannot resume experiments.
3. `tests/test_p2b_experimental.py` (new): 86 offline regressions covering explicit
   profile/approval requirements, every identity and limit binding, unsafe/private
   file handling, clean-commit checks, complete synthetic capture/replay, durable
   accounting, aggregate reconstruction, strict separation, unexpected provider
   metadata, transport safeguards, failures, interruption and privacy boundaries.
4. `docs/P2B_EXPERIMENTAL_UB_COURSE.md` (new): operator procedure, fixed local
   assumptions/limits, budget consistency, explicit unverified guarantees,
   experimental interpretation and privacy requirements.
5. `docs/evidence/p2b-experimental-ub/verification.json` (new): complete sanitized
   test identities/results, tooling outcomes and preservation checks.
6. `docs/evidence/p2b-experimental-ub/report.md` (new): this review report.

The ZIP contains full contents of all six files, the complete tracked diff,
per-file hashes, final Git status, diff-check results and archive privacy evidence.
No approval authorizing a live task is shipped.

## Exact verification

- Full locked offline suite: **658 passed, 6 skipped**, exit 0 (106.60 seconds).
- Complete P2B harness: **279 passed**, exit 0 (30.14 seconds).
- New experimental coverage: **86 passed**. All original **572** passing identities
  and **193** P2B identities remain passing. The exact six existing optional
  Streamlit demo skips are unchanged; no additional skips.
- Ruff: **All checks passed**. Formatting: **119 files already formatted**.
- Dependency consistency: **No broken requirements found**.
- `git diff --check`: exit 0, empty output.
- Existing exact locked Python/dependencies and three hash-pinned offline fixtures
  reused read-only. No original virtual environment changes or installations.
- Original checkout: **963** tracked/untracked file hashes and status unchanged,
  including all four original untracked entries. Detached audit: **1,056** files
  and status unchanged. Committed hardening: **1,075** files and status unchanged.
- All protected heads/tags/remotes unchanged (ephemeral `refs/codex/*` excluded);
  only the requested new development branch was added at the base commit.
- **870 frozen evaluation files** match the historical checksum manifest, with no
  added evaluation files. Manifest SHA256:
  `7a48e94b70525803660b31791f8158e39549f1c3e3e93a35424ca6da4774337a`.
- Inherited P2A runtime/evaluation bytes unchanged. All **28** existing P2B package
  files, task definitions, fixtures, oracles and evaluation expectations unchanged.
- `pilot.py` is byte-for-byte unchanged. An AST regression confirms the entire
  strict `Contract` class is unchanged. Its shipped UNVERIFIED contract still
  rejects admission; experimental policy cannot load as a verified contract.
- No API calls, live tasks, additional calibration, project commits, pushes or
  merges. Full trajectory verification used an in-memory synthetic provider only.

## Remaining risks and decision

Provider context, tokenizer/framing, supported maximum output, reasoning cap
semantics, authoritative usage/billing, rate/concurrency limits and server-side
cancellation/retry guarantees remain **UNVERIFIED**. Two prior operator-reported
calibrations are observations and do not elevate this status. No overall account
quota does not remove these unknowns or the local campaign budget.

The 16,000-input / 2,000-output thresholds and 4,096 framing allowance are local
planning assumptions. They cannot prove a provider token/billing ceiling.
Experiments retain every full reservation (or a larger observed total), never
refund on reported low usage, and stop on any detected violation. A single
already-dispatched attempt can exceed an undocumented limit; its observations and
uncertainty are retained rather than clamped. Undetected provider-side behavior
cannot be certified by this harness.

Task allocations remain 110k / 130k / 110k, aggregate 350k, at most 24 attempts/task
and 72 aggregate. At maximum 18k/request, allocation limits allow at most 6 / 7 / 6
attempts respectively and 19 aggregate; rate/time checks may stop earlier. Budget
censorship is an explicit ineligible result, never an abstention or valid negative.
The single-request guarantee is enforced within the locked campaign; operators
must not launch parallel campaigns or discard private audit history.

Recommendation: **ready for independent offline review; no live authorization is
created by this change**. After review, a separately authorized commit is required
because live execution refuses dirty worktrees. Then regenerate and review an
exact approval for only `P2B-DEV-001-STOCK-LOW-WATERMARK`, its shared private output
directory and fixed profile/limits. That task must receive explicit operator
approval before execution. Review its captured trajectory, identical-response
OFF/ON replay, ordinary evidence validity, false positives, accounting and terminal
eligibility before considering any further task. Strict production admission
remains blocked pending a sufficiently evidenced provider contract.
