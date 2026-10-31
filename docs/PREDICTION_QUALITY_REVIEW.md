# RIPPLE prediction-quality review

**Finding: prediction quality remains inadequate and the revised controller's live
quality is unmeasured.** Historical file F1 is **0.015873**, recall is **0.011111**,
and 102/105 RIPPLE runs abstained. The reported 219 passing tests establish tested
implementation behavior; they do not establish useful feature-change prediction.
The offline demo demonstrates pipeline execution with supplied answers.

This review changed documentation only. No implementation, benchmark label,
request, schedule, saved result, or evidence artifact was changed. No provider
request, commit, or push was made. Inference cost for this review: **$0**.

## Evidence and scope

The primary denominator is the **105 scored RIPPLE runs**, covering 35 tasks and
three requested seeds in `final-v1`. The root-cause table covers their **102
abstentions**. Counts are runs, not tasks, tool errors, or candidate updates.

Inspected evidence:

- [Abstention audit](evidence/abstentions.json), independently regenerated to a
  temporary file and compared equal, including every per-run entry.
- All **735 final checkpoints** in [final-v1](../evaluation/raw/final-v1/), the
  [frozen configuration](../evaluation/final_config.json),
  [task manifest](../evaluation/data/final_fea_tasks.json), schedule, gold paths,
  and [summary](../evaluation/results/final_summary.json).
- All **456 JSONL traces** and **455 JSON reports** in the
  [artifact archive](../evaluation/raw/final-v1-reports-and-traces.tar.gz).
  Exactly **420 traces** match the current RIPPLE/A1/A2/A3 checkpoint paths;
  there are no missing referenced traces. The other 36 are superseded: 35 end
  in controller failure and one is unfinished. They are not extra scored runs.
- The 41 original provider-failure checkpoints in the two retry-history
  directories; both 15-run development smoke directories and their summaries;
  development MVP result versions; deterministic development baselines; both
  saved Stage B result sets; and the preserved scripted demo. Local demo traces
  are additional scripted executions, not additional benchmark observations.
  Local copies under `.ripple/baseline/` duplicate historical artifacts.

Archive SHA-256:
`4c187ca46ef3d558b878e8421de8ae95845855fbb58448774a533370c523ce4b`.
For each checkpoint, resolve its `trace_path`/`report_path` to the exact archive
member beginning `_artifacts/`. Do not choose a trace by task-name prefix: retries
would change the denominator. Line references below are one-based JSONL lines.

**Evidence limit:** historical `tool_result` events record tool name, arguments,
ID, success, duplication and truncation, but omit payloads. Historical ledger
rejections omit reason codes. We can establish ID mistakes, successful inspections
of named files, and observed state transitions; we cannot reconstruct complete
search results, semantic entailment, or every first failing validation gate.
Accordingly, classifications below identify the closest evidenced failure
mechanism. They are not claims that repairing that mechanism necessarily yields
a correct prediction. Unresolved semantic causes remain explicit.

## Root-cause counts

The categories are exclusive for counting. A confirmed candidate lost during
report validation takes precedence; otherwise an observed evidence-ID defect
takes precedence over a subsequent budget stop. Remaining rejected-only cases
are candidate-validation failures; remaining tool-budget stops are budget cases.
Everything else is classified under Other. Secondary symptoms are counted
separately below.

| Requested category | Primary cases / 102 | Evidence and interpretation |
| --- | ---: | --- |
| No candidates generated | **0** | Every RIPPLE run seeded 5–8 candidates; 819 seeds across 105 runs. No-update counts do not measure candidate generation. |
| Relevant files not retrieved | **0 established; true count unknown** | Missing search payloads prevent complete retrieval attribution. At least one gold source-file owner was successfully inspected in 82/102 abstentions. Nine abstentions inspected every gold source-file owner. These are lower bounds on exposure, not semantic-support measurements. |
| Candidates rejected by validation | **2** | One redundant `suspected` transition; one confirmed existing symbol incorrectly drafted as `new_file` and dropped. |
| Invalid or missing evidence references | **17** | Rejected updates cite unknown IDs, omit the latest observation ID, and/or cite a failed result. Other target/transition defects may coexist. |
| LLM/tool failures | **0 uniquely attributable** | No terminal provider failure remains among the 105 scored RIPPLE runs. Schema errors and unsuccessful tools occur, but traces do not establish them as the exclusive cause of any of these 102 abstentions. This is not a claim of zero model/tool errors. |
| Budget exhaustion | **11** | Tool budget reached with seeds present and no proposed ledger update. Five further budget stops are counted under evidence references. |
| Other | **72** | Seeds present, no proposed ledger update, and `no_progress` termination. The directly observed failure is failure to advance candidates. Why the model did not advance them is only partly identifiable. |
| **Total** | **102** | Three further runs retained predictions and are excluded from the abstention count. |

