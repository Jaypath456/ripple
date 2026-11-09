# P3 offline accuracy harness and demo

Development tooling for the opt-in P3 policies (`proof_selection`,
`diverse_seeding`). Nothing here contacts a model provider or reads credentials.

| File | Purpose |
| --- | --- |
| `fixtures.py` | 10 synthetic development tasks (6 repositories) with gold source files |
| `holdout.py` | 6 confirmation tasks frozen before the pre-submission proof pass; run once |
| `harness.py` | Runs every configuration × simulated policy and scores against gold |
| `demo.py` | Walks three cases (single-file, multi-file, abstain) stage by stage |
| `request_size.py` | P2B-equivalent request estimate (canonical bytes + 4,096; cap 16,000) |
| `stress.py` | 12 request-growth scenarios: original V2 vs `bounded_context`, with evidence checks |
| `results/` | Recorded output of the commands below |

```sh
python dev/accuracy/harness.py --set development --output dev/accuracy/results
python dev/accuracy/harness.py --set confirmation --output dev/accuracy/results
python dev/accuracy/stress.py --json dev/accuracy/results/stress.json
python dev/accuracy/demo.py --config G-combined   # or A-baseline to compare
```

Use the repository's locked environment (Python 3.13, `pydantic`); both scripts add
`src/` to `sys.path` themselves.

## What the numbers mean

The real controller, tools, ledger, checkpoint verification, validator and P2A
retention run unchanged. The model is a deterministic simulation that never sees
gold labels: interpretation returns each task's fixed search terms; one action policy
reproduces recorded live failure modes (fixation, repeated calls, file paths passed
to `find_references`) and another follows the controller's `needs` hints; the
checkpoint judge is a fixed lexical relevance rule; the report drafter keeps at most
two components. Changed policies change trajectories, so every configuration is run
fresh; this is not replay of recorded provider responses.

These are author-written synthetic tasks, not a held-out benchmark. The development
set was used to diagnose the premature-submission failure. Results measure the
controller's behaviour under a simulated model and are not comparable to historical
FEA-Bench F1 or evidence of live accuracy.

## Configurations

| Name | Flags | Retention |
| --- | --- | --- |
| A-baseline | `RIPPLE_V2` | off |
| B-retention | `RIPPLE_V2` | on |
| C-proof-selection | `proof_selection` | off |
| D-diverse-discovery | `diverse_seeding` | off |
| E-review-progression | `review_progression` | off |
| F-bounded-context | `bounded_context` | off |
| H-precision-guard | `precision_guard` | off |
| G-combined | all five flags | on |
| G-without-guard | all but `precision_guard` | on |
| G0-combined-no-retention | all five flags | off |
| P3a-proof+diverse+retention | session-1 combination | on |
| T-confidence-tiers | `confidence_tiers` | off |
| GT-combined+tiers | all six flags | on |

`primary_*` and `secondary_*` split final predictions using the controller's
definition: a file is late (secondary) when its first accepted confirmation came
after a deferred submission; `primary_precision/recall/f1` score the primary tier
alone; `fp_by_origin` attributes false
positives to direct seeds, structural admissions or other candidates.
