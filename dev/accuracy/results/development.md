### Simulated policy: observed

| metric | A-baseline | B-retention | C-proof-selection | D-diverse-discovery | E-review-progression | F-bounded-context | H-precision-guard | G-without-guard | G-combined | G0-combined-no-retention | P3a-proof+diverse+retention | T-confidence-tiers | GT-combined+tiers |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tasks | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 |
| seed_recall | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 |
| candidate_recall | 0.8667 | 0.8667 | 0.8667 | 1.0 | 0.8667 | 0.8667 | 0.8667 | 1.0 | 1.0 | 1.0 | 1.0 | 0.8667 | 1.0 |
| confirmed_files | 8 | 8 | 13 | 8 | 8 | 8 | 7 | 15 | 15 | 15 | 14 | 8 | 15 |
| predicted_files | 8 | 8 | 11 | 8 | 8 | 8 | 7 | 15 | 15 | 14 | 14 | 8 | 15 |
| true_positives | 7 | 7 | 8 | 7 | 7 | 7 | 6 | 10 | 10 | 10 | 9 | 7 | 10 |
| false_positives | 1 | 1 | 3 | 1 | 1 | 1 | 1 | 5 | 5 | 4 | 5 | 1 | 5 |
| false_negatives | 8 | 8 | 7 | 8 | 8 | 8 | 9 | 5 | 5 | 5 | 6 | 8 | 5 |
| precision | 0.875 | 0.875 | 0.7273 | 0.875 | 0.875 | 0.875 | 0.8571 | 0.6667 | 0.6667 | 0.7143 | 0.6429 | 0.875 | 0.6667 |
| recall | 0.4667 | 0.4667 | 0.5333 | 0.4667 | 0.4667 | 0.4667 | 0.4 | 0.6667 | 0.6667 | 0.6667 | 0.6 | 0.4667 | 0.6667 |
| f1 | 0.6087 | 0.6087 | 0.6154 | 0.6087 | 0.6087 | 0.6087 | 0.5454 | 0.6667 | 0.6667 | 0.6897 | 0.6207 | 0.6087 | 0.6667 |
| abstentions | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 2 | 2 | 2 | 2 | 3 | 2 |
| correct_abstentions | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| refusals | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 2 | 11 | 11 | 0 | 0 | 11 |
| decided_candidates | 8 | 8 | 22 | 9 | 9 | 8 | 7 | 36 | 32 | 32 | 34 | 8 | 32 |
| fully_checked_candidates | 14 | 14 | 25 | 20 | 17 | 14 | 13 | 51 | 54 | 54 | 44 | 14 | 54 |
| duplicate_calls | 19 | 19 | 6 | 19 | 19 | 19 | 17 | 6 | 6 | 6 | 6 | 19 | 6 |
| tool_calls | 52 | 52 | 92 | 68 | 63 | 52 | 49 | 152 | 161 | 161 | 138 | 52 | 161 |
| proof_steps | 0 | 0 | 47 | 0 | 0 | 0 | 0 | 51 | 55 | 55 | 64 | 0 | 55 |
| submitted | 7 | 7 | 7 | 7 | 7 | 7 | 7 | 8 | 7 | 7 | 8 | 7 | 7 |
| censored | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| max_request_estimate | 14673 | 14673 | 14868 | 14702 | 14673 | 14220 | 14673 | 14432 | 14432 | 14432 | 14868 | 14673 | 14432 |
| over_cap_requests | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| fp_by_origin | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} |
| tp_by_origin | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 8, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 6, 'structural': 0, 'other': 0} | {'seed': 10, 'structural': 0, 'other': 0} | {'seed': 10, 'structural': 0, 'other': 0} | {'seed': 10, 'structural': 0, 'other': 0} | {'seed': 9, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 10, 'structural': 0, 'other': 0} |
| indirect_confirmations | 1 | 1 | 1 | 2 | 1 | 1 | 0 | 7 | 0 | 0 | 3 | 1 | 0 |
| primary_tp | 7 | 7 | 7 | 7 | 7 | 7 | 6 | 8 | 8 | 8 | 7 | 7 | 8 |
| primary_fp | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 4 | 3 | 3 | 2 | 1 | 3 |
| secondary_tp | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 2 | 2 | 2 | 2 | 0 | 2 |
| secondary_fp | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 1 | 2 | 1 | 3 | 0 | 2 |
| primary_precision | 0.875 | 0.875 | 0.875 | 0.875 | 0.875 | 0.875 | 0.8571 | 0.6667 | 0.7273 | 0.7273 | 0.7778 | 0.875 | 0.7273 |
| primary_recall | 0.4667 | 0.4667 | 0.4667 | 0.4667 | 0.4667 | 0.4667 | 0.4 | 0.5333 | 0.5333 | 0.5333 | 0.4667 | 0.4667 | 0.5333 |
| primary_f1 | 0.6087 | 0.6087 | 0.6087 | 0.6087 | 0.6087 | 0.6087 | 0.5454 | 0.5926 | 0.6154 | 0.6154 | 0.5834 | 0.6087 | 0.6154 |
| retired | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 13 | 11 | 11 | 0 | 0 | 11 |
| stop_reasons | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_progress': 3, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_progress': 2, 'submitted': 8} | {'no_progress': 2, 'submitted': 7, 'tool_budget': 1} | {'no_progress': 2, 'submitted': 7, 'tool_budget': 1} | {'no_progress': 2, 'submitted': 8} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_progress': 2, 'submitted': 7, 'tool_budget': 1} |

### Simulated policy: guided

