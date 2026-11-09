### Simulated policy: observed

| metric | A-baseline | B-retention | C-proof-selection | D-diverse-discovery | E-review-progression | F-bounded-context | H-precision-guard | G-without-guard | G-combined | G0-combined-no-retention | P3a-proof+diverse+retention | T-confidence-tiers | GT-combined+tiers |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tasks | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| seed_recall | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| candidate_recall | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| confirmed_files | 3 | 3 | 4 | 3 | 4 | 3 | 3 | 5 | 5 | 5 | 4 | 3 | 5 |
| predicted_files | 3 | 3 | 4 | 3 | 4 | 3 | 3 | 5 | 5 | 5 | 4 | 3 | 5 |
| true_positives | 3 | 3 | 4 | 3 | 4 | 3 | 3 | 5 | 5 | 5 | 4 | 3 | 5 |
| false_positives | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| false_negatives | 5 | 5 | 4 | 5 | 4 | 5 | 5 | 3 | 3 | 3 | 4 | 5 | 3 |
| precision | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| recall | 0.375 | 0.375 | 0.5 | 0.375 | 0.5 | 0.375 | 0.375 | 0.625 | 0.625 | 0.625 | 0.5 | 0.375 | 0.625 |
| f1 | 0.5455 | 0.5455 | 0.6667 | 0.5455 | 0.6667 | 0.5455 | 0.5455 | 0.7692 | 0.7692 | 0.7692 | 0.6667 | 0.5455 | 0.7692 |
| abstentions | 3 | 3 | 3 | 3 | 2 | 3 | 3 | 2 | 2 | 2 | 3 | 3 | 2 |
| correct_abstentions | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| refusals | 0 | 0 | 1 | 2 | 0 | 0 | 0 | 0 | 3 | 3 | 1 | 0 | 3 |
| decided_candidates | 3 | 3 | 6 | 4 | 4 | 3 | 3 | 12 | 12 | 12 | 7 | 3 | 12 |
| fully_checked_candidates | 7 | 7 | 12 | 10 | 7 | 7 | 7 | 19 | 19 | 19 | 13 | 7 | 19 |
| duplicate_calls | 12 | 12 | 9 | 12 | 8 | 12 | 12 | 3 | 3 | 3 | 8 | 12 | 3 |
| tool_calls | 29 | 29 | 50 | 39 | 31 | 29 | 29 | 71 | 72 | 72 | 61 | 29 | 72 |
| proof_steps | 0 | 0 | 25 | 0 | 0 | 0 | 0 | 25 | 26 | 26 | 26 | 0 | 26 |
| submitted | 3 | 3 | 3 | 3 | 4 | 3 | 3 | 4 | 4 | 4 | 3 | 3 | 4 |
| censored | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| max_request_estimate | 13771 | 13771 | 13962 | 14116 | 13771 | 13771 | 13771 | 14307 | 14307 | 14307 | 15042 | 13771 | 14307 |
| over_cap_requests | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| fp_by_origin | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} |
| tp_by_origin | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} |
| indirect_confirmations | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 |
| primary_tp | 3 | 3 | 3 | 3 | 4 | 3 | 3 | 4 | 4 | 4 | 3 | 3 | 4 |
| primary_fp | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| secondary_tp | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 1 | 1 | 1 | 0 | 1 |
| secondary_fp | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| primary_precision | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| primary_recall | 0.375 | 0.375 | 0.375 | 0.375 | 0.5 | 0.375 | 0.375 | 0.5 | 0.5 | 0.5 | 0.375 | 0.375 | 0.5 |
| primary_f1 | 0.5455 | 0.5455 | 0.5455 | 0.5455 | 0.6667 | 0.5455 | 0.5455 | 0.6667 | 0.6667 | 0.6667 | 0.5455 | 0.5455 | 0.6667 |
| retired | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 9 | 9 | 9 | 0 | 0 | 9 |
| stop_reasons | {'no_ledger_progress': 2, 'no_progress': 1, 'submitted': 3} | {'no_ledger_progress': 2, 'no_progress': 1, 'submitted': 3} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_ledger_progress': 2, 'no_progress': 1, 'submitted': 3} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} | {'no_ledger_progress': 2, 'no_progress': 1, 'submitted': 3} | {'no_ledger_progress': 2, 'no_progress': 1, 'submitted': 3} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} | {'no_ledger_progress': 2, 'no_progress': 1, 'submitted': 3} | {'no_ledger_progress': 2, 'no_progress': 1, 'submitted': 3} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} |

