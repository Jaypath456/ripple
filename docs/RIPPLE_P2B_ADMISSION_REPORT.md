# RIPPLE P2B preparation — admission and dry-run report

2026-11-01, America/New_York. **Offline preparation. No inference, push or merge.
Live admission remains blocked.** This report records verification immediately
before the separately approved single local P2B commit. Its SHA is reported
externally to avoid a self-referential checksum.

Parent: `112092aae61d96891af192adead7bded833ea569`, branch
`integration/v2.1.1-p2`, in the existing isolated P2A worktree. All P2A tracked
files remain byte-identical. New harness, regressions, synthetic preparation assets
and this sanitized report form the exact 38-file authorized commit inventory;
no preexisting source file is modified.

## Implemented harness

`src/ripple/pilot_admission.py` supplies a locked, owner-only, hash-chained journal,
pre-send reservation and aggregate accounting, deployment-contract checks, request,
token, rate and time limits, and persistent stop handling. Interrupted or torn
journals refuse resumption. Reservations are persisted before sending; unknown or
ambiguous usage retains the reservation and closes admission. Valid completed
input/output/total usage reconciles a reservation only after capture is durable.

`src/ripple/pilot.py` supplies the separate CLI, capped SDK boundary, capture client,
strict replay client, paired controller execution, provenance comparisons and staged
rollout approval checks. `dev/p2b/run.sh` uses the existing Python environment and
an optional explicit temporary SDK path. No production prompt, policy, controller,
validator, ledger, serialization, hash input or default feature flag is changed.

The wire boundary injects the output cap into the actual SDK request, checks the
complete request against its conservative input bound, disables HTTP redirects,
and verifies SDK/application retry settings. Every failure, including 404, 429,
503, timeout or interruption, closes admission. No endpoint fallback or automatic
request replay is allowed in this pilot. An absolute main-thread SIGALRM deadline
supplements SDK per-I/O timeouts; an existing alarm requires review.

Each task has one captured model-response stream. Both local OFF/ON controller
replays read the same sealed stream, check every operation/prompt/schema hash and
consume it exactly. They cannot use sockets. The OFF replay must reproduce the
captured semantic report; both replays must preserve index, evidence, candidate
histories, presented IDs and caches. Only report ID and runtime seconds are removed
for semantic comparison. Original valid claims and dropped claims remain intact;
added targets must already be confirmed, cite successful presented evidence and
pass the unchanged ordinary validator again. No new confirmation is created.
Source bytes/commit cleanliness and approved runtime/package/ref identities are
checked, including before live wire attempts. Raw captures remain outside Git in
private temporary directories; only sanitized dry-run projections are included.

## Verified local policy and unverified UB deployment

| Limit | Enforced harness policy | UB deployment verification |
| --- | ---: | --- |
| Complete request input upper bound | 16,000 tokens maximum | Tokenizer and framing guarantee unavailable |
| Total generated tokens | Requested cap 2,000 per attempt, including reasoning required by contract | UB support/meaning unverified |
| Maximum pre-send reservation | Input bound + 2,000; at most 18,000 | Conditional on verified counting/output contract |
| Task token allocations, in order | 110,000 / 130,000 / 110,000 | Local allocation only |
| Aggregate planning reserve | 350,000 tokens | No dollar/billing guarantee |
| Wire attempts | 24 per task; 72 total | UB rate/quota limits unavailable |
| Absolute request deadline | 90 seconds | Server cancellation/billing semantics unknown |
| Time admission | 40 minutes/task; 120 minutes aggregate active execution | Local ceiling only |
| Parallel requests | One | Local lock and pending-attempt guard |
| Transient retries / HTTP redirects / 404 fallback | Disabled / disabled / disabled | No unverified duplicate-billing assumption |

The included provider profile is deliberately **UNVERIFIED**, with unknown deployed
context size, output ceiling/reasoning accounting, usage semantics, input framing,
RPM and TPM. The gateway/model names come from the frozen repository configuration,
not a newly verified deployment. **No numeric UB context-window limit is claimed.**
OpenAI model-card limits were not substituted for UB's deployment settings.

