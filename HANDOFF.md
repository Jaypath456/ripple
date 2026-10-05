# RIPPLE Cloud Continuation Handoff

## Current frozen product

Branch: `ripple-v2.1`
Frozen product commit: `f381d1c`
Frozen tag: `ripple-v2.1.1-frozen`
Product config: `full-report-v2.1.1`

Do not modify the frozen product implementation during the current held-out evaluation.

Historical branches:
- `main` -> `358dbff` — frozen final-v1 evaluation
- `ripple-demo` -> `7536594` — developer/research demo
- `ripple-showcase` / `prod-v1` -> `7ec7ff7` — recruiter showcase
- `ripple-v2` -> `414e734`
- `ripple-v2.1` contains `58997c7` then frozen `f381d1c`

## Frozen final-v1 integrity

All frozen final-v1 artifacts were byte-identical before this evaluation.
SHA-pinned tests passed.
Do not change historical final-v1 artifacts.

## V2.1.1 status

Completed before held-out evaluation:
- candidate decision checkpoints
- new-feature decision framing
- typed tool-target validation
- semantic duplicate suppression
- bounded ledger progress
- deterministic Ask RIPPLE diagnostics
- one bounded report-draft repair for an existing confirmed target incorrectly labelled `new_file`
- validator remains authoritative
- no Python rewriting of model claims
- 241 tests passed before held-out evaluation

## Current held-out evaluation

Location:
`evaluation/v2_heldout/`

Frozen manifest:
`evaluation/v2_heldout/manifest.json`

Frozen config:
`evaluation/v2_heldout/config.json`

Hashes:
- config: `019c3b37fbd2fe77ddbcdec8c6057af7bb0f313ace15a2f5a99ac3740f2c929c`
- manifest: `bbfcfa3ba265c9aaa53efa52a0ce0757b805dd4cbe6ce22b6d87bcd2b355913d`

Plan:
- 30 untouched held-out tasks
- V1 vs V2.1.1
- 3 repeats per system
- 180 scored task/system/repeat slots total
- same BullsAI `openai/gpt-oss-20b` model
- predetermined single retry for provider failure
- no tuning after evaluation started

## Current progress

Completed:
- 13 / 30 tasks
- 78 / 180 scored slots

Remaining:
- 17 tasks
- 102 scored slots

Existing `evaluation/v2_heldout/raw/*.json` files MUST be preserved.

The runner is resumable:
- existing first-attempt results are skipped;
- provider-error a1 results get exactly one a2 retry if missing;
- completed results are not overwritten.

## Why the local run stopped

The runner stopped while preparing the next repository because the local
machine temporarily could not resolve GitHub:

`fatal: unable to access 'https://github.com/joke2k/faker.git/': Could not resolve host: github.com`

This was an infrastructure/DNS failure, not an agent/evaluation failure.

Do not restart the evaluation from scratch.

## Cloud continuation

1. Check out `ripple-v2.1`.
2. Read this file, README, `evaluation/v2_heldout/config.json`,
   `manifest.json`, and `run_heldout.py`.
3. Verify HEAD/product commit relationship and hashes.
4. Configure the required BullsAI environment variables securely.
5. Resume the existing held-out run so completed slots are skipped.
6. Do not modify product source, prompts, budgets, validator, retrieval,
   checkpoint logic, or manifest/config after model execution has started.
7. If a product/code bug is discovered, STOP and report it rather than patching
   under the same evaluation.
8. After all 180 slots are resolved according to the frozen retry policy:
   run deterministic analysis, generate summary/README, run final checks,
   verify product source still matches frozen `f381d1c`, then commit only
   evaluation/documentation artifacts.

## After held-out evaluation

RAG / hybrid semantic retrieval is future V2.2 work.
Do NOT add RAG during this held-out evaluation.
