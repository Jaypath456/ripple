# RIPPLE delivery — 2026-10-28 (America/New_York)

RIPPLE runs end to end offline with traceable predictions, an unsupported-request
abstention, and deterministic diff verification. The original benchmark is
reproduced. The revised controller's real-model accuracy is **unmeasured**.
All changes remain uncommitted; the user's checkout received no commits or pushes.

## 1. Baseline and preexisting state

- Checkout: `main`, `358dbff3f63b1ed3b1048d5c032f66702cee0f6f`; initial
  `git status --short` and `git diff --stat` were empty. No preexisting work was
  overwritten. No `AGENTS.md`, separate architecture/design documents, or CI
  workflow was found in the checkout or its ancestor directories. README,
  evaluation documentation/configuration, pipeline modules, and all existing
  test modules were inspected.
- Default `python3` was 3.15.0 with none of the project dependencies installed.
  A project `.venv` was created with available Python **3.13.16**. macOS arm64,
  Git 2.50.1. Declared dependencies only: OpenAI 3.41.0, Pydantic 2.14.1,
  pytest 9.2.0, Ruff 0.17.3; build dependency setuptools 84.2.1.
  [Environment and full versions](evidence/environment.json).
- No `OPENAI_API_KEY`, `RIPPLE_LLM_API_KEY`, model, endpoint, `.env`, or
  `.env.local` was configured. No credential was created and **zero live
  provider calls** were made. The API credential skill was applied under the
  user's explicit existing-credentials-only constraint.
- Initial install failed because sandbox DNS could not resolve PyPI. A network
  escalation for declared dependencies succeeded. The subsequent isolated
  installation used cached wheels with package-index access disabled.
- Baseline: **170 tests passed in 31.17 s**, Ruff check passed, all 62 original
  Python files formatted. Existing tests already covered malformed Python,
  unknown tools, dirty checkouts, graph direction, static test mapping, gold
  leak protection, provider-free scripted runs, and Stage B anomalies. These
  were not treated as newly discovered bugs.

## 2. Verified defects and reproductions

The same new security cases were run against an archived copy of the original
commit and current code, with the same interpreter/dependencies. Original:
**16 failed / 11 passed**; current: **27 passed**.
[Original failure summary](evidence/sanitized/security_baseline.json),
[current security and resilience results](evidence/security_current.txt).