UB's [official BullsAI service page](https://www.buffalo.edu/ubit/services/BullsAI.html)
links to the [developer repository](https://github.com/ubuffalo-developer/bullsai).
The public document request returned 404. The configured environment has no `gh`
executable; noninteractive Git could not authenticate through its existing helper.
No credentials were requested, printed or changed. Documentation access stopped
there as instructed. No BullsAI metadata or inference endpoint was probed.

A live contract must contain reviewed primary-document paths and SHA256 values
for context, output/reasoning, counting, usage, rates and retry/billing. It must
also bind actual output-parameter semantics and rate values. The currently
implemented input-count method is a conservative serialized-request-byte bound
plus a documented framing upper bound; it is admitted live **only if UB's reviewed
tokenizer/framing contract establishes that bound**. Without that evidence, or if
UB requires a different authoritative counter, live admission stays closed and the
counter must be implemented and retested. A byte count alone is not verification.
Manual primary-document review remains necessary; a `VERIFIED_UB_DEPLOYMENT` label
is not independent certification. Current policy checks cannot establish provider
truthfulness, cancellation cost or a guaranteed monetary cap.

## Dry-run results

All three input manifests and separate oracle files are unchanged from the P2A
proposal. Six fixture baseline unit tests pass. Oracles and mock policy never enter
model context. The positive mocks deliberately submit empty drafts after genuine
checkpoint confirmations; these are injected regression scenarios, not observed
BullsAI errors or accuracy gains. Usage is an artificial 100 input + 50 output
tokens per response. The 18,000-token mock context is a test value, not a UB limit.

| Exact task ID | OFF source predictions | ON source predictions | Mock requests | Synthetic tokens | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| `P2B-DEV-001-STOCK-LOW-WATERMARK` | 0 | 1 | 7 | 1,050 | Retains `warehouse/status.py`; submitted, ON partial |
| `P2B-DEV-002-RESERVATION-CANCEL-NOTIFY` | 0 | 2 | 11 | 1,650 | Retains `booking/service.py` and `booking/notifications.py`; submitted, ON partial |
| `P2B-DEV-003-OUT-OF-REPO-IAM` | 0 | 0 | 5 | 750 | Both abstained; no confirmations |
| Total | 0 | 3 | **23** | **3,450 synthetic** | No duplicate inference |

Both replays used identical recorded responses and preserved provenance in every
case. The unsupported-task report is semantically identical OFF/ON. Added inference
for either replay is zero. Full source/response-stream/provenance hashes and
per-task outcomes are in `evidence/p2b-preparation/dry_run_results.json`. Raw
prompts, response bodies, ledgers and JSONL captures are retained privately outside
the worktree and are not included in the report evidence.

The prior 172,500-token expected pilot usage remains a provisional planning
estimate from an incomplete historical sample. This mock's 3,450 synthetic tokens
neither validate nor replace that estimate. Real target accuracy, costs, latency,
context capacity, reasoning use and historical recovery remain unmeasured.

## Regression and preservation verification

Final complete suite: **443 passed, six unchanged optional UI skips** in 89.42
seconds. All **64 new harness regressions pass**; all 385 P2A test identities and
statuses are preserved. Ruff and formatting pass, covering 110 Python files.
The real SymPy/Rich source-limit regressions run using the preserved P0 fixtures.
All provider work is scripted or uses an in-memory HTTP transport with sockets
blocked and API credentials removed from the test interpreters.

Tests cover exact reservation boundaries, task/global request and token exhaustion,
unknown/invalid/inconsistent/excess usage, context and output limits, complete
schema/prompt counting, output cap serialization, stop latching, timeouts and
interruptions, capture/disk failure, process locks, restart refusal, torn/hash-invalid
journals, artifact overwrite/symlinks, strict replay and response tampering, unchanged
provenance, source manifest tampering, contract evidence hashes, disabled retries,
HTTP redirects, 404/429/503 behavior, the three task oracles, and first/review/remaining
rollout gates. Real SDK 1.109.1 with HTTPX 0.28.1 is additionally tested against
in-memory success/307/429/503 responses: one send each, capped request body, no
redirect or retry, and no authorization key in the journal.

SDK/HTTP dependencies were absent from the original test environment. They were
downloaded from PyPI into a new temporary test-only target; no installed repository
environment package changed. Versions and distribution SHA256 values are recorded
in `sdk_test_environment.json`. Original Pydantic and existing test dependencies
remain in use. Other SDK versions require the boundary tests to pass again.

One earlier full run passed 436 tests and stopped a mock rollout when preparation
documentation was edited during the test. Its `request_identity_changed` guard
prevented another request. Inputs were then frozen and the full suite passed;
final validation after the redirect guard and temporary SDK tests passed as above.
Earlier mock scripts were also corrected to use real symbol IDs so the positive
cases exercise report omission, rather than the preexisting legacy fallback.

Verified unchanged: all **1,018 P2A tracked files**, **870 frozen evaluation files**,
original/integration index state, all existing user refs and frozen tag, the **963**
current original tracked/untracked files and **8,758** earlier original file hashes.
All 11 P2A checksum entries verify. Three inherited complete B0–B2 comparisons and
their normalized index/ranking hashes reverify. No fresh ranking/corpus/held-out
replay is claimed. Runtime Codex capture refs may rotate externally; no task command
wrote or restored them. At verification time nothing was staged or committed.
The separately approved local commit must recheck exact staged blobs, privacy,
checksums and preservation immediately before committing; no push or merge is
authorized.

## Exact commands and staged rollout

Run from the isolated worktree. This offline command is verified and creates a
fresh owner-only output directory (choose another new output name to repeat):

```sh
cd /private/tmp/ripple-v211-p2a-integration-20261101-isw7gltf/worktree
P2B_SDK_PATH=/private/tmp/ripple-v211-p2a-integration-20261101-isw7gltf/p2b-sdk-test-deps \
sh dev/p2b/run.sh dry-run --package dev/p2b \
  --output /private/tmp/ripple-p2b-approved-dry-run-20261101
```

The exact **first-task execution command for later approval** is:

```sh
P2B_SDK_PATH=/private/tmp/ripple-v211-p2a-integration-20261101-isw7gltf/p2b-sdk-test-deps \
sh dev/p2b/run.sh execute-one --package dev/p2b \
  --output /private/tmp/ripple-p2b-live-20261101 \
  --task P2B-DEV-001-STOCK-LOW-WATERMARK \
  --contract /private/tmp/ripple-p2b-approval/UB_PROVIDER_CONTRACT.json \
  --approval /private/tmp/ripple-p2b-approval/FIRST_TASK_APPROVAL.json
```

**Do not run inference now.** Those reviewed/approved files do not yet exist.
Copy the unapproved template outside the package after documented UB verification
and explicit execution approval. Bind its hashes to the complete package, harness,
reviewed contract, current user refs and exact private ledger directory. Execution
with the shipped unverified contract was tested: it exits 2 with
`provider_contract_unverified`, before SDK initialization or credential access.

First-phase approval permits only task 1, once, in the bound ledger directory.
The command terminates after capturing, replaying and writing its result. Review
source correctness, eligibility/refusals, citations, stop reason, usage/budget,
response/provenance hashes and preservation before doing anything further.

Tasks 2 and 3 require a separate remaining-phase approval that explicitly names
both tasks and hashes the reviewed task-1 result. Task 3 additionally requires a
finished task 2. Each invocation still executes only one task. The same journal
carries the 350k/72-attempt aggregate budget across invocations. Failure or unknown
billing stops the pilot and prevents resumption; no replacement trajectory or
budget increase is automatic. Set the feature flag OFF/unset for operational
rollback, preserve failed captures and compare sealed OFF output. Never reset,
rewrite refs, delete evidence, resend an ambiguous request or enable retries to
finish a trajectory.

## Remaining admission gates

- Verified UB deployment documentation and a supported authoritative/conservative
  token bound, total reasoning-inclusive output cap, usage, rate and billing rules.
- Explicit approval of the exact first task, reviewed source/contract/dependencies
  and bound private budget ledger. Later tasks require separate result review.
- A durable location for the private capture/dependency artifacts before live use;
  these preparation artifacts are temporary and have not been published.

The harness is prepared and tested offline. **Live admission is blocked, and no
pilot result or accuracy improvement is claimed.** The full new-file inventory and
checksums are in `evidence/p2b-preparation/file_inventory.json` and `SHA256SUMS`.