The largest group is **candidate non-advancement**, not absence of candidates.
Of the 72 Other cases, **57 successfully inspected at least one gold source-file
owner**. Across all runs, 85/105 did so, including all three successes. Successful
`inspect_symbol` on a symbol or file establishes exposure to its source/outline;
it does not establish complete, untruncated source or a correct reasoning step.
The remaining 20 abstentions without such an inspection cannot automatically be
classified as retrieval misses: search, references and other tools may have
exposed relevant files too.

### Reconciliation with the existing audit

| Observation | Count |
| --- | ---: |
| No proposed LLM ledger updates | **83** = 72 Other + 11 budget cases |
| Proposed updates but none accepted | **18** = 17 evidence cases + 1 transition case |
| Accepted confirmation, then empty validated report | **1** |
| Abstentions | **83 + 18 + 1 = 102** |
| Proposed / rejected / accepted updates, all 105 runs | **29 / 25 / 4** |
| Decisions | **2,116**; only 29 updates were proposed |
| Stop reasons | **85** no progress, **16** tool budget, **4** submitted |
| Submission rejections | **215**, all `no candidates are confirmed` |
| Decision / interpretation / tool-argument schema errors | **13 / 2 / 37** events |
| Abstaining runs with at least one schema error | **37**; overlaps primary categories |
| Requests prefix-truncated under the frozen policy | **15/105**, covering five task requests across three seeds. |

The truncation total is a **run count**. Request preparation remains frozen;
no truncation-policy change is proposed for the matched experiment.

Of 25 rejected updates, **22 have an independently visible ID defect**: 10 omit
the latest ID and include unknown IDs, nine omit the latest ID only, two cite
failed results only, and one combines unknown and failed IDs. Three have no
ID defect visible in metadata. Two of the stale-ID rejections occurred in a
successful run; another occurred in the report-validation abstention. Thus
update counts must not be mistaken for 22 distinct failed runs.

There are **440 non-duplicate `ok=false` results** across all 105 RIPPLE traces:
286 expansion-time `repo_facts` results, and 154 exploration results. All 102
abstentions have at least one unsuccessful result, largely because deterministic
expansion probes fact kinds irrelevant to many repositories. The tool code
returns `ok=false` for ordinary `none_detected` and `no_results`, as well as
invalid targets. Historical payload/error codes are missing, so these are not
440 proven execution failures. Exploration had unsuccessful results in 61 runs;
the abstaining subset contains 432 unsuccessful results overall.

### Concrete failure evidence

1. **Relevant code inspected, no confirmation.**
   [astropy-16677 / seed 17](../evaluation/raw/final-v1/astropy__astropy-16677__RIPPLE__17.json):
   trace member
   `_artifacts/astropy__astropy-16677__RIPPLE__17/runs/20261002T180650-f5126c7-3cd39aec.jsonl`.
   Line 8 seeds eight candidates. Lines 22 and 27 successfully inspect
   `astropy/modeling/core.py::Model.sync_constraints` and `::Model`, respectively.
   The latter is truncated. The owner is one of the two frozen gold source files.
   Repeated submit decisions contain empty `ledger_updates`; line 164 ends with
   `no_progress`. Some reasons even describe changes as already implemented,
   suggesting role/protocol confusion. That last explanation is an inference;
   the empty updates and successful inspections are recorded facts.
