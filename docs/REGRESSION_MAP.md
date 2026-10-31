# Delivery improvements and offline evidence

`S` = `tests/test_delivery_security.py`; `R` =
`tests/test_delivery_resilience.py`. Test identifiers below omit the common
`test_` prefix. `F → P` means the archived 358dbff implementation failed and
delivery passed. `P → P` is useful coverage, not a demonstrated new fix.
Per-parameter outcomes are in the two test-outcome JSON files.

| Change | Demonstrating tests | Finding / port boundary |
| --- | --- | --- |
| Reject tracked source symlinks | S `tracked_symlink_does_not_index_external_source` | F → P. Shared source primitive can port independently; search/facts/dirty hashing use it, but separate attacks on each caller are not covered by this test. |
| Reject symlinked state/cache directories | S `state_directory_symlink_cannot_redirect_cache_writes` | F → P. Cache defense demonstrated. Additional analysis/verification output-directory guards are visible in source but lack their own matched regression. |
| Preserve unusual diff paths | S `diff_preserves_unusual_git_paths` | Five parameters F → P. NUL records and literal pathspecs can port independently. |
| Preserve file-to-symlink changes | S `file_to_symlink_change_stays_visible` | F → P. Deterministic diff change can port independently. |
| Disable Git text conversion | S `diff_never_executes_textconv` | F → P; baseline actually creates a marker in an isolated synthetic repository. Independent security port. |
| Reject failed evidence | S `failed_tool_result_cannot_confirm_or_validate` | F → P. First failing baseline assertion is touched-target attribution; ledger/validator defenses are also inspected in source. Port into the V2 ledger manually and rerun its protocol tests. |
| Require support for every claim/test/risk target | S `one_supported_target_cannot_launder_unrelated_claims` | Three parameters F → P. Validator hardening; changes accepted report content and requires V2 compatibility checks. |
| Reject test-path traversal | S `proposed_test_path_cannot_escape_repository` | F → P. Report validation defense, not an observed write exploit. |
| Preserve unowned/edited evaluation checkouts | S `evaluation_preserves_unowned_existing_checkout`, `owned_evaluation_checkout_resumes_but_preserves_user_edits` | Both F → P. Evaluation-harness defense can port independently. |
| Expose ledger rejection reasons and transition timing | R `rejection_reason_reaches_next_prompt_and_trace` | F → P. Changes model input and prediction trajectory; do not transplant wholesale over V2 checkpoints. |
| Enforce soft token ceiling at every request boundary | R `token_ceiling_also_blocks_interpretation_retry`, `token_ceiling_also_blocks_report_retry` | Both F → P. Changes call count/stopping behavior; V2 has additional provider operations to audit. |
| Represent missing usage honestly | R `missing_provider_total_usage_is_unknown_not_zero` | F → P. Counter fix; budgets still depend on provider-reported usage. |
| Reject parseable but incomplete provider output | R `responses_404_fallback_and_partial_json_are_not_certified`, `responses_status_and_usage_are_preserved[incomplete]` | F → P. Complete-response parameter P → P. Independent boundary change but changes accepted model output. |
| Reject negative/nonfinite Retry-After | R `invalid_retry_after_cannot_cause_invalid_sleep` | Three parameters F → P. Independent provider defense. |
| Bound source files and lines | R `massive_file_and_long_line_are_visible_skips` | Two parameters F → P. Skips files over 1 MiB or lines over 16,384 characters; changes available evidence. |
| Repair evaluate-agent CLI argument handling | R `evaluate_agent_cli_uses_supported_arguments` | F → P. Original raises AttributeError for missing `args.force`; independent CLI port. |
| Offline scripted demo and verify --offline | R `demo_exercises_real_evidence_and_stage_b` | New feature absent from baseline; passes on delivery and fails if a live client is constructed. Demonstrates evidence payload persistence and deterministic verification, not model accuracy. |
| Detailed payloads, counters and final candidate summaries | R rejection-feedback and demo tests; existing show-run test | Delivery integration coverage; no separate original-failure reproduction for every newly logged field. Raw traces may now contain repository source and must stay local or be sanitized. |
| 120,000-character prompt and 12,000-character inspect limits | Source inspection; R source-size tests cover different limits | Implemented, but no direct matched regression of these two particular caps in the supplied suite. They can affect prediction context. |
| Disable checkout hooks in verification clones | Source inspection; existing phase6 verification tests | Implemented; no hostile-hook matched regression. Independent security candidate, not fully demonstrated by existing tests. |
| Index schema 3 → 4 and full-report-v1 → full-report-v2 | Existing references cache-version, render and show-run tests updated | Cache invalidation/version labeling; three existing expectation changes, not three additional tests. Delivery's full-report-v2 is distinct from the frozen full-report-v2.1.1 protocol. |
| CI, fresh-install/stress/abstention utilities and docs | Current local tests/lint; inspected utility source | CI is configured for 3.11/3.13; only local 3.13 was executed. Historical install/stress measurements were not rerun and are claims/records, not new P0 measurements. |

These added tests also pass on 358dbff: unknown encoding, provider failure status,
all nine scripted repository-injection parameters, KeyboardInterrupt propagation,
four parser edge cases, empty Python corpus, complete provider response,
non-404 provider failure, and bounded chat retries. The scripted attacks test
deterministic boundaries; they do not measure resistance of a live model.

Filesystem races, aggregate memory limits, semantic entailment of natural-language
claims, live provider behavior and real prediction accuracy remain unverified.