### Simulated policy: guided

| metric | A-baseline | B-retention | C-proof-selection | D-diverse-discovery | E-review-progression | F-bounded-context | H-precision-guard | G-without-guard | G-combined | G0-combined-no-retention | P3a-proof+diverse+retention | T-confidence-tiers | GT-combined+tiers |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tasks | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| seed_recall | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| candidate_recall | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| confirmed_files | 3 | 3 | 4 | 3 | 4 | 3 | 3 | 5 | 5 | 5 | 4 | 3 | 5 |
| predicted_files | 3 | 3 | 4 | 3 | 4 | 3 | 3 | 5 | 5 | 5 | 4 | 3 | 5 |
| true_positives | 3 | 3 | 4 | 3 | 4 | 3 | 3 | 5 | 5 | 5 | 4 | 3 | 5 |
| false_positives | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| false_negatives | 5 | 5 | 4 | 5 | 4 | 5 | 5 | 3 | 3 | 3 | 4 | 5 | 3 |
| precision | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| recall | 0.375 | 0.375 | 0.5 | 0.375 | 0.5 | 0.375 | 0.375 | 0.625 | 0.625 | 0.625 | 0.5 | 0.375 | 0.625 |
| f1 | 0.5455 | 0.5455 | 0.6667 | 0.5455 | 0.6667 | 0.5455 | 0.5455 | 0.7692 | 0.7692 | 0.7692 | 0.6667 | 0.5455 | 0.7692 |
| abstentions | 3 | 3 | 3 | 3 | 2 | 3 | 3 | 2 | 2 | 2 | 3 | 3 | 2 |
| correct_abstentions | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| refusals | 6 | 6 | 6 | 6 | 3 | 6 | 6 | 3 | 3 | 3 | 6 | 6 | 3 |
| decided_candidates | 3 | 3 | 5 | 3 | 4 | 3 | 3 | 12 | 12 | 12 | 6 | 3 | 12 |
| fully_checked_candidates | 8 | 8 | 13 | 11 | 7 | 8 | 8 | 20 | 20 | 20 | 14 | 8 | 20 |
| duplicate_calls | 2 | 2 | 2 | 2 | 1 | 2 | 2 | 1 | 1 | 1 | 2 | 2 | 1 |
| tool_calls | 26 | 26 | 43 | 33 | 25 | 26 | 26 | 67 | 67 | 67 | 54 | 26 | 67 |
| proof_steps | 0 | 0 | 17 | 0 | 0 | 0 | 0 | 24 | 24 | 24 | 18 | 0 | 24 |
| submitted | 3 | 3 | 3 | 3 | 4 | 3 | 3 | 4 | 4 | 4 | 3 | 3 | 4 |
| censored | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| max_request_estimate | 13771 | 13771 | 13962 | 14116 | 13771 | 13771 | 13771 | 14307 | 14307 | 14307 | 14307 | 13771 | 14307 |
| over_cap_requests | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| fp_by_origin | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} | {'seed': 0, 'structural': 0, 'other': 0} |
| tp_by_origin | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} |
| indirect_confirmations | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| primary_tp | 3 | 3 | 3 | 3 | 4 | 3 | 3 | 4 | 4 | 4 | 3 | 3 | 4 |
| primary_fp | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| secondary_tp | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 1 | 1 | 1 | 0 | 1 |
| secondary_fp | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| primary_precision | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| primary_recall | 0.375 | 0.375 | 0.375 | 0.375 | 0.5 | 0.375 | 0.375 | 0.5 | 0.5 | 0.5 | 0.375 | 0.375 | 0.5 |
| primary_f1 | 0.5455 | 0.5455 | 0.5455 | 0.5455 | 0.6667 | 0.5455 | 0.5455 | 0.6667 | 0.6667 | 0.6667 | 0.5455 | 0.5455 | 0.6667 |
| retired | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 7 | 7 | 7 | 0 | 0 | 7 |
| stop_reasons | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 3} | {'no_progress': 1, 'review_exhausted': 1, 'submitted': 4} |