2. **Stale evidence despite successful relevant tools.**
   [SoftLayer-1766 / seed 1729](../evaluation/raw/final-v1/softlayer__softlayer-python-1766__RIPPLE__1729.json):
   member
   `_artifacts/softlayer__softlayer-python-1766__RIPPLE__1729/runs/20261002T211238-00e6a06-41ef6c1c.jsonl`.
   Lines 13/18 record successful reference/test results `e2`/`e3` for
   `SoftLayer/CLI/formatting.py::format_output`. Line 22 rejects confirmation
   citing only `e2`, while the latest observation is `e3`. This demonstrably
   violates the latest-ID gate; it does not prove that every other gate would pass.
3. **Invented IDs.**
   [conan-11284 / seed 1729](../evaluation/raw/final-v1/conan-io__conan-11284__RIPPLE__1729.json)
   rejects `eX?` at trace line 73, when the latest result is `e10`.
   [sqlglot-3991 / seed 1729](../evaluation/raw/final-v1/tobymao__sqlglot-3991__RIPPLE__1729.json)
   rejects future ID `e3` at line 17, when only `e1`/`e2` have been observed.
   Two other updates contain 1,107 and 1,325 IDs, respectively; the model is
   producing protocol noise, not merely choosing too few files.
4. **Wrong candidate transition.**
   [sqlglot-858 / seed 1729](../evaluation/raw/final-v1/tobymao__sqlglot-858__RIPPLE__1729.json):
   member
   `_artifacts/tobymao__sqlglot-858__RIPPLE__1729/runs/20261003T185137-6e4c0ef-703bb69a.jsonl`.
   Line 17 successfully checks references to `sqlglot/expressions.py::Window`
   using `e2`; line 97 replays that result. Line 101 rejects an update back to
   `suspected` citing `e2`. The valid existing symbol, eight seeds, no accepted
   intervening update, available capacity, and ledger transition rules identify
   the redundant suspected transition as the rejection. The exact reason was
   reconstructed from code and event order, not logged historically.
5. **Confirmation discarded by report drafting.**
   [supervision-1296 / seed 17](../evaluation/raw/final-v1/roboflow__supervision-1296__RIPPLE__17.json):
   member
   `_artifacts/roboflow__supervision-1296__RIPPLE__17/runs/20261002T202702-fbce6e4-9515ac0c.jsonl`.
   Line 98 accepts confirmation of
   `supervision/detection/core.py::Detections.from_lmm` using `e6`; line 118
   validates zero components. Its saved report explicitly says
   `invalid migration new-file dropped: supervision/detection/core.py::Detections.from_lmm`.
   The validator correctly rejects this representation of an existing symbol.
   Improving report generation is safer than permitting invalid `new_file` claims.

The three surviving components cite successful evidence IDs, but **3/3 provenance
does not prove semantic entailment**. Historical aggregate precision of 0.028571
is a mean over all tasks including abstentions; it is not conditional precision
among emitted components. All three predicted source files match frozen gold.

## Broader benchmark evidence

The same metadata-only classification was applied to all current ablation traces,
so abstentions outside production RIPPLE are also accounted for. These results
are diagnostic comparisons, not candidate product settings.

| Category | RIPPLE | A1 | A2 | A3 | All four |
| --- | ---: | ---: | ---: | ---: | ---: |
| No candidates generated | 0 | 0 | 0 | 0 | 0 |
| Retrieval miss established | 0* | 0* | 0* | 0* | 0* |
| Candidate/report validation rejection | 2 | 2 | 2 | 5 | 11 |
| Evidence-reference defect | 17 | 20 | 22 | 16 | 75 |
| Uniquely attributable terminal LLM/tool failure among abstentions | 0 | 0 | 0 | 0 | 0 |
| Budget, after the stated precedence | 11 | 4 | 3 | 4 | 22 |
| Other: no confirmation progression | 72 | 78 | 77 | 75 | 302 |
| Other: submitted draft omitted confirmed component | 0 | 0 | 1 | 0 | 1 |
| **Abstentions** | **102** | **104** | **105** | **100** | **411** |

`*` The true retrieval-miss count is unidentifiable from these traces. A1/A2/A3
validation rows can establish rejected updates but often cannot identify which
target, support, or transition rule rejected them. A3 disables report validation,
but still uses the candidate ledger. One A3 provider failure is outside these
411 abstentions and remains in the 735-run scoring denominator. Completed/partial
runs across these four systems total eight.