| Severity | Defect and cause | Reproduction / correction |
| --- | --- | --- |
| P1 | Scanner, lexical search, facts and dirty-cache hashing could follow tracked symlinks outside the repository. | Track `leak.py` pointing to a sibling Python file. Original index contains its external symbol. Shared bounded reads reject source symlinks, including directory components. |
| P1 | `.ripple` or its cache directories could redirect writes outside the repository. | Track `.ripple` as a symlink to a sibling directory, then scan with caching. Original writes there. Cache, default analysis directories and verification directories now reject symlink paths. |
| P1 | Fixed Git diff commands could execute a configured text-conversion program. `--no-ext-diff` alone did not disable it. | `.gitattributes` selects `diff=hostile`; local `diff.hostile.textconv='touch EXECUTED'`. Original creates the marker during `parse_diff`. Both diff commands now explicitly disable text conversion and external diff programs. |
| P1 | Failed tool invocations were attributed touched targets, and ledger/validator did not independently require successful evidence. | A failed `inspect_symbol` result could support confirmation and report validation for an indexed target. Failed results now touch nothing; ledger and validator independently reject them. |
| P2 | One supported target could carry unsupported additional targets in narrative claims, risks, or test coverage. | Cite evidence touching auth while also claiming the unrelated service. Require support for each claimed target and successful test evidence. |
| P2 | Proposed test paths could escape the repository. This was a misleading report, not an observed write primitive. | `action=add`, `test_path=../../outside.py` previously survived validation. Absolute/traversal suggestions are dropped. |
| P2 | Line-oriented name-status parsing used Git's quoted filenames as real paths. Pathspec syntax could hide hunks; file-to-symlink changes raised an unsupported-record error. | Tabs, newlines, quotes, non-ASCII and `:(literal)` filenames; type-change diff. Use NUL-delimited records, literal pathspecs, and retain type changes as modifications. |
| P2 | Rejected ledger updates were silently invisible to the next model decision. The protocol omitted crucial transition timing/examples. | Submit a stale ID; original next prompt contains no rejection reason. New prompts contain explicit rejection codes, previous-observation timing, exact current evidence ID, and transition examples. No candidate is automatically confirmed. |
| P2 | Token ceiling was checked only in the exploration loop, allowing interpretation/report repair calls after reported usage exceeded it. | Meter an invalid first interpretation or report above the ceiling. New regression proves no repair request starts; status is abstained/partial with `token_ceiling`. |
| P2 | Partial provider output could contain parseable JSON and be treated as complete. Negative/nonfinite Retry-After values could cause invalid sleeps. | Mock Responses `incomplete` and Chat `length`, plus `-1`, `NaN`, `Infinity` headers. Incomplete payloads cannot enter structured validation; invalid delays are ignored. |
| P2 | Partial provider usage could be reported as zero total tokens. | Supply input usage without output/total. Totals now remain unknown; known accumulated usage is only a lower bound. |
| P1 | Evaluation setup unconditionally deleted an existing task directory, including user data. | An unmarked task directory with a sentinel was removed before a fixture fetch failure. Setup now requires a matching ownership marker, unchanged base commit and clean worktree before replacing an evaluation checkout. A local clone regression also verifies safe reruns and preservation of later user edits. |
| P2 | `evaluate-agent` referenced `args.force`, which its parser never defined and its evaluator does not accept. | Credential-free mocked CLI regression. Remove the unsupported argument. |

Resource resilience: original source reads had no byte/line bounds. The revised
scanner records explicit per-file errors above 1 MiB or 16,384 characters/line,
and catches recursive extraction failures. Inspection excerpts are bounded at
120 lines / 12,000 characters; prompts fail explicitly above 120,000 characters.
These are conservative product limits, **not a complete memory sandbox**.

## 3. Changes and engineering rationale

- [Agent](../src/ripple/agent.py), [ledger](../src/ripple/ledger.py),
  [report models](../src/ripple/agent_models.py): structured counters, precise
  rejection feedback, final candidate snapshots, sanitized tool payloads in
  traces, prompt size checks, complete token accounting and request-boundary
  ceilings. Config version is `full-report-v2`; older saved reports still load.
  The original trace stored IDs but omitted payloads, preventing later semantic
  evidence audits. New traces retain the deterministic observations.
- [Validator](../src/ripple/validate.py): successful evidence and per-target
  support checks; safe proposed test paths. Validation remains mandatory for
  production RIPPLE. The original explicitly unsafe A3 research ablation is
  preserved without being exposed as a product mode.
- [Source reader](../src/ripple/source.py),
  [scanner](../src/ripple/scanner.py), [cache](../src/ripple/cache.py),
  [search](../src/ripple/search.py), [facts](../src/ripple/facts.py),
  [tools](../src/ripple/tools.py): consistent safe reads and visible skipped-file
  reasons. Index schema 4 forces rebuilding indexes with previous unsafe read
  semantics. Tracked generated files remain in scope within these limits.
- [Evaluation setup](../src/ripple/evaluation.py): ownership and clean-state checks protect preexisting directories. Older unmarked workspaces are refused with instructions to choose a fresh workspace. This changes setup safety, not benchmark tasks, gold or scoring.
- [Diff parser](../src/ripple/diffing.py),
  [verification](../src/ripple/verification.py): literal paths, safe commit
  option termination, disabled text conversion, preserved unusual names/type
  changes, and hooks disabled for temporary verification checkouts.
- [Provider boundary](../src/ripple/llm.py): completion status and retry-delay
  handling. Responses-to-Chat fallback still occurs only for a 404. Provider
  authentication failures do not trigger speculative alternate requests.
