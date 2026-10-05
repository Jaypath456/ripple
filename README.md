# RIPPLE

RIPPLE predicts which files in a Python repository a feature request is likely to
touch. Later, it checks the actual Git implementation against that saved
prediction. The design rule is that the model may only *propose*. Python code
validates every tool call, owns all evidence, enforces budgets, and makes every
final classification.

## Problem

Before you implement a feature in an unfamiliar codebase, you want to know the
change surface: the files to edit, the tests to update, and the callers that could
break. After you implement it, you want to know whether the diff matches the plan:
unplanned files, dropped tests, and callers left behind by a signature change.
Plain LLM agents answer the first question with unverifiable guesses and rarely
answer the second at all. RIPPLE separates the two questions and keeps the
LLM's role narrow.

## Architecture

```text
            ┌──────────── deterministic, never executes target code ────────────┐
repo ──► scan (AST index, imports, references, tests, BM25, co-change history)
                │
request ──► Stage A: bounded agent ──► validator ──► Expand ──► change-impact report
                │   (LLM picks tools,     (drops claims     (tests, regression
                │    Python runs them)     without evidence) areas, order)
                ▼
git range ──► Stage B: diff ⟷ saved report ──► expected / adjacent / unexpected /
                                              missing_test / stale_caller / ...
```

- **Index.** RIPPLE scans only Python files tracked by Git. It uses Python's
  `ast` module to extract symbols, imports, references, and test mappings. Target
  code is never imported, executed, or installed. Indexes are cached per commit
  under `<repo>/.ripple/index/`.
- **Seven deterministic tools.** `search_code` (BM25), `inspect_symbol`,
  `find_references`, `get_dependencies`, `find_tests`, `repo_facts` (routes,
  models, settings, entry points), and `co_changed` (pre-base Git history). Every
  result carries an invocation-local evidence ID.

### Deterministic trust boundary

The model never touches the filesystem, Git, or the report directly. It emits
tool requests and a draft report as JSON. Python then:

- validates each argument against the tool schema;
- treats repository text as delimited untrusted data;
- owns the candidate ledger and the evidence IDs;
- drops every claim that does not cite evidence it actually produced.

Stage B categories come from fixed Git and AST rules. A model verdict can add an
explanation to an `unexpected` file, but it cannot change the file's category.

### Bounded agent

A Stage A run stops at the first of these limits:

- 25 unique tool executions;
- four iterations without progress;
- 30 candidates;
- an accepted submission;
- an optional token ceiling.

Duplicate tool calls reuse the earlier result. If the model never submits a
supported report, RIPPLE **abstains**: it returns an empty prediction rather than
an unsupported guess.

### Pre-change prediction (Stage A)

`ripple analyze` writes a validated JSON report and a Markdown report, plus a
JSONL trace. The report covers:

- affected components, each with a reason, a confidence, and evidence IDs;
- mapped tests and regression areas;
- an implementation order;
- blind spots.

### Post-change verification (Stage B)

`ripple verify` diffs a Git range against a saved report and classifies each file
or finding:

| Category | Meaning |
| --- | --- |
| `expected` | changed and originally predicted |
| `adjacent` | unpredicted, but one import hop from, or a top-three co-change partner of, a prediction |
| `unexpected` | changed with neither relationship |
| `missing_predicted` | a medium- or high-confidence prediction that was not changed |
| `missing_test` | changed source symbols without a changed, statically mapped test |
| `stale_caller` | an unchanged caller of a function whose signature changed incompatibly |

## Install

You need Python 3.11+ and Git.