Earlier development evidence is separate:

- `mvp_agent_results.json` and `mvp_agent_results_v1.json` are byte-identical:
  13 abstentions among 17 evaluated tasks, with three setup failures. Do not count
  the copies as independent runs. Version v1.1 has 15 abstentions among 20 tasks.
  The referenced per-run reports/traces are unavailable locally. All **28 saved
  development abstention rows** are **Other: detailed cause unavailable** for
  this review; result summaries alone cannot support a finer causal assignment.
- The successful-transport development smoke has five RIPPLE abstentions;
  their referenced traces are unavailable too. These **5** are likewise
  **Other: detailed cause unavailable**. The earlier 401 smoke records provider
  failures, not five additional abstentions. These smoke tasks overlap development
  tasks; none of these counts is added to the final-test denominator.
- Retry histories preserve **18 timeout failures** and **23 other provider
  failures** across systems. After exact-run retries, the replacements include
  34 abstentions, six completions and one provider failure. Those replacements
  already belong to the final 735 checkpoints. Among RIPPLE specifically, eight
  original provider-failure checkpoints were replaced by abstentions; transport
  recovery did not establish prediction recovery.
- B0 ranked recall@5/@10 is **0.305/0.362**, showing incomplete lexical coverage.
  Its tiny matched-*k* set F1 is confounded by RIPPLE's near-empty prediction set.
  B3 one-shot F1 is **0.299437** on the same final tasks. This supports investigating
  the controller/protocol bottleneck; B3 does not satisfy RIPPLE's evidence
  requirements and is not a safe drop-in prediction set.
- A3 F1 is only **0.017460** with 100 abstentions. Disabling the final validator
  did not solve the collapse. The ablation is not a controlled per-candidate
  replay, but it gives no basis for removing evidence checks.
- Saved Stage B controls have 5/15 false alarms with oracle reports and 15/15
  with actual seed-17 reports. Excellent planted-anomaly detection with empty
  predictions does not establish useful end-to-end quality.

## Smallest safe changes and expected impact

**First measure the already implemented correction.** Current `full-report-v2`
already explains `ledger_updates`, previous-observation timing, exact latest IDs,
and allowed transitions; returns rejection codes to the next prompt; and records
payloads and final candidate state. The [controller](../src/ripple/agent.py),
[ledger](../src/ripple/ledger.py), and
[feedback regression](../tests/test_delivery_resilience.py) confirm this.
Repeating that work would not constitute a new improvement. Its live effect on
the 83 no-update runs and 17 evidence-defect abstentions is unmeasured.

| Priority and minimal intervention | Expected impact | Offline proof available | Live evaluation needed |
| --- | --- | --- | --- |
| 1. Evaluate current v2 against v1 before another behavioral patch | Tests whether existing protocol/feedback corrections reduce candidate non-advancement and ID errors. No numerical gain can be promised. | Prompt content, reason propagation, ID provenance, resource stops and unchanged scoring are testable. | Yes: model compliance, correct file recall, precision, latency and cost. |
| 2. Preserve a conservative validated fallback when a schema-valid model report loses all confirmed components | Addresses the one observed production case where confirmation survives but drafting destroys the prediction. Use the existing fallback builder, validate it again, and emit partial status with an explicit draft-rejection reason. One historical run is a demonstrated opportunity, not a measured recall gain. | Reproduce an existing confirmed symbol drafted as `new_file`, or omitted; verify only independently supported confirmed components survive. Also verify failed/unknown/unrelated evidence still produces nothing. | Live evaluation is needed for prevalence and net prediction effect; deterministic mechanics do not require inference. |
| 3. Only if v2 still emits empty updates: add one explicit prediction-role sentence and a concrete suspected → inspected → confirmed example to the decision prompt | Could reduce the recorded confusion between predicting changes and claiming to have implemented them. Prefer this bounded prompt change before changing state-machine rules. | Check prompt/schema consistency and that examples never supply a target-specific answer. | Yes. Scripted replies cannot measure whether the model follows it. |
| 4. Only after payload-rich runs demonstrate a retrieval deficit: deduplicate seeds by source owner and reserve a few inspection slots for uninspected owners | Could diversify the eight seed candidates without admitting more unsupported predictions. Current seeds may select multiple symbols from one file. This is a hypothesis, not an established historical cause. | Compare original and proposed rankings/seed coverage on fixed development base indexes, including absent-at-base files separately. | End-to-end effect requires live runs because observations and decisions change. |

