# Explicit UB course experiments

`python -m ripple.pilot` remains strict. Its provider contract and default command
are unchanged. `dev/p2b/provider_contract.json` remains UNVERIFIED and cannot admit
live requests. No production prompt, agent logic, evidence validator, task
fixture, oracle, evaluation expectation or frozen artifact changes in this work.

The separate `python -m ripple.pilot_experimental` entry point requires explicit
`--profile EXPERIMENTAL_UB_COURSE`. This policy permits an operator to accept
uncertain deployment behavior for a bounded academic experiment. It does not
verify, replace or satisfy the strict provider contract. `ExperimentalPolicy`
is deliberately not a `Contract`.

## Operator review sequence

This change is for independent review; no live task or approval is included.
Execution requires a clean reviewed commit descended from hardening commit
`8d750d89f36ae0f8f1cd7aba00d1d87e13f471fe`. The current uncommitted development
checkout cannot execute through this entry point.

1. Review the source, tests, report and ZIP. Commit the reviewed implementation
   only with separate authorization. Use the locked preparation environment.
2. Choose one private campaign output directory outside all repositories, Git
   directories, frozen evaluation directories, input packages and fixture sources.
   Its parent must already exist. Keep this directory for every task in the campaign.
3. Generate a fresh unapproved template offline:

   ```sh
   python -m ripple.pilot_experimental prepare-approval \
     --profile EXPERIMENTAL_UB_COURSE \
     --task P2B-DEV-001-STOCK-LOW-WATERMARK \
     --output /absolute/private/campaign
   ```

   This command makes no SDK client, reads no credential and writes no output
   artifact. It may run on a dirty checkout for inspection, but its binding must
   be regenerated after the final commit before execution.
4. Review its exact commit, complete runtime/harness hash, input-package hash,
   protected-ref digest, canonical output directory, model, profile, limits and
   explicitly unverified assumptions. Save the approval outside repositories as
   an owner-only regular file (mode 0600, no symlinks or hard links). Set a real
   operator identifier and a unique approval ID. Only explicit operator approval
   may set both `approved` and `acknowledges_unverified_provider` to true.
5. A separately authorized invocation must supply that approval file and the
   additional `--approve-live-task` value equal to the single `--task` ID. The
   execution command is `execute-one`; neither profile selection nor template
   generation is authorization. There is no batch or automatic continuation.
6. Inspect the journal offline with the unchanged `python -m ripple.pilot inspect
   --output /absolute/private/campaign`. Review terminal eligibility, uncertainty,
   OFF/ON predictions and false positives before any later approval. For task 2/3,
   a new task-specific approval must include `reviewed_previous_result_sha256`,
   computed as `ripple.pilot_admission.digest(parsed_previous_result_json)`.
   All preceding tasks must be completed and their terminal evidence verified.

Approval is a trusted local operator attestation, not a cryptographic signature
or an authorization service. Preserve the private campaign directory; deleting
or editing its ledger removes its audit history and is outside supported use.
Do not operate concurrent campaigns against the same account. A journal's process
lock and the single-pending-attempt rule serialize its entire campaign task.

## Fixed local controls

| Control | Value / behavior |
| --- | --- |
| Provider | `https://gateway.bullsai.buffalo.edu/v1` |
| Requested and required returned model | `openai/gpt-oss-20b` |
| Endpoint | Responses only; Chat fallback stops before dispatch |
| Per-request input estimate | Entire canonical UTF-8 request byte length + 4,096 allowance; maximum 16,000 |
| Requested output cap | `max_output_tokens=2000` |
| Local context planning ceiling | 18,000 estimated input plus requested output |
| Local rate ceilings | 24 attempts / 110,000 reserved or observed tokens in a rolling minute |
| Task attempts | At most 24 actual dispatch attempts |
| Aggregate attempts | At most 72 across the shared campaign ledger |
| Task allocations | 110,000 / 130,000 / 110,000; existing task definitions unchanged |
| Aggregate allocation | 350,000 local accounted tokens |
| Time | 90 s absolute request deadline and SDK I/O timeout; 2,400 s/task; 7,200 s aggregate |
| Transport | SDK retries 0; application replay retries off; redirects off; no endpoint fallback |
| Concurrency | One pending request and exclusive campaign journal lock |
| Accounting | Reserve before dispatch; retain max(reservation, valid observed total); never refund |
| Request formatting | `compact-structured-json-v1`: proven default-`json.dumps` `repository_data` objects are re-emitted with compact separators before admission; parsed context, all other prompt bytes and the logical journal prompt are unchanged; bound into the approval |
| Failures | Durable private censored/failed/interrupted summaries; unknown cost retained; no resumption |

The framing allowance, context planning ceiling and rate ceilings are LOCAL
ASSUMPTIONS, not BullsAI limits. Serialized bytes are not a verified tokenizer
bound. Reasoning counts, when explicitly reported, cannot exceed reported output;
missing reasoning details are not invented. Valid usage is retained, but a
complete consistent reported total does not establish billing authority.

An observed input/output overrun, missing/inconsistent usage, incompatible model,
incomplete response, provider error, timeout, identity change, capture failure or
unexpected endpoint stops the experiment. An attempted overrun remains visible
and is never clamped to 350,000. A local bound cannot prevent an already-dispatched
request from exceeding an undocumented server limit or hidden billing quantity.
Server-side retries, cancellation and continued work after a timeout remain unknown.

The 24/72 attempt numbers are upper limits, not promised capacity. At maximum
18,000 reservation per request, the first/second/third task can admit only
6/7/6 such requests; the aggregate allocation admits at most 19. Smaller input
estimates permit more attempts. Conservative accounting can censor a run even
when reported usage is small; a censored run is not a valid negative result.

## Result interpretation and privacy

Every task-start journal record, terminal summary, inspector task row and result
contains the experimental profile, `provider_guarantees_verified: false`, approval
hash and campaign-binding hash. Completion eligibility means evidence/replay
integrity within this experiment; it never means production provider readiness.
The immutable campaign metadata contains the complete bound policy and identities.

One trajectory is captured with retention OFF and replayed with identical responses
OFF and ON using the existing harness and unchanged validators. No extra model
request is made for either replay. Synthetic fixture inputs alone reach the model;
oracles are consulted only after capture/replay. Private journals and trajectories
contain fixture prompts/responses and must not be placed in a public review ZIP.
Credentials are read only by the existing client after admission and environment
checks. CLI failures never echo provider exception messages.

Two prior operator-reported calibrations observed capped reasoning-inclusive
output at caps 256 and 32. They are observations, not general guarantees. Deployed
context, supported maximum output, tokenizer/framing, authoritative usage/billing,
rate/concurrency limits, cancellation and parameter guarantees remain unverified.
