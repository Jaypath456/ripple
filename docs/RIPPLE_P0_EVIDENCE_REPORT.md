# RIPPLE Phase P0 evidence report

Audit date: **2026-10-31, America/New_York**. Phase P0 only. **Zero inference or
paid API calls; no algorithm edits, merges, commits, pushes, resets or stashes.**
The only proposed tracked addition is this report. Evidence was written into a
new ignored local directory; existing user files were preserved.

## 1. Verified Git state

| Ref | Exact SHA |
| --- | --- |
| Current `review/ripple-accuracy`, delivery HEAD | `baf2fc6fa5f34e092b773a2c87d5f3b76bfeb965` |
| Local `main`, cached `origin/main`, common ancestor | `358dbff3f63b1ed3b1048d5c032f66702cee0f6f` |
| `ripple-v2.1.1-frozen` tag and commit | **`f381d1c7c6f55e504cee3f9e6beb425d2387ec0d`** |
| Cached `origin/ripple-v2.1`, saved held-out results | `0119bee5666dfbabecbdf50790bccfe560717c2a` |

The frozen tag is lightweight and resolves exactly to the required commit.
Its source tree is `8f5ef60c86010183941ffdf560e105d3720f039f`.
Local branches are `main` and `review/ripple-accuracy`; no local `ripple-v2.1`
branch exists. Cached remote branches are `origin/main` and `origin/ripple-v2.1`;
`origin/HEAD` points to main. The only tag is the frozen tag. Review has no
upstream. Origin is `git@github.com:Jaypath456/ripple.git`.

`git ls-remote --heads --tags origin` failed on GitHub DNS resolution. Actual
remote review-branch availability is **unverified**, not proven absent.
No fetch or ref update was performed.

Initial inventory: **956 tracked files**, no tracked modifications or staging,
**6 untracked files**, and **7,772 ignored files** (109,822,992 bytes by file
size). Untracked: three `.DS_Store` files at root, docs and docs/evidence, plus
`docs/docc/{PREDICTION_QUALITY_REVIEW.md,RIPPLE_DELIVERY.md,abstentions.json}`.
The three docc files are byte-identical to their tracked counterparts. All six
remain excluded. Ignored data includes `.env`, `.venv/`, `.ripple/`, build output,
caches, egg-info and five unsanitized local evidence originals. Complete initial
path/size/hash inventories were retained in private temporary audit scratch.
Final SHA-256 verification found every preexisting tracked, untracked and ignored
file unchanged; all Git refs and the index also match the initial state.

## 2. Delivery versus V2.1.1

Delivery is the **preexisting** commit `baf2fc6`, one commit above 358dbff;
V2.1.1 is a separate descendant of 358dbff. Neither implementation contains the
other. The delivery's `full-report-v2` label does **not** mean the frozen
`full-report-v2.1.1` candidate-checkpoint protocol is present.

Against 358dbff: **51 paths changed: 30 added, 21 modified**, including 17 source
modules, two new test files, three changed test files, three evaluation utilities,
CI, docs and evidence. Exact inventory:
`../.ripple/p0-evidence-20261031/existing_delivery_commit_files.txt`.

Source inventory: `agent.py`, `agent_models.py`, `cache.py`, `cli.py`, new
`demo.py`, `diffing.py`, `evaluation.py`, `facts.py`, `ledger.py`, `llm.py`,
`models.py`, `scanner.py`, `search.py`, new `source.py`, `tools.py`, `validate.py`,
and `verification.py`, all under `src/ripple/`.

Against the frozen tag: **107 paths differ: 30 added, 23 modified, 54 absent**.
The absent files include V2 protocol tests, the Streamlit demo, fixtures and
V2 development evaluation; these are divergent snapshots, not deletions made
by P0. Never replace V2 with this whole tree.

Both lines changed `README.md`, `agent.py`, `agent_models.py`, `cli.py`,
`ledger.py`, and `llm.py`. Three-way file merge simulation against 358dbff found
textual conflicts in **README, agent and agent_models**; cli, ledger and llm
merged textually but still require semantic review. No merge was applied.

Independently portable candidates are bounded source/filesystem reads, Git
diff execution/path safety, checkout preservation, provider status/retry handling
and CLI repairs. Failed-evidence/per-target validation should be ported with V2
protocol tests. Prompt/ledger feedback, source/context caps, accepted provider
outputs and token stopping rules can change predictions; their accuracy effect
is unmeasured. Detailed per-improvement test mapping and gaps:
`../.ripple/p0-evidence-20261031/REGRESSION_MAP.md`.