For priority 2, do not rewrite `new_file` into `modify` on the model's authority.
Construct a fresh fallback **only from confirmed existing targets**, filter its
evidence to successful target-supporting records, preserve normal confidence
caps, validate it through the production validator, and retain deterministic
expansion. Keep reference/test checks or explicitly report incomplete checks in
partial output. Do not misrepresent fallback generation as model success.
The current fallback labels every component `business_logic`; avoid claiming that
generic tag as a verified change kind. The old payloads are insufficient to prove
an integrity-preserving historical recovery for supervision-1296 without rebuilding
its exact base evidence.

Do not auto-confirm lexical seeds, repair invented IDs by guessing, promote B3's
ranked paths into predictions, remove reference/test requirements, raise budgets
first, or count regression areas as affected source components. Each could hide
the measured bottleneck or inflate recall by admitting unsupported claims.

## What can be evaluated offline

There are real offline fixes and checks; they must be separated from a claim of
improved live prediction quality.

1. **Audit correctness and observability.** Regeneration reproduced the saved
   abstention audit exactly, and a separate standard-library pass resolved all
   420 checkpoint-linked traces, reconstructed latest IDs including duplicate
   replay, counted seeds and unsuccessful results, and joined successful
   inspections to frozen gold owner paths. Additive diagnostics can distinguish
   generation, exposure, advancement, report loss and transport without changing
   scoring. Full historical semantic replay remains impossible from IDs alone.
2. **Ledger/report fixes.** Replay scripted unknown, stale, failed and unrelated
   evidence; redundant suspected transitions; invalid `new_file`; omitted
   confirmed components; and genuine no-support requests. These establish
   correctness of feedback/fallback and continued abstention at the trust boundary.
   Use the actual validator and exact-base tool payloads, not fabricated support.
3. **Retrieval and scanner coverage.** With exact-base repositories available,
   scan and run fixed deterministic queries/seed policies without a model.
   Score owner-level recall@5/@10 and candidate exposure with frozen gold used
   only by the external scorer. Separate files absent at base from retrievable
   files. This measures retrieval behavior, not whether an LLM can infer the
   right change. Historical base checkouts are not present in this workspace;
   fetching public bases would be a separate network preparation step, not an
   inference charge. No such fetch was needed for this review.
4. **Scoring and resource checks.** Preserve per-run zero scores for abstentions
   and failures and seed-then-task aggregation. Test the proposed spend guard
   with a fake transport that retries, reports missing usage, and exceeds the
   reservation. Do not run target-repository tests or alter task labels.

Live inference is genuinely required to establish whether a changed prompt,
context selection, candidate ordering, tool schedule, or model improves actual
feature-to-file decisions. Frozen-response replay fixes the model's behavior;
it cannot estimate the response to changed observations or prompts. Reconstructed
tool payloads can validate provenance, but cannot restore an unrecorded historical
model response or establish its semantic reasoning.

## Cost-capped matched live pilot — proposal only

**No live run is authorized by this document.** Obtain explicit approval for the
exact endpoint/model, pricing, $5 total ceiling, and frozen run manifest before
any paid request. Missing pricing or approval means zero requests. Keep this
pilot separate from the frozen 735-run result set.

Use five representative existing tasks and the same requested seed within each
before/after pair:

| Task | Requested seed | Historical stratum | Historical tokens / model operations |
| --- | ---: | --- | ---: |
| `astropy__astropy-16677` | 17 | Relevant inspection, no candidate update | 113,350 / 33 |
| `softlayer__softlayer-python-1766` | 1729 | Stale evidence ID | 63,687 / 15 |
| `roboflow__supervision-1296` | 17 | Confirmed target lost during report validation | 58,389 / 22 |
| `tobymao__sqlglot-415` | 1729 | Tool exhaustion, no candidate update | 114,249 / 35 |
| `roboflow__supervision-847` | 1729 | Existing positive control | 37,560 / 13 |