- [CLI](../src/ripple/cli.py), [demo](../src/ripple/demo.py),
  [README](../README.md): explicit offline verification, runnable scripted demo,
  and correct environment export instructions. `.env` is not automatically read.
- [CI](../.github/workflows/ci.yml): proposed Python 3.11/3.13 offline tests and
  Ruff checks on Ubuntu, with read-only repository permission and no inference.
  It has **not run on GitHub** because changes have not been pushed.

No retrieval-ranking rewrite or automatic promotion was justified by the
recorded evidence. No provisional suggestions were added to the scored prediction
set. Regression areas remain separate from affected components.

## 4. Verification commands and results

Run from the repository root with the project venv:

```shell
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pip check
.venv/bin/pytest -q tests/test_delivery_security.py tests/test_delivery_resilience.py
.venv/bin/python evaluation/diagnose_abstentions.py --output docs/evidence/abstentions.json
.venv/bin/python evaluation/stress_index.py --output docs/evidence/sanitized/stress_current.json
.venv/bin/ripple demo --output .ripple/demos
```

| Check | Result |
| --- | --- |
| Complete current suite | **219 passed**; [full log](evidence/tests_current.txt) |
| New security/resilience suite | **49 passed**, including scripted injection attempts, provider failures, retry bounds, unknown usage, cancellation and token ceilings |
| Ruff check / format | Pass; 71 Python files formatted |
| Compile / dependency consistency | Pass; no broken requirements |
| Original security regressions on `358dbff` | Expected failure: 16 failed, 11 passed; these are matched defect reproductions, not benchmark tasks |
| Frozen result regeneration | Pass; original README, results README and summary hashes unchanged before/after generation |
| Fresh snapshot / new venv | Install, `pip check`, SDK construction without requests, Ruff, all tests and demo passed |
| Type checker | No dedicated type-checker/configuration exists; none was added or claimed |

The [fresh install record](evidence/sanitized/fresh_install.json) includes exact commands,
outputs and timings. This is a snapshot of current **uncommitted** source, not a
claim that a remote clone already contains these changes. Reproduce with:

```shell
.venv/bin/python -m pip wheel --wheel-dir .ripple/wheels '.[dev]' 'setuptools>=68'
.venv/bin/python evaluation/check_fresh_install.py \
  --wheels .ripple/wheels --output .ripple/fresh-install.json
```

Wheel preparation uses package access; the fresh install itself sets
`PIP_NO_INDEX=1`. The script creates a new temporary snapshot and venv, strips
provider environment variables, copies no local secrets/state, and preserves
the result for inspection. Local testing used Python 3.13/macOS only; the
proposed 3.11/Linux CI matrix remains unexecuted.

## 5. Benchmark, root cause, before/after and costs

Frozen labels, definitions, task membership, seeds, schedule and raw checkpoints
were not changed. `ripple build-results` regenerated the original outputs
byte-for-byte before README setup edits:
[hash evidence](evidence/frozen_regeneration.json). The independent
[diagnostic audit](evidence/abstentions.json) recomputes set scores using the
existing scorer, averaging seeds within task before averaging tasks.

| Measurement | Historical baseline | Revised controller |
| --- | --- | --- |
| Sample | 35 held-out tasks × 3 requested seeds | No live model runs |
| Stage A precision / recall / F1 | 0.028571 / 0.011111 / **0.015873** | **Unmeasured** |
| Abstention | 102/105 (97.14%) | Unmeasured on benchmark |
| One-shot B3 F1, same tasks/seeds | **0.299437** | Not rerun |
| Component IDs traceable to successful tools | 3/3 | 1/1 in the scripted positive fixture only |
| Mean Stage A recorded latency | 50.088 s/run | No comparable live latency |
| Recorded RIPPLE workload | 2,240 model operations; 7,348,679 tokens (6,391,770 input / 956,909 output) | Zero paid calls/tokens in this delivery |
| Stage B oracle saved controls | 5/15 false alarms; 15/15 detections for each planted anomaly | Controlled demo: clean control, stale caller detected |
| Stage B actual seed-17 saved controls | 15/15 false alarms with near-empty Stage A | Historical 15-task suite not rerun |