```shell
python -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Configuration

Only Stage A and the LLM baselines need a model. Copy `.env.example` to `.env`
(both `.env` and `.ripple/` are gitignored) and set:

```shell
RIPPLE_LLM_API_KEY=...            # never committed, never written to reports
RIPPLE_LLM_BASE_URL=...           # any OpenAI-compatible endpoint
RIPPLE_LLM_MODEL=...
# RIPPLE_MAX_TOKENS=50000         # optional run-wide ceiling
```

## Usage

```shell
ripple scan /path/to/repo                          # index summary (--json, --no-cache)
ripple tool /path/to/repo search_code '{"query":"user auth","limit":5}'
ripple analyze /path/to/repo "Add token refresh support"   # Stage A (--json)
ripple show-run <run-id> --repo /path/to/repo      # replay a trace, no model call
ripple verify /path/to/repo --report latest --range main..feature-branch  # Stage B
```

`ripple analyze` refuses a dirty checkout unless you pass `--allow-dirty`. Reports
go to `.ripple/reports/`, traces to `.ripple/runs/`, and verifications to
`.ripple/verifications/`.

## Interactive Demo

A Streamlit app in [`demo/`](demo/README.md) explains RIPPLE in a few minutes:
the architecture, the seven tools, the trust boundary, the Stage B categories, the
prompts, and the frozen benchmark results.

```shell
python -m pip install -e '.[demo]'
streamlit run demo/app.py
```

- **Guided Replay** steps through three saved runs on a bundled sample repository,
  from feature request to agent trace, candidate ledger, validation, Stage A report,
  real diff, and Stage B findings. The three runs demonstrate soft delete, a stale
  caller, and a missing test with an unexpected change. Replay needs **no API key
  and makes no model calls**. The fixtures come from the real core. Only the model's
  decisions are scripted, and the app says so.
- **Live Analysis** runs the real Stage A, and optionally Stage B, on a Git
  repository on your machine. It needs an OpenAI-compatible model configured through
  the `RIPPLE_LLM_*` variables. Live analysis is for local use; a hosted demo should
  offer replay only.

The final-v1 results shown in the demo are read from
`evaluation/results/final_summary.json`. They are frozen and independent of whatever
model the demo is configured with.

## Benchmark methodology

The held-out benchmark is built from [FEA-Bench](https://huggingface.co/datasets/microsoft/FEA-Bench)
Lite feature-implementation pull requests. Its frozen configuration is
`evaluation/final_config.json` (version `final-v1`), and its hash is recorded in
that file.

- **Tasks.** The manifest `evaluation/data/final_fea_tasks.json` excludes every
  Phase 3–5 development task. It was meant to be stratified by source-file count,
  but every eligible Lite candidate changed 2–4 primary Python source files.
  Larger strata were not available in the eligible pool, so all final tasks are
  small changes.
- **Leak safety.** Each run uses a fresh depth-limited checkout of the exact base
  commit, with no remote and no future refs. The request is the PR title and body,
  with paths masked. A fail-closed structural audit
  (`evaluation/audits/pre_run_leak_audit.json`) passed for every task. A
  post-run audit records future-only symbol names that appear at base, for
  sensitivity analysis.
- **Gold.** Gold is the set of Python source files in the official PR diff. Tests
  are scored separately. Symbol gold is derived from the same diffs only after
  every prediction checkpoint exists.
- **Systems.** There are six systems:
  - B0: BM25.
  - B1: BM25 plus one-hop dependency expansion.
  - B2: BM25 plus co-change expansion.
  - B3: a one-shot LLM call.
  - B4: plain ReAct with the same tools and call ceiling.
  - RIPPLE: the full controller.

  There are also three ablations of RIPPLE:
  - A1: without `co_changed`.
  - A2: without `get_dependencies` and `find_references`.
  - A3: without the report validator.

  Each LLM system ran with requested seeds 17, 42, and 1729. The provider did
  not offer deterministic seed control. All model systems used
  `openai/gpt-oss-20b` through the BullsAI gateway.
- **Metrics.** The headline metrics are set precision, recall, and F1, plus
  Recall@5, Recall@10, MRR, false positives per task, test precision and recall,
  symbol recall, runtime, and tool, model, and token counts.
  - For B0–B2, set metrics use a matched *k*: RIPPLE's confirmed source-file count
    for the same task and seed. An abstention (*k* = 0) scores zero.
  - Seeds are averaged within each task first, then results are averaged across
    tasks.
  - Confidence intervals come from a 95% repository-cluster bootstrap (1,000
    samples, seed 1729), and paired differences from the same bootstrap.
  - Abstentions and provider failures stay in every denominator.
- **Stage B planted anomalies.** Stage B runs on 15 tasks that were selected
  before any results were seen. For each task, the official diff is applied to the
  base checkout (nothing is executed), and four variants are committed:
  - **control:** the diff unchanged;
  - **unrelated:** the diff plus an unrelated new file, which should be flagged
    `unexpected`;
  - **drop_tests:** the diff with its tests removed, which should be flagged
    `missing_test`;
  - **stale_caller:** the diff plus an incompatible signature change to a function
    whose caller is left unchanged (applied only when such a function exists),
    which should be flagged `stale_caller`.

  Each variant is verified twice: once against an oracle Stage A report and once
  against RIPPLE's actual seed-17 report.

## Final held-out results

Everything between the markers below is generated by `ripple build-results` from
saved artifacts. Full tables, confidence intervals, paired differences, provider
reconciliation, and artifact hashes are in
[`evaluation/results/README.md`](evaluation/results/README.md) and
[`evaluation/results/final_summary.json`](evaluation/results/final_summary.json).

<!-- final-results:start -->
Generated by `ripple build-results` from `evaluation/raw/final-v1` (735 checkpoints, 35 tasks, 14 repositories). Model systems are averaged over seeds within task, then over tasks.

| System | P | R | F1 | R@5 | R@10 | MRR | FP/task | Test P | Test R | Symbol R | Runtime (s) | Tools | Calls | Tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B0 | 0.010 | 0.005 | 0.006 | 0.305 | 0.362 | 0.386 | 0.019 | 0.000 | 0.000 | 0.000 | 8.039 | 0.000 | 0.000 | n/a |
| B1 | 0.010 | 0.005 | 0.006 | 0.290 | 0.352 | 0.387 | 0.019 | 0.013 | 0.633 | 0.000 | 8.247 | 0.000 | 0.000 | n/a |
| B2 | 0.010 | 0.003 | 0.005 | 0.319 | 0.343 | 0.360 | 0.019 | 0.015 | 0.271 | 0.000 | 8.226 | 0.000 | 0.000 | n/a |
| B3 | 0.310 | 0.342 | 0.299 | 0.327 | 0.342 | 0.483 | 1.952 | 0.000 | 0.000 | 0.000 | 2.136 | 0.000 | 1.000 | 7630.800 |
| B4 | 0.092 | 0.065 | 0.074 | 0.065 | 0.065 | 0.117 | 0.171 | 0.000 | 0.000 | 0.000 | 3.133 | 0.105 | 1.571 | 3028.819 |
| RIPPLE | 0.029 | 0.011 | 0.016 | 0.011 | 0.011 | 0.029 | 0.000 | 0.010 | 0.010 | 0.001 | 50.088 | 12.943 | 21.333 | 69987.419 |
| A1 | 0.010 | 0.005 | 0.006 | 0.005 | 0.005 | 0.010 | 0.000 | 0.010 | 0.010 | 0.000 | 49.378 | 12.200 | 20.819 | 66134.314 |
| A2 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.010 | 0.019 | 0.000 | 40.316 | 11.362 | 19.390 | 62215.162 |
| A3 | 0.029 | 0.013 | 0.017 | 0.013 | 0.013 | 0.029 | 0.010 | 0.010 | 0.010 | 0.001 | 50.544 | 13.086 | 21.476 | 67542.257 |

Status mix by system (runs):

| System | abstained | completed | partial | provider_failed |
|---|---|---|---|---|
| B0 | 0 | 35 | 0 | 0 |
| B1 | 0 | 35 | 0 | 0 |
| B2 | 0 | 35 | 0 | 0 |
| B3 | 0 | 105 | 0 | 0 |
| B4 | 0 | 105 | 0 | 0 |
| RIPPLE | 102 | 3 | 0 | 0 |
| A1 | 104 | 1 | 0 | 0 |
| A2 | 105 | 0 | 0 | 0 |
| A3 | 100 | 2 | 2 | 1 |

Paired repository-cluster bootstrap (RIPPLE minus baseline, F1 and recall):

| Comparison | Metric | Mean diff | 95% CI |
|---|---|---|---|
| RIPPLE − B0 | recall | 0.006 | [0.000, 0.013] |
| RIPPLE − B0 | f1 | 0.010 | [0.000, 0.019] |
| RIPPLE − B1 | recall | 0.006 | [0.000, 0.013] |
| RIPPLE − B1 | f1 | 0.010 | [0.000, 0.019] |
| RIPPLE − B2 | recall | 0.008 | [0.000, 0.017] |
| RIPPLE − B2 | f1 | 0.011 | [0.000, 0.023] |
| RIPPLE − B3 | recall | -0.331 | [-0.482, -0.206] |
| RIPPLE − B3 | f1 | -0.284 | [-0.393, -0.192] |
| RIPPLE − B4 | recall | -0.054 | [-0.100, -0.012] |
| RIPPLE − B4 | f1 | -0.058 | [-0.110, -0.011] |

Stage B planted anomalies (15 tasks):

| Report | Anomaly | Applicable | Inapplicable | Rate |
|---|---|---|---|---|
| Oracle Stage A | unrelated file → unexpected/unexplained | 15 | 0 | 1.000 |
| Oracle Stage A | dropped tests → missing_test | 15 | 0 | 1.000 |
| Oracle Stage A | stale caller → stale_caller | 15 | 0 | 1.000 |
| Oracle Stage A | control false-alarm rate | 15 | 0 | 0.333 |
| RIPPLE seed-17 report | unrelated file → unexpected/unexplained | 15 | 0 | 1.000 |
| RIPPLE seed-17 report | dropped tests → missing_test | 15 | 0 | 1.000 |
| RIPPLE seed-17 report | stale caller → stale_caller | 15 | 0 | 1.000 |
| RIPPLE seed-17 report | control false-alarm rate | 15 | 0 | 1.000 |

Secondary human-adjudicated RIPPLE precision on the 15 pre-selected tasks: `0.022` (0 false-positive judgments; RIPPLE made no false-positive predictions on the selected tasks, so no human judgment was required and the secondary value equals primary precision on this subset.).

Totals across all 735 runs: 8987 model calls, 29036571 tokens (25198768 input, 3837803 output), 5218 tool calls, 5.943 hours of recorded runtime. Provider failures still in the denominator: 1.
<!-- final-results:end -->

### Reading these results

- **Abstention dominates.** With `gpt-oss-20b`, RIPPLE and its ablations almost
  always stopped on the no-progress or tool-budget limit without submitting a
  supported report, so they abstained. Their set precision, recall, and F1 are
  therefore near zero, and the matched-*k* deterministic baselines inherit the
  same *k* = 0. This frozen model behavior was not tuned after the held-out
  results were seen. The status table above shows the exact counts.
- **RIPPLE did not beat the baselines on this benchmark.** The one-shot LLM
  baseline (B3) scored highest on set and ranked metrics. The plain ReAct baseline
  (B4) also beat RIPPLE, and the paired intervals against B3 and B4 exclude zero.
  RIPPLE's near-zero false-positive rate comes from rarely answering, not from
  ranking well.
- **Ranked metrics are independent of abstention.** Recall@5, Recall@10, and MRR
  use each system's own full ranking. RIPPLE's ranking is its validated submitted
  set, so it is empty when RIPPLE abstains. That is why the lexical baselines
  (B0–B2) lead on ranked metrics.
- **Stage B is deterministic.** The oracle rows measure the verifier on its own.
  The RIPPLE-report rows show what verification does when Stage A abstained. An
  empty prediction makes every changed file look `unexpected`, so those rows are
  not a measure of verifier quality.

## Reproducing

```shell
ripple build-results            # regenerates every number above from evaluation/raw/final-v1
```

Running `ripple build-results` is byte-deterministic. Rerunning predictions
(`ripple evaluate-final`) needs network access to GitHub and the model gateway.
It resumes from the checkpoints in `evaluation/raw/final-v1` and refuses a config
whose hash does not match. Stage B is `python evaluation/run_stage_b.py`. Before running it against RIPPLE
reports, extract `evaluation/raw/final-v1-reports-and-traces.tar.gz` into
`.ripple/evaluation/final-v1/`. The
post-prediction gold/audit/adjudication step is `python evaluation/post_run.py`.
Provenance for the development phases (Phases 3–5) is in
[`evaluation/README.md`](evaluation/README.md). Those phases are development
results, not held-out results.

## Limitations

- **Python only.** RIPPLE analyzes Python repositories only.
- **Static analysis only.** Target repository code and tests are never imported
  or executed. Dynamic Python behavior can hide dependencies, and a static test
  mapping is not runtime coverage.
- **Small benchmark.** Only the tasks listed above qualified, all of them 2–4
  source-file changes from a limited set of repositories, so confidence intervals
  are wide. Medium and large changes are untested.
- **One historical diff as gold.** A historical PR is one valid implementation,
  so exact-diff precision can penalize reasonable alternatives. The secondary
  human-adjudicated precision exists for that reason.
- **Model-dependent.** Results depend on the model and provider. Under
  `gpt-oss-20b`, RIPPLE's conservative controller mostly abstained, which means
  it was precise only when it answered at all.
- **No contamination split.** No recent-PR contamination split is claimed,
  because no authoritative training cutoff for `gpt-oss-20b` could be established.
- **Provider outages.** BullsAI gateway outages caused provider failures. Original
  failure artifacts are preserved under `evaluation/raw/final-v1-provider-*`. Only
  the exact failed checkpoints were retried, and any checkpoint that still failed
  is counted as a provider failure.

## Development

```shell
pytest -q
ruff check .
ruff format --check .
```