These five tasks cover four repositories. They are intentionally diagnostic and
were selected after inspecting outcomes; they cannot support an unbiased holdout
or generalization claim. Do subsequent tuning on development tasks. A fresh
untouched evaluation set would be required for a generalization claim, without
changing existing split membership or labels.

### Before/after definition and matched conditions

- **Before:** original commit
  `358dbff3f63b1ed3b1048d5c032f66702cee0f6f` (`full-report-v1`).
  **After:** a content-hashed snapshot of the current uncommitted v2 source,
  with no additional behavioral fix. This first experiment measures the existing
  delivery bundle. It cannot attribute gains to a specific patch. A future
  fallback or prompt patch needs a separately approved v2-versus-patch experiment;
  do not silently add a third arm or extra repeats to this cap.
- Prepare separate disposable workspaces from the same exact task base SHAs.
  Perform the existing leak checks before each arm. Supply only the same frozen
  masked request with the existing 2,000-character prefix policy; match its hash.
  Do not expose gold, PR head/diff, historical predictions, or the diagnostic
  stratum to the agent. Gold remains in the scoring process only.
- Use the historical BullsAI endpoint and `openai/gpt-oss-20b` if approved and
  available. If unavailable, freeze one agreed replacement for both arms and
  label this a new-provider comparison. Match model revision, endpoint mode,
  dependencies, sampling settings and execution limits. The historical provider
  did not support seed control: requested seeds identify pairs, not deterministic
  replay. Record whether actual seed control is now available before scheduling.
- Run one fresh before and one fresh after per task: **10 runs total**. Alternate
  order before/after, after/before across the five tasks. Recompute both arms;
  a historical checkpoint is not a matched live before. Preserve all failures,
  capped runs, traces, payloads, hashes and time-stamped provider metadata.
- Keep the 25 exploration-tool, 30 candidate and four-no-progress limits equal.
  Additional financial rails below apply identically to both arms and make this
  a capped pilot, not an exact reproduction of the historical uncapped workload.
  Do not overwrite `final-v1` or call its full-schedule runner for this subset.

### Enforceable financial rails

The current `max_tokens` controller option is a **soft usage stop**: a single
request can overshoot it, missing usage undermines accounting, and provider
retries/fallback can create additional attempts. The current provider boundary
does not set per-response output limits. Therefore existing CLI limits alone
cannot enforce the proposed monetary ceiling.

Before live execution, prepare and test a shared evaluation transport guard for
both snapshots, or use equivalent provider-enforced controls. The guard must:

1. Require approved input/output prices including any other billed token classes.
   Reserve worst-case request cost **before dispatch**, covering full serialized
   input/schema and a provider-enforced maximum of **2,048 output tokens**. Use
   provider-compatible token accounting or a documented conservative upper bound;
   never assume character count equals token count.
2. Enforce **$0.50 per run**, **$5 across all ten runs**, and **150,000 total
   reserved/accounted tokens per run** (1.5 million aggregate). Refuse a request
   whose worst-case reservation would cross either limit. If the 16,384-token
   input ceiling is exceeded, record a capped failure; do not change the request
   or silently trim evidence. Choose prices and rails before either arm runs.
3. Limit dispatches to **40 HTTP attempts per run / 400 aggregate**, disable
   automatic retries and endpoint fallback for this pilot, and use one endpoint
   mode known to work for both arms. Count every attempted paid request, including
   failures, not just successful application operations. Add a 10-minute per-run
   wall-clock limit and a 100-minute aggregate limit.
4. Reconcile reservations with actual billed usage. If usage is unknown, retain
   the full reservation and stop that run. Never release an uncertain charge or
   retry to obtain a better result. Keep execution serial so reservations cannot
   race. Prefer a provider-enforced $5 prepaid/project limit with no automatic
   replenishment; if the provider cannot supply the information needed to bound
   charges, do not launch a supposedly hard-capped paid pilot.

The guard/output limits are proposed evaluation work, **not implemented or used
in this review**. If these rails truncate a pair, retain both scheduled outcomes
in the pilot and show the censoring; do not compare only completed pairs or retry
outside the cap. Stop immediately on an evidence-integrity violation and retain
the incomplete schedule and reason.

### Workload and dollar estimate