The ID rates above establish provenance, **not semantic entailment or prediction
correctness**. Frozen Stage B metrics were recomputed from saved classifications;
they are not measurements of the revised verifier. No general Stage B accuracy
rate can be inferred from the two demo variants. Full historical tables, per-case
scores, confidence intervals and provider reconciliation remain in
[the frozen summary](../evaluation/results/final_summary.json).

The dominant *recorded* failure is demonstrated, not inferred from README prose:

- **83/105 runs proposed no ledger updates.** With no accepted confirmation,
  the controller cannot produce affected components.
- 29 updates were proposed across 2,116 decisions; **25 were rejected** and four
  accepted. **101/105 runs accepted no update.**
- 85 runs stopped for no progress, 16 for tool budget, four submitted. All **215**
  submission rejections said no candidates were confirmed.
- Of four submissions, three retained components. The fourth, supervision-1296
  seed 17, proposed an existing symbol as `new_file`; the validator correctly
  dropped it. This accounts for the 102nd abstention.
- Only 13 decision-schema errors, two intent-schema errors and 37 invalid tool
  calls were logged. They are real failures, but do not explain most abstentions.

The audit matches **exact checkpoint-referenced archive members**. The archive
also contains superseded retries; counting every trace produces incorrect
denominators. Per-case entries preserve status, stop reason, updates, validation
errors, dropped claims, metrics, workload and sanitized rejection samples.

Other causes were investigated with these limits:

| Area | Finding |
| --- | --- |
| Retrieval | Frozen B0 R@5=0.305, R@10=0.362 shows incomplete lexical recall. Its near-zero matched-k set score is partly inherited from RIPPLE abstention. Historical agent traces omit search payloads, so exact agent retrieval recall is unrecoverable. |
| Scanning and dependency direction | Tracked-Python coverage, relative imports, reverse dependencies, type-only imports and test mappings pass existing tests and the fixture. External symlink ingestion was reproduced and fixed. No proof of complete coverage in all 35 historical repositories is claimed. |
| Context and provider handling | 15/105 RIPPLE requests were prefix-truncated to the frozen 2,000-character evaluation limit. That definition is unchanged. Provider fallback, incomplete responses, invalid JSON/schema, failure, retries, cancellation and token limits are tested offline. |
| Candidate acceptance | Exact IDs, target validity, suspicion-before-confirmation and latest-observation constraints explain concrete rejected examples. Historical traces lack detailed rejection reasons; the audit does not invent them. New runs record these reasons and tool payloads. |
| Task identifiability | Manual diagnostic inspection found some informative requests (e.g. Model constraint properties) and some brief/indirect requests (e.g. little-h equivalency). A PR's prose and one historical implementation do not prove every gold file is inferable. No label or request was edited to resolve this ambiguity. |

The prompt/feedback correction is a testable research hypothesis for improved
live coverage. The fixed scripted positive path already worked in the baseline;
the new demo is **not an accuracy improvement claim**. Security regressions show
measurable safety improvement on identical inputs, not a replacement F1 score.

Future matched comparisons must run `358dbff` and the revised source in separate
workspaces on identical manifests/seeds/provider settings, preserving existing
final-v1 artifacts. Use development tasks for further iteration; final tasks
inspected in this audit are no longer a pristine development-blind holdout for
this intervention. A further untouched evaluation split is needed for a fresh
generalization claim, without changing existing split membership.

Before a paid rerun, price a development smoke against the configured provider
and obtain approval. Historical RIPPLE workload is roughly 7.35 million tokens;
all 735 systems/ablations used 8,987 model operations and 29.04 million tokens.
Current feedback may increase context cost. Dollar cost is unknown without
provider pricing; there was no new inference charge in this session. Recorded
model counts are application operations, not exact HTTP attempts including
fallback/retries. The 25-tool ceiling counts exploration; new diagnostics also
count deterministic expansion and total tool executions.

