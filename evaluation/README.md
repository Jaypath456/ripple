# RIPPLE Phase 3 evaluation

This directory contains the fixed ten-task development evaluation for the
deterministic B0 and B1 change-surface baselines. It is development data, not a
held-out test set.

## Provenance and reconstruction

The task IDs, repositories, pull numbers, and base commits come from the
official [Microsoft FEA-Bench dataset](https://huggingface.co/datasets/microsoft/FEA-Bench/tree/55ee2f78126a3ecdeac6f595fa1ba6ae5c600bad),
revision `55ee2f78126a3ecdeac6f595fa1ba6ae5c600bad`, restricted to IDs in the
official Lite list. The reconstruction design was checked against the official
[microsoft/FEA-Bench repository](https://github.com/microsoft/FEA-Bench/tree/fb3c11274796f6057bf447410540fcf2fa1d90b1),
commit `fb3c11274796f6057bf447410540fcf2fa1d90b1`.

The published lightweight table intentionally contains only essential fields.
For each selected row, RIPPLE read the referenced public GitHub pull request:

- request input: PR title plus PR body only;
- head identity: PR head SHA, used only by leak checks and never fetched for
  prediction;
- gold paths: the official GitHub PR `.diff`, matching FEA-Bench's own
  `extract_patches` source;
- prediction state: the exact published `base_commit`.

RIPPLE did not run FEA-Bench's reconstruction script, install FEA-Bench, execute
target code, import target modules, install a target repository, or run its
tests. The checked-in JSON manifest is the complete frozen input needed for
future runs.

## Fixed development set

Selection was fixed before baseline execution. Candidates had to be merged,
publicly reconstructable, have a usable title/body, contain 2–20 primary Python
source files under RIPPLE's classifier, and pass leak-safe preparation. The
planned preference was four tasks with 2–4 source files, three with 5–9, and
three with 10–20. Screened Lite candidates did not provide eligible 5–20-source
patches; some two-dot base/head comparisons appeared larger because of branch
drift, but the authoritative FEA-Bench PR diff was small. The fallback therefore
uses ten 2–4-file tasks from ten repositories rather than changing the gold
definition or admitting one-file tasks.

| Task ID | Repository | Source | Tests |
| --- | --- | ---: | ---: |
| `PyThaiNLP__pythainlp-1054` | `PyThaiNLP/pythainlp` | 2 | 1 |
| `RDFLib__rdflib-1968` | `RDFLib/rdflib` | 3 | 1 |
| `Textualize__rich-901` | `Textualize/rich` | 3 | 3 |
| `aws-powertools__powertools-lambda-python-5588` | `aws-powertools/powertools-lambda-python` | 3 | 2 |
| `aws__sagemaker-python-sdk-3432` | `aws/sagemaker-python-sdk` | 2 | 3 |
| `conan-io__conan-12887` | `conan-io/conan` | 2 | 1 |
| `embeddings-benchmark__mteb-1256` | `embeddings-benchmark/mteb` | 2 | 1 |
| `google-deepmind__optax-632` | `google-deepmind/optax` | 3 | 1 |
| `roboflow__supervision-245` | `roboflow/supervision` | 3 | 1 |
| `softlayer__softlayer-python-2073` | `softlayer/softlayer-python` | 3 | 1 |

Examples excluded during screening for the one-source-file rule include
`RDFLib__rdflib-968`, `Textualize__rich-1706`,
`Textualize__rich-1894`, `Textualize__rich-376`,
`docker__docker-py-1230`, `deepset-ai__haystack-6758`,
`lark-parser__lark-1467`, `pydicom__pydicom-1648`, and
`pypa__hatch-211`. No selected task failed reconstruction or evaluation.

## Leak prevention

Every run creates a fresh evaluation-owned Git repository, fetches only the
exact base SHA with depth one and no tags, checks it out detached, removes the
remote, and deletes every retained Git ref. The fail-closed audit then verifies:

1. `HEAD` is exactly the task's base commit;
2. the worktree is clean;
3. no target PR/head ref exists and the target head is not reachable from a
   retained ref;
4. gold-only paths absent at base are not present in the worktree; and
5. no full gold path or gold filename survives in the masked request.

Only Git commands and static file reads are used. A deliberately contaminated
future branch, a wrong `HEAD`, and a visible gold-only file all fail automated
tests.

## Request masking and gold taxonomy

The deterministic sanitizer replaces explicit `.py`, `.md`, `.rst`, JSON,
YAML, and TOML filenames, slash-separated paths, dotted names with three or more
components, and dotted module names following `from` or `import` with `[PATH]`.
It preserves normal prose, version numbers, and HTTP(S) URLs. Both original and
masked requests remain in the manifest; only the masked request reaches B0/B1.

Changed files are split into Python source, Python tests using RIPPLE's existing
test convention, other implementation files, and ignored documentation,
changelogs, lockfiles, and obvious generated artifacts. Primary metrics use
Python source only. Tests are scored separately.

## Frozen baselines

The primary prediction cutoff is `DEFAULT_PREDICTION_K = 5`; it never depends
on gold size. Full rankings are saved, so a later phase can rescore at an
external cutoff without rerunning a baseline.

**B0 — BM25.** Run the Phase 2 BM25 corpus over the masked request, sum every
positive file/symbol/string document score by non-test source file, add all
unmatched source files at score zero, deduplicate, and rank by descending score
then path.

**B1 — BM25 plus dependency structure.** Take the top five B0 source seeds and
their one-hop runtime importees and importers. Begin with each B0 score. A
non-seed one-hop candidate receives one bounded bonus of
`0.25 * max_seed_score`; every candidate receives the modest fan-in adjustment
`0.01 * max_seed_score * min(fan_in, 10) / 10`. Rank by descending final score
then path. Tests statically mapped to seeds or expanded candidates are kept in a
separate ranking ordered by their best associated source rank. These formulas
were fixed before aggregate results were observed.

## Metrics and uncertainty

At cutoff `k=5`, precision is `TP / predicted`, recall is `TP / gold`, and F1 is
their harmonic mean; zero denominators produce zero. Recall@5 and Recall@10 use
the full source ranking. MRR is the reciprocal rank of the first gold source
file, or zero. False positives are counted in the top-five source set. Test
precision and recall use the separate top-five test ranking.

Aggregates are arithmetic means of per-task metrics, never pooled file counts.
Ninety-five-percent percentile intervals use 1,000 deterministic bootstrap
resamples with seed 1729. Repositories are sampled as clusters and all their
tasks travel together. Since this development set has one task per repository,
cluster resampling is equivalent to task resampling here. Ten tasks are far too
few for strong inferential or significance claims.

## Development result

The required run completed with 10/10 valid tasks, zero skipped tasks, and zero
setup failures.

| Baseline | P | R | F1 | R@5 | R@10 | MRR | FP/task | Test P | Test R | Time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B0 | 0.100 | 0.167 | 0.125 | 0.167 | 0.350 | 0.158 | 4.500 | n/a | n/a | 7.648s |
| B1 | 0.120 | 0.217 | 0.154 | 0.217 | 0.283 | 0.171 | 4.400 | 0.060 | 0.167 | 7.622s |

B1 improved the fixed-cutoff precision, recall, F1, and MRR, but reduced mean
Recall@10 from 0.350 to 0.283. The result was retained without tuning. Exact
per-task rankings, reasons, scores, metrics, runtimes, leak checks, aggregates,
and bootstrap intervals are in
[`results/dev_baselines.json`](results/dev_baselines.json).

Run it again from the repository root with:

```shell
ripple evaluate \
  --tasks evaluation/data/dev_tasks.json \
  --output evaluation/results/dev_baselines.json
```

Fresh clones default to `.ripple/evaluation/repos/`, already ignored by Git.
The result writer refuses to overwrite an incompatible schema.

## Limitations and boundary

A historical PR is one valid implementation, not necessarily the only valid
implementation. Exact-diff precision can therefore penalize reasonable
alternatives. PR bodies can contain design-level API examples even after path
masking. The development set lacks the desired medium and large source-change
strata, contains only ten tasks, and is not held out. Static import resolution
and test mapping are conservative, and baseline runtime varies by repository
size.

Phase 3 ends at B0/B1 deterministic development evaluation. There are no LLM
calls, API keys, agent controller, FeatureIntent, ledger, traces, B2–B4,
verification stage, final benchmark, UI, or MCP integration here.

## Phase 4 MVP extension

`data/mvp_tasks.json` freezes a 20-task development set: all ten tasks above plus
ten additional eligible FEA-Bench Lite tasks, each from a distinct repository
and each changing 2–4 primary Python source files. The added task IDs are
`Project-MONAI__MONAI-465`, `astropy__astropy-16135`, `boto__boto3-74`,
`falconry__falcon-640`, `graphql-python__graphene-1506`,
`pgmpy__pgmpy-1753`, `prometheus__client_python-302`,
`sphinx-doc__sphinx-9131`, `sqlfluff__sqlfluff-3937`, and
`tobymao__sqlglot-1252`.

The agent receives only the exact base checkout and masked request. Gold paths,
the target head/diff, baseline rankings, signatures, and metrics are not passed
to it. Validated component symbols are converted to their owner paths,
deduplicated in report order, and filtered to source files; suggested tests are
scored separately. B0 and B1 use the resulting `agent_k` as their primary
cutoff. If `agent_k` is zero, all three systems receive zero set precision,
recall, and F1 while full-ranking R@5, R@10, and MRR remain auditable.

Run a one-task real-provider smoke test before the full set:

```shell
ripple evaluate-agent --task-limit 1 --verbose \
  --output .ripple/evaluation/smoke.json
ripple evaluate-agent --verbose \
  --output evaluation/results/mvp_agent_results.json
```

The v1.1 result records `AGENT_CONFIG_VERSION = "mvp-v1.1"`, per-system metrics,
agent status, tool/LLM/token counts, dropped-claim count, stop reason, runtime,
and report/trace paths. For evaluation only, masked requests longer than 2,000
Python characters are deterministically prefix-truncated once; the exact same
prepared text is supplied to B0, B1, and RIPPLE. Per-task results record the
original length, used length, and truncation flag. This remains development-only
evidence; historical PRs are one implementation rather than the only valid
implementation.