Historical RIPPLE workload was 6,391,770 input + 956,909 output = **7,348,679
tokens** and 2,240 application model operations over 105 runs. The selected five
historical runs total 387,235 tokens and 118 operations. Doubling those exact
tasks for matched arms gives the planning estimate:

- **667,806 input + 106,664 output = 774,470 tokens**, approximately **236
  application operations**. This is not a price quote or a guaranteed workload;
  new feedback can lengthen prompts or reduce the number of decisions.
- With approved prices `P_in` and `P_out` in dollars per million tokens,
  **estimated cost = 0.667806 × P_in + 0.106664 × P_out**, plus any explicitly
  billed additional classes. The financial reservation guard controls actual
  spend, rather than this average-based estimate.
- For arithmetic illustration only, hypothetical prices of $1/M input and $3/M
  output yield **about $0.99**. These are **not documented BullsAI prices**.
  The frozen config and smoke summary contain no end-user tariff; actual dollar
  cost is **unknown until the provider/account rate is supplied**. A genuinely
  funded zero-charge allocation would cost $0 only after that is confirmed.
- Maximum newly authorized spend would be **$5**, including failed attempts;
  the pilot may stop before ten runs if prices/reservations require it. This
  review's actual new paid calls, paid tokens and inference charge are all zero.

### Measurements and decision rule

Score all ten scheduled outcomes against unchanged Python-source gold using the
existing scorer and owner-path projection. Report each pair's precision, recall,
F1, false positives, prediction count, abstention/status, stop reason, seeded and
confirmed counts, update/rejection reasons, tokens, attempt count, latency and
dollars. One requested seed per task means five paired task differences, not
15-seed evidence. Show the small-sample paired mean and individual outcomes;
do not claim statistical significance or replace the full benchmark F1.

Use new payload-rich traces to measure at least-one-gold exposure, gold coverage
among inspected owners, and, where reconstructable, retrieved/seeded gold coverage
before confirmation. Exclude no gold file from the original primary score;
absent-at-base retrieval strata are additional diagnostics only. Keep provenance
validity separate from human semantic-support review of every emitted component.
Existing v2 payload logging can supply after-arm details; a common observer may
capture before-arm tool returns without changing the data given to its model.

A useful pilot signal requires positive paired mean recall **and** F1, no loss
of the positive control's correctly predicted file, no new unmatched source
predictions on these tasks, and **100% successful, target-supporting evidence
references** for emitted components. An independent review must also find each
reason supported by the recorded code. If the run is capped, this is a censored
pilot signal only. More predictions without support, or fewer abstentions without
better file scores, do not pass. A positive signal justifies a separately priced
larger evaluation; it does not establish product quality on five selected tasks.

## Does the offline demo prove prediction capability?

**It proves pipeline execution and specific deterministic trust-boundary
behavior, not autonomous prediction capability.**
[demo.py](../src/ripple/demo.py) supplies the answer through `ScriptedLLM`:
the intent, `billing.py::subtotal` target, inspection action, confirmation with
`e2`, reference/test actions and final report are prewritten. The weather case
is scripted to submit without confirmation. The fixture change and stale-caller
variant are also constructed in advance.

The [preserved trace](evidence/demo/trace.jsonl) and
[summary](evidence/demo/summary.json) establish that production tools can return
real evidence, the ledger/validator can accept a correctly prepared proposal,
expansion can find callers/tests, unsupported proposals can abstain, and static
verification can distinguish the supplied clean/anomalous variants. They do not
measure whether a live model can discover the target, supply valid updates,
choose relevant files on unfamiliar requests, or improve benchmark precision or
recall. The delivery record states the scripted positive path also worked on the
historical implementation. It is not a measured accuracy improvement.

## Per-case classification

Every current final abstention is represented below, including the ablations.
Each cell lists **seed 17 / seed 42 / seed 1729**, in that order. The checkpoint
path is `evaluation/raw/final-v1/<task>__<system>__<seed>.json`; its exact
`trace_path` resolves the supporting archive member.

- **N**: Other — no confirmation progression. For RIPPLE, precisely zero
  proposed updates and a no-progress stop; ablations also include an accepted
  suspected/rejected state that never became confirmed.