## 6. Prompt injection, repository scale and resilience

Injection regressions place malicious closing delimiters/instructions in README,
comments and docstrings, then supply adversarial model decisions attempting shell
execution, external file reads and fabricated evidence. The production registry,
path checks and validator yield abstention, preserve fixture source and create
no command marker. Existing Stage B tests also reject fabricated verdict evidence
and preserve deterministic categories. These are **scripted boundary tests**,
not measured live-model attack success rates. A model can still overlook or
reject a relevant target; provenance checks do not establish completeness or
truth of arbitrary natural-language reasoning.

The bounded synthetic repository contains 300 small modules, tracked generated
Python, one 2 MiB file, one 32 KiB line, malformed/binary Python, mixed-language
files and an external symlink. Target code is never executed.

| Single-process sample | Original commit | Current |
| --- | ---: | ---: |
| Tracked Python files | 305 | 305 |
| Indexed symbols | 301 (includes external symbol) | 300 |
| Visible per-file errors | 2 | 5 |
| Scan time | 0.201 s | 0.127 s |
| Search corpus build | 0.042 s | 0.016 s |
| Query | 0.0037 s | 0.0012 s |
| Peak process RSS | 42.03 MiB | 45.06 MiB |

[Original measurement](evidence/sanitized/stress_baseline.json),
[current measurement](evidence/sanitized/stress_current.json). These are single noisy
samples on the same machine, not a performance significance test; **memory did
not improve in this sample**. Limits cap individual reads but do not bound the
total number of files, retained AST/index size, Git diff output or total RSS.
Untracked generated folders are naturally excluded by Git tracking; tracked
generated files are not silently excluded. Empty Python corpora, malformed
syntax, invalid/unknown encodings, massive lines/files, model failure and
KeyboardInterrupt have concrete regressions. Cancellation propagates rather
than writing a misleading completed report.

## 7. Demo inputs, commands, outputs and limitations

```shell
.venv/bin/ripple demo --output .ripple/demos
```

Each invocation creates a fresh `invoice-*` directory; only its fixture gets Git
commits. The output provides `repo`, `report`, `trace`, `control_range` and
`anomaly_range`. Inspect the generated Markdown and evidence, then substitute
those values below:

```shell
.venv/bin/ripple scan <repo> --no-cache
.venv/bin/ripple tool <repo> inspect_symbol '{"target":"billing.py::subtotal"}'
.venv/bin/ripple show-run <report-id> --repo <repo>
.venv/bin/ripple verify <repo> --report <report> --range <control_range> --offline
.venv/bin/ripple verify <repo> --report <report> --range <anomaly_range> --offline
```

Request: “Round invoice subtotal to two decimal places before checkout displays
it.” Expected affected component `billing.py::subtotal`; checkout is a reverse
dependency and invoice tests are statically mapped. Evidence `e2` contains the
original function source. The actual implementation uses `round(sum(prices), 2)`
and updates the mapped test. Verification produces `expected` for billing and
`adjacent` for its test, without a missing-test/stale-caller alarm. A separate
required `currency` argument leaves `checkout.py` unchanged and produces
`stale_caller`. The weather request abstains.

Preserved [positive report](evidence/demo/report.json),
[readable report](evidence/demo/report.md), [full trace](evidence/demo/trace.jsonl),
[negative report](evidence/demo/negative_report.json),
[control](evidence/demo/control.json), [anomaly](evidence/demo/anomaly.json),
[summary](evidence/demo/summary.json). Workspace paths in these copies are
sanitized; rerun the demo for usable local paths. Proposals come from
`ScriptedLLM`; evidence, validation, expansion and Stage B execute production
code. This demonstrates plumbing and the trust boundary, not live inference.

