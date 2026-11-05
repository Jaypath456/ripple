# P2B offline hardening review

2026-11-04, America/New_York. Branch `integration/v2.1.1-p2b-hardening`;
base/HEAD `cf4e6b29f98dccf8c2280e49be803e431c11c56d`. Changes are uncommitted.
No BullsAI requests, live inference, calibration, development-task execution,
pushes or merges were performed. Regression tests use synthetic fixture repositories
and in-memory transports; fixture commits are confined to disposable test directories.

## Result

The complete suite passes **485 tests with the same six optional Streamlit skips**:
443 existing passes plus 42 new hardening regressions. The separate P2B harness run
passes **106 tests**, including all 64 existing harness identities. Ruff and format
checks pass across 113 Python files. `pip check` reports no broken requirements.
See `evidence/p2b-hardening/test_results.json` for individual identities/statuses.

## Accounting and terminal evidence

Reservations remain durable before dispatch. Safe provider metadata is recorded
before body-size rejection or body capture, so valid observed usage survives those
failures. Complete, consistent, in-bound usage may release unused reservation.
Overruns and incomplete responses retain at least the larger of the original
reservation and observed total. Overruns may visibly exceed 350,000: they are never
clamped. Missing/inconsistent counts and contradictory observations retain explicit
unknown-cost state and close admission. Stopped and unfinished journals cannot resume.
A single reducer drives accounting reconstruction, terminal summaries and inspection.

Owner-only, immutable `terminal-<task-digest>.json` files record task/status/reason,
attempts, cumulative reservations, conservative accounted tokens, observed usage,
unresolved attempts, cost uncertainty and evaluation eligibility. Budget exhaustion
is CENSORED; transport/capture/incomplete-response failures are FAILED; graceful
interruptions are INTERRUPTED. These outcomes cannot become valid negative results.
SIGTERM is converted into a recorded interruption and its prior handler is restored.

If SIGKILL, power loss or disk failure prevents a summary, use the offline inspector:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m ripple.pilot inspect \
  --output /absolute/private/existing-ledger
```

Inspection opens an existing journal read-only, never creates missing ledgers,
repairs files, constructs an SDK client or resumes admission. Unfinished journals
remain ineligible; corrupt/torn journals report unknown cost rather than invented
zero totals. A full disk can prevent any additional durable write; the inspector
cannot recover bytes that were never successfully recorded.

## Output isolation and provider metadata

Before creating output, both harness entry points reject canonical paths overlapping
registered/protected worktrees, shared Git storage, input packages or fixture sources.
Symlink ancestors and path aliases are rejected. Inherited evaluation checks also
reject unexpected files, empty directories and symlinks. Input fixture copies created
inside a private run directory remain intentional run artifacts.

Private wire metadata preserves allowlisted input/output/total counts and nested
cached/audio/reasoning/prediction-token details when explicitly present. SDK dumps
exclude unset fields. Requested, returned and fallback-derived model identifiers
are separate; missing returned models do not authorize evaluation. Missing provider
status/finish metadata is never defaulted to completion. Production LLMResponse,
report/evidence schemas, prompts, validators and retention policy are unchanged.

## Reproduction

`requirements/p2b-hardening.lock` pins 22 distributions with wheel SHA256 values for
CPython 3.13.16 on macOS arm64. All **11 recorded preparation SDK distribution versions
and wheel hashes match exactly**, including OpenAI 1.109.1 / HTTPX 0.28.1 / HTTPcore
1.0.9. The historical record did not pin the complete pytest/Ruff/Pydantic foundation;
its exact original state cannot be independently reconstructed from that record.
Those foundation versions were selected from the preserved original environment and
are now explicitly locked. This limitation does not prevent reproducing the recorded
SDK stack or all 443 existing passing tests and six original optional skips.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements/p2b-hardening.lock
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python dev/p2b/check_offline.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python dev/p2b/check_offline.py \
  tests/test_p2b_pilot.py tests/test_p2b_hardening.py
.venv/bin/ruff check --no-cache .
.venv/bin/ruff format --check --no-cache .
.venv/bin/python -m pip check
```

Before running, restore the three public fixture files to the paths in
`evidence/p2b-hardening/fixture_sources.json` and verify their listed checksums.
The runner refuses missing fixtures instead of adding skips, removes API credentials,
blocks Python socket connections, disables bytecode/pytest cache writes in the
reported invocation, and writes test artifacts under a fresh private temporary root.
Live SDK construction also checks the pinned Python/platform/package versions; the
approval harness hash binds both environment specification files. Existing approvals
must therefore be reviewed again for the changed harness. The original virtual
environment was never installed into or modified.

## Preservation and remaining gates

All 870 frozen evaluation files and all 1,018 P2A inherited files are byte-identical.
There are no unexpected evaluation additions. The original 963 tracked/untracked
files, detached audit worktree's 1,056 files, original index, protected refs, and
6,638 original virtual-environment files match the pre-work snapshot. Only the
explicitly authorized hardening branch was added; it still points at the base.
Full preservation details and the frozen checksum manifest are adjacent evidence.
Historical P2B preparation records/checksums remain unchanged and describe their
historical commit; new hardening checksums are separate.

The shipped provider contract remains UNVERIFIED. Context capacity, tokenizer/framing,
reasoning-inclusive output control, authoritative usage/billing, rate limits and
cancellation/provider-retry behavior still require deployment evidence. Local caps
cannot guarantee remote expenditure if the provider violates its contract. Private
raw captures require controlled retention; the new summary/metadata projections do
not make arbitrary response bodies suitable for publication.

Recommended next step: review the uncommitted diff and evidence. Live P2B admission
remains NO-GO; no provider request or calibration is implied by this hardening work.
