# P2B preparation assets

This is a small synthetic development pilot, not a held-out accuracy benchmark.
Production source behavior and defaults remain unchanged. All task requests and
source manifests were preregistered in the P2A pilot proposal. Fixture contents and
oracles are unchanged from that proposal. Only the individual fixture source root
and task request enter analysis. Never index this whole package or expose the
oracles, mock response policy, approval template or documentation to the model.

`python -m ripple.pilot dry-run` creates three temporary input Git repositories and
records scripted responses through the real request/admission boundary. It then
runs each controller twice using only the recorded responses. The positive mocks
intentionally submit empty reports after genuine checkpoint confirmations; the
negative mock never confirms a source target. These injected losses are regression
tests, not observed model failures or accuracy gains. Synthetic usage is 150 tokens
per mock response. Replays block sockets and remove API credentials.

`provider_contract.json` is intentionally UNVERIFIED. Do not replace missing UB
context/output/usage/rate/tokenizer evidence with OpenAI model-card values.
The verified live contract must cite local primary-document paths and SHA256 hashes
for context, output (including reasoning), counting/framing, authoritative usage,
rate limits and retry/billing semantics. `Contract.load` requires operator-reviewed
UB deployment evidence; it does not certify documentation claims automatically.
The only implemented live input-count method is an attested conservative bound on
the whole serialized request plus documented framing. If UB cannot establish that
bound, an authoritative counter implementation and new tests are required. A byte
count by itself does not verify a token limit.

Copy `APPROVAL_TEMPLATE.json` outside the package for any later reviewed execution.
No approval is granted by this template. Bind it to the full package, harness,
current non-Codex refs, reviewed provider contract and one fixed private output
ledger directory. First-phase approval admits only task 1. The same approval cannot
run another task or a second output ledger. A later remaining-phase approval must
bind the hash of task 1's reviewed result and explicitly name tasks 2 and 3. Only
one task can run per command; task 3 also requires completed task 2.

The live command has no automatic retry or 404 fallback, including when the SDK or
production client would otherwise recover. All failures retain conservative token
reservations and block further requests; incomplete captures cannot resume. The
outer wall-clock deadline supplements the SDK's per-I/O timeout and requires the
main thread on platforms with SIGALRM (tested on macOS). Usage must include valid
input/output/total integers with a consistent sum and stay within the admission
reservation. RPM/TPM counters reconcile only authoritative completed usage.

Raw prompts, responses, ledgers, indices and traces stay in an owner-only private
output root. This package contains no provider credentials or historical raw
traces. No production/integration commit or push is made by preparation. Regression
and dry-run checks create only disposable fixture commits in independent temporary
repositories, as required by the existing scanner.

The final HTTP-boundary checks use OpenAI-compatible SDK 1.109.1 and HTTPX 0.28.1
in a temporary dependency directory, with an in-memory HTTP transport and sockets
blocked. They verify the serialized output cap and one-send behavior for success,
redirect, 429 and 503 responses. No SDK packages were installed into the repository
virtual environment. Set `P2B_SDK_PATH` explicitly to that reviewed temporary test
directory when reproducing this environment; `run.sh` otherwise uses only the
repository's existing virtual environment. Dependency versions and distribution
hashes are recorded in the preparation evidence. This SDK test does not establish
UB's deployment context, reasoning-token, tokenizer, rate or billing guarantees.