## 3. Independently executed offline checks

| Check | P0 result |
| --- | --- |
| Full suite, Python 3.13.16 | **219 passed in 18.09 s** |
| New delivery security / resilience tests | **27 / 22 passed**, included in 219 |
| Ruff lint | Passed |
| Ruff formatting | Passed; tool reported 75 formatted files |
| Dependency consistency (`pip check`) | Passed; harmless cache-permission warning |
| Same security tests against archived 358dbff | **16 failed / 11 passed** |
| Resilience tests against archived 358dbff | **12 failed / 9 passed / 1 deselected** |

Test interpreters blocked socket connections, removed API credential environment
variables, disabled bytecode/pytest caches and disabled Git SSH. Tests used
scripted or mocked providers and fresh temporary repositories. No dependencies
were installed. Existing source and evaluation artifacts were not regenerated.

Baseline production source was untouched. Security tests were copied unchanged.
To collect resilience tests on the old tree, the absent demo import/test was
removed/deselected and absent size-limit imports were replaced with identical
numeric constants; assertions were unchanged. Thus **28 demonstrated failures**
become passes, **20 checks already passed**, and the new demo adds one passing
feature check. The 49 tests are not 49 independently demonstrated fixes.

No direct matched regression covers every output-directory guard, checkout-hook
suppression, or the specific prompt/inspect character caps. CI's Python 3.11
configuration, live provider behavior, stress/install measurements and prediction
accuracy were not independently executed. Existing delivery docs' statement
that all changes are uncommitted is stale. Their older final-v1 F1 of 0.015873
belongs to a different protocol/set from V2.1.1's held-out F1.

## 4. Publication safety

Scanned all 956 branch-tree files, all six preexisting untracked files, 63
additional unique ancestor blobs, and 1,366 files inside the inherited archive.
Checks covered credential signatures/assignments, private-key markers, JWTs,
authenticated URLs, exact locally available secret values, home paths and email
locations. **No credential/private-key/signature or exact local-secret match
was detected.** Thirteen assignment matches were placeholders or code expressions.
No actual credential values are present in this report or scan output.

Privacy findings remain: **108 legacy home-path references** in three inherited
MVP result files, and email matches from benchmark/PR text, synthetic fixtures and
history. These artifacts were preserved. A push includes ancestor history and
author metadata. This is **not an unconditional privacy clearance**: review the
inherited path/email findings before approving publication. Rewriting frozen
artifacts/history to remove them is outside P0.

The existing final-v1 archive is 1,607,532 bytes; dev_baselines.json is 1,967,029
bytes. Both are inherited, unchanged generated artifacts. The archive contains
456 JSONL logs of public benchmark runs, with structured model decisions and
provider error events; no raw provider-response field keys were found. Detailed
delivery traces now log tool payloads and can contain repository source, so
local traces must be sanitized before sharing. No private local trace is added.

`.env`, `.ripple/`, `.venv/`, build/cache files, unsanitized evidence originals,
all `.DS_Store` files and duplicate docc files are excluded. No dedicated secret
scanner was available; the documented local scan cannot prove absence of every
possible secret or personal datum.

## 5. Proposed review-branch commit inventory

The dedicated branch already exists at the verified delivery SHA. No branch
switch or new implementation commit is needed. Relative to main, the existing
commit inventory is exactly:

1. **Already present before P0:** `baf2fc6fa5f34e092b773a2c87d5f3b76bfeb965`,
   “Prepare RIPPLE implementation and evidence for architecture review” — 51
   changed files, 8,268 insertions and 73 deletions; exact paths in the package.
2. **Proposed, not created:** `docs: record Phase P0 preservation audit` — add
   **only `docs/RIPPLE_P0_EVIDENCE_REPORT.md`**. Its SHA cannot exist before an
   approved commit. No implementation, benchmark artifact or local evidence
   package is proposed for that commit.

The proposed tree would have 957 tracked paths; its exact list is
`../.ripple/p0-evidence-20261031/proposed_review_tree_files.txt`.

No preexisting user change is silently included. All six initial untracked files
and all ignored files stay local. Approval is required before either committing
the report or pushing. A push is also pending inherited-metadata review and a
fresh remote-head check; no force push or history rewrite is proposed.

## 6. October 5 held-out traces and saved results

The held-out manifest, frozen configuration, summary, complete saved results and
partial raw checkpoints are recoverable **from Git objects at origin/ripple-v2.1**,
without checking out that branch. The tag predates these evaluation artifacts.
Verified canonical config hash, manifest SHA-256 and frozen source-tree hash.
Recomputed V2.1.1 mean file F1: **0.19814814814814813**, 30 tasks × 3 repeats,
16 repositories; statuses: 46 completed, 5 partial, 37 abstained, 2 failed.