- **E**: Invalid/missing evidence references in rejected updates; may coexist
  with target/transition errors or a later tool-budget stop.
- **V**: Candidate/report validation rejection, with no visible ID defect
  explaining the primary outcome, or confirmation lost in final validation.
- **B**: Budget exhaustion after the stated precedence, with no confirmed target.
- **O**: Other — confirmed target omitted from the submitted report
  (`astropy-16677`, A2, seed 17).
- **S**: Retained predictions; not an abstention (includes A3 partial reports).
- **F**: Terminal provider failure; not an abstention, retained in scoring.

| Task | RIPPLE | A1 | A2 | A3 |
| --- | --- | --- | --- | --- |
| `astropy__astropy-13676` | N / N / B | B / N / N | N / B / E | N / V / N |
| `astropy__astropy-16677` | N / N / B | N / E / E | O / N / N | N / N / N |
| `astropy__astropy-7970` | N / N / N | E / N / N | N / V / N | N / E / N |
| `conan-io__conan-10154` | N / B / B | N / N / N | N / N / E | E / N / N |
| `conan-io__conan-10437` | N / B / N | N / N / N | N / N / N | N / N / E |
| `conan-io__conan-10874` | N / N / E | N / N / B | N / N / E | N / N / N |
| `conan-io__conan-11284` | N / E / E | N / N / E | N / N / N | E / V / N |
| `conan-io__conan-11585` | S / N / E | V / V / B | N / N / E | B / N / B |
| `deepset-ai__haystack-8042` | N / N / E | N / S / N | V / N / E | N / N / N |
| `google-deepmind__optax-1063` | N / E / N | E / N / N | N / E / N | N / N / N |
| `google-deepmind__optax-897` | N / N / N | N / N / N | N / N / N | N / N / N |
| `huggingface__accelerate-255` | N / N / B | N / E / N | N / N / N | N / N / V |
| `huggingface__datasets-214` | N / E / N | N / E / E | N / E / N | N / N / E |
| `matplotlib__matplotlib-23692` | E / N / E | N / N / N | N / E / N | E / N / E |
| `matplotlib__matplotlib-26293` | N / N / N | N / N / N | N / N / N | N / N / N |
| `pgmpy__pgmpy-1797` | N / N / N | N / N / E | N / N / N | N / N / N |
| `roboflow__supervision-1237` | N / N / N | N / N / E | N / E / N | N / N / N |
| `roboflow__supervision-1296` | V / N / N | N / N / N | N / N / N | N / N / N |
| `roboflow__supervision-1340` | N / N / N | N / N / E | E / N / N | N / N / N |
| `roboflow__supervision-847` | N / N / S | N / N / N | N / N / N | N / N / S |
| `roboflow__supervision-910` | N / N / N | N / N / N | N / E / N | E / N / E |
| `scikit-learn__scikit-learn-11196` | B / E / N | E / N / N | N / E / N | N / N / E |
| `scikit-learn__scikit-learn-17225` | N / N / N | N / N / E | N / N / N | N / N / N |
| `softlayer__softlayer-python-1766` | N / N / E | E / N / E | N / E / E | N / N / N |
| `sphinx-doc__sphinx-9154` | B / N / N | E / N / E | N / E / B | E / N / E |
| `sympy__sympy-12417` | N / E / N | E / N / N | N / N / E | N / V / N |
| `sympy__sympy-13304` | N / B / N | N / N / N | E / N / N | E / V / N |
| `sympy__sympy-14745` | B / N / N | N / N / N | N / E / N | N / F / E |
| `sympy__sympy-14853` | N / N / N | N / N / N | N / N / N | N / N / E |
| `sympy__sympy-14928` | N / N / N | N / N / N | N / N / N | S / N / N |
| `tobymao__sqlglot-3145` | S / N / N | N / N / N | N / N / N | B / N / N |
| `tobymao__sqlglot-3991` | N / N / E | N / N / E | N / N / E | N / N / N |
| `tobymao__sqlglot-4084` | N / N / E | N / N / N | N / N / E | N / N / N |
| `tobymao__sqlglot-415` | E / E / B | E / N / N | B / E / E | S / S / B |
| `tobymao__sqlglot-858` | N / E / V | N / N / B | N / N / N | E / N / N |