Live analysis uses README's environment exports and `ripple analyze <repo>
"<request>"`; by default it refuses tracked dirty work. Model-free verification
can always be requested with `--offline`. Inspect report status, stop reason,
dropped claims, blind spots and candidate diagnostics before acting on results.

## 8. Remaining risks, ranked

- **P0:** None observed in the tested scope; this is not an exhaustive security
  certification.
- **P1:** Revised real-model usefulness, latency, dollar cost and prompt-injection
  behavior remain unmeasured. The historical baseline's abstention crisis is not
  claimed solved. Missing predictions can also degrade Stage B usefulness.
- **P2:** Natural-language claim correctness and completeness are not certified
  by touched-target provenance. Test mappings are static and can overapproximate;
  source modules without symbols are awkward under the reference-check gate.
  Changed callers are conservatively treated as addressed without executing them.
- **P2:** No aggregate index/AST/diff memory bound; adversarial scale or concurrent
  filesystem mutation can still cause resource exhaustion or check/read races.
  Source reads use no-follow/nonblocking flags where available, but parent-path
  checks are not a race-proof capability filesystem. Working trees should remain
  stable during a run.
- **P2:** Saved local indexes/reports are not cryptographically authenticated.
  New traces contain repository source; configured API key values are redacted,
  but arbitrary secrets already committed into target source are not detected.
  Audit full traces accordingly. Non-UTF-8 Git filenames and all possible global
  Git extensions/filters have not been exhaustively tested.
- **P2:** Token ceiling is soft and depends on provider usage; HTTP retry/fallback
  costs can be unknown. Stage B model investigations retain their existing
  per-finding limits rather than a new global paid-token ceiling.
- **P3:** Dependency ranges remain broad and unpinned; exact tested versions are
  recorded. Linux/3.11 CI and Windows behavior are not locally validated. Very
  large Python files/symlinked source are deliberately incomplete coverage and
  require reading the reported blind spots.

## 9. Blocked or deferred investigations

- Live provider smoke, matched current 35-task × 3-seed evaluation, current
  valid-evidence rate, and live injection trials: no configured credentials.
  Large paid runs additionally require an estimated cost and explicit approval.
- Historical retrieval and semantic rejection replay: old traces lack tool
  payloads and exact historical repository indexes. New instrumentation fixes
  future observability, not the missing past data. Historical base checkouts were
  not fetched and the 15-task Stage B suite was not rerun.
- Strong generalization, runtime test coverage, dynamic imports, medium/large
  feature tasks and multi-language support: outside the demonstrated Python
  static-analysis scope. No gold adjustment or speculative rewrite was used.
- Remote CI execution requires publishing these uncommitted changes, which was
  not authorized and was not performed.

## 10. Changed files and diff summary

The [working-tree change record](evidence/changes.json) lists every modified/new
file and the tracked diff statistics. Implementation is concentrated in the
pipeline modules linked above plus two new modules (`source.py`, `demo.py`), two
regression modules, three diagnostic/install/stress scripts, documentation and
the proposed CI workflow. Three existing assertions were updated solely for
the explicit report/index version bumps.

`evaluation/final_config.json`, task manifests, schedules, gold labels/diffs,
existing raw checkpoints, `evaluation/results/README.md` and
`evaluation/results/final_summary.json` have no working-tree changes. Generated
benchmark tables inside README are preserved. Ignored venv, wheelhouse, original
snapshot and local demo artifacts remain available under `.venv/` and `.ripple/`.
No reset, deletion of user data, commit, push or external message was performed.

## Architecture-review publication copies

Local diagnostic originals are preserved and excluded from staging. Linked
installation, stress and final-check records under `evidence/sanitized/` replace
local path prefixes with placeholders; the baseline security log is represented
by a result summary. These publication copies preserve outcomes and metrics and
record the SHA-256 of each original. The earlier delivery snapshot and its
no-commit statements above describe that delivery, not the later review-branch
preparation; see [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md).