All requested priority slots are identifiable in saved results: **25 abstained
with zero confirmed candidates; 12 abstained with confirmations but no predicted
source files**. Of those, only **9/25 and 8/12** have matching individual raw
checkpoint files. Across both systems, saved results contain all 180 scored
slots, but Git retains **89 of 197 attempted checkpoints**, covering 78 final
slots. All available final raw checkpoints agree with saved results.

**Original October 5 agent artifacts and JSONL logs are unavailable in the
searched accessible local locations.** The expected
`.ripple/evaluation/v2-heldout/artifacts/` directory does not exist. Searched
Documents, Downloads, temporary directories and expected Hugging Face caches;
9,125 filenames were examined, with 61 permission-denied traversal errors.
Private application/session logs and remote machines were not accessed. The
inherited final-v1 archive contains October 1–3 logs and cannot substitute.

Saved checkpoints omit the original agent run IDs and trace paths. The package
preserves actual task/system/repeat/attempt identifiers, Git blob IDs, SHA-256
and JSON pointers; missing agent IDs are explicitly null. Missing event logs
prevent independent diagnosis of which candidate/report step lost each target.

## 7. Sanitized local evidence package

Directory: **`.ripple/p0-evidence-20261031/`** (gitignored; do not push its parent).
Contains source/test snapshot and patch, exact change inventories, regression
outcomes, conflict inventory, sanitized metadata for all 90 V2.1.1 slots and the
37 priority slots, attempt availability, verified hashes, census, safety findings
and `SHA256SUMS`. Free-text provider errors, request bodies, raw responses,
dropped-claim narratives, personal emails and home paths are excluded from
diagnostic records. Synthetic source/test identities remain in the code snapshot.
Nothing was automatically sent to Claude or uploaded.

## 8. FEA-Bench census without inference

Available manifests: development 10 tasks/10 repositories, MVP 20/20,
final-v1 35/14, V2 held-out 30/16. Their union plus V2 development yields
**85 prior benchmark task IDs across 32 repositories**, plus four synthetic
development cases (89 total case/task IDs). Exclusions include every prior
task and every prior repository, broader than the historical held-out builder's
task exclusions plus four V2-development repositories.

After those strict exclusions, available prepared manifests contain **0 eligible
tasks / 0 eligible repositories**. Among 84 historical screening rows, seven
rows from five unused repositories survive the repository/task exclusions; all
seven failed the held-out 2–4-source-file rule. Some could fit the older 2–20
rule, but their complete metadata/checkouts are unavailable and eligibility is
unverified.

**Full-dataset eligible task/repository counts are unknown. Capacity for 80 fresh
repository-disjoint tasks is unverified.** Eighty mutually repository-disjoint
tasks require at least 80 eligible unused repositories; merely being disjoint
from prior evaluation repositories is a weaker requirement. Neither capacity
is established by selected manifests or the stopped-at-30 screening log.

Missing: pinned full FEA-Bench parquet/table and complete Lite inputs, unsampled
PR metadata/diffs, fresh base checkouts/fetchability validation, and optional
`pyarrow` for the historical builder (not installed locally). The pinned full
table SHA-256 is
`22ebac756180b3dfd63d1c7d57a1ed7cf5707a28c1e4ce59929dc30955cf0836`.
No dataset, manifest, label or gold file was altered; no builder was run.

## 9. Blockers and remaining uncertainties

- Original October 5 agent traces/run IDs and 108 attempted raw checkpoints are
  missing; saved result summaries support outcomes, not complete causal replay.
- Missing full benchmark inputs prevent an honest global eligibility count.
- GitHub DNS prevents fresh remote verification; inherited privacy metadata
  requires a publication decision before push.
- Textual merge success does not establish protocol compatibility; several
  defenses lack individual matched tests, and delivery accuracy is unmeasured.

## 10. Exact recommended next action

**Have Claude review the local sanitized source/evidence package first.** Recover
the original October 5 `.ripple/evaluation/v2-heldout/artifacts/` directory and
remaining raw checkpoints from the original execution machine if available, and
restore the pinned FEA-Bench table for a read-only census. Then obtain explicit
approval for the report-only commit and, separately, publication of the existing
delivery branch after inherited-metadata review and a fresh remote-head check.
Keep V2.1.1 frozen; select and test individual security/resilience ports before
any later integration or paid accuracy experiment.