| metric | A-baseline | B-retention | C-proof-selection | D-diverse-discovery | E-review-progression | F-bounded-context | H-precision-guard | G-without-guard | G-combined | G0-combined-no-retention | P3a-proof+diverse+retention | T-confidence-tiers | GT-combined+tiers |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| tasks | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | 10 |
| seed_recall | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 | 0.8667 |
| candidate_recall | 0.8667 | 0.8667 | 0.8667 | 1.0 | 0.8667 | 0.8667 | 0.8667 | 1.0 | 1.0 | 1.0 | 1.0 | 0.8667 | 1.0 |
| confirmed_files | 8 | 8 | 13 | 8 | 9 | 8 | 7 | 14 | 14 | 14 | 13 | 8 | 14 |
| predicted_files | 8 | 8 | 11 | 8 | 9 | 8 | 7 | 14 | 14 | 12 | 13 | 8 | 14 |
| true_positives | 7 | 7 | 8 | 7 | 7 | 7 | 6 | 9 | 9 | 8 | 9 | 7 | 9 |
| false_positives | 1 | 1 | 3 | 1 | 2 | 1 | 1 | 5 | 5 | 4 | 4 | 1 | 5 |
| false_negatives | 8 | 8 | 7 | 8 | 8 | 8 | 9 | 6 | 6 | 7 | 6 | 8 | 6 |
| precision | 0.875 | 0.875 | 0.7273 | 0.875 | 0.7778 | 0.875 | 0.8571 | 0.6429 | 0.6429 | 0.6667 | 0.6923 | 0.875 | 0.6429 |
| recall | 0.4667 | 0.4667 | 0.5333 | 0.4667 | 0.4667 | 0.4667 | 0.4 | 0.6 | 0.6 | 0.5333 | 0.6 | 0.4667 | 0.6 |
| f1 | 0.6087 | 0.6087 | 0.6154 | 0.6087 | 0.5834 | 0.6087 | 0.5454 | 0.6207 | 0.6207 | 0.5926 | 0.6429 | 0.6087 | 0.6207 |
| abstentions | 3 | 3 | 3 | 3 | 2 | 3 | 3 | 2 | 2 | 2 | 3 | 3 | 2 |
| correct_abstentions | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| refusals | 6 | 6 | 6 | 6 | 6 | 6 | 7 | 6 | 8 | 8 | 6 | 6 | 8 |
| decided_candidates | 8 | 8 | 22 | 8 | 9 | 8 | 7 | 32 | 32 | 32 | 33 | 8 | 32 |
| fully_checked_candidates | 18 | 18 | 32 | 24 | 20 | 18 | 17 | 54 | 53 | 53 | 45 | 18 | 53 |
| duplicate_calls | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| tool_calls | 44 | 44 | 80 | 60 | 59 | 44 | 42 | 141 | 138 | 138 | 117 | 44 | 138 |
| proof_steps | 0 | 0 | 31 | 0 | 0 | 0 | 0 | 41 | 42 | 42 | 37 | 0 | 42 |
| submitted | 7 | 7 | 7 | 7 | 8 | 7 | 7 | 8 | 8 | 8 | 7 | 7 | 8 |
| censored | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| max_request_estimate | 14673 | 14673 | 14868 | 14654 | 14673 | 14220 | 14673 | 14406 | 14406 | 14406 | 14849 | 14673 | 14406 |
| over_cap_requests | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| fp_by_origin | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 3, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 2, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 4, 'structural': 0, 'other': 0} | {'seed': 1, 'structural': 0, 'other': 0} | {'seed': 5, 'structural': 0, 'other': 0} |
| tp_by_origin | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 8, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 6, 'structural': 0, 'other': 0} | {'seed': 9, 'structural': 0, 'other': 0} | {'seed': 9, 'structural': 0, 'other': 0} | {'seed': 8, 'structural': 0, 'other': 0} | {'seed': 9, 'structural': 0, 'other': 0} | {'seed': 7, 'structural': 0, 'other': 0} | {'seed': 9, 'structural': 0, 'other': 0} |
| indirect_confirmations | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 3 | 0 | 0 | 1 | 1 | 0 |
| primary_tp | 7 | 7 | 7 | 7 | 7 | 7 | 6 | 7 | 6 | 6 | 7 | 7 | 6 |
| primary_fp | 1 | 1 | 1 | 1 | 2 | 1 | 1 | 2 | 2 | 2 | 1 | 1 | 2 |
| secondary_tp | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 2 | 3 | 2 | 2 | 0 | 3 |
| secondary_fp | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 3 | 3 | 2 | 3 | 0 | 3 |
| primary_precision | 0.875 | 0.875 | 0.875 | 0.875 | 0.7778 | 0.875 | 0.8571 | 0.7778 | 0.75 | 0.75 | 0.875 | 0.875 | 0.75 |
| primary_recall | 0.4667 | 0.4667 | 0.4667 | 0.4667 | 0.4667 | 0.4667 | 0.4 | 0.4667 | 0.4 | 0.4 | 0.4667 | 0.4667 | 0.4 |
| primary_f1 | 0.6087 | 0.6087 | 0.6087 | 0.6087 | 0.5834 | 0.6087 | 0.5454 | 0.5834 | 0.5217 | 0.5217 | 0.6087 | 0.6087 | 0.5217 |
| retired | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 11 | 11 | 11 | 0 | 0 | 11 |
| stop_reasons | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_progress': 2, 'submitted': 8} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_progress': 2, 'submitted': 8} | {'no_progress': 2, 'submitted': 8} | {'no_progress': 2, 'submitted': 8} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_ledger_progress': 1, 'no_progress': 2, 'submitted': 7} | {'no_progress': 2, 'submitted': 8} |
