# RCA evaluation - live mode

- Run at: 2026-10-15T21:20:08.330859+00:00  |  dataset: rca-holdout-v1 (12 cases)
- Versions: {"agent_version": "1.2.0-opspulse", "prompt_version": "rca-prompt-v6", "evaluator_version": "quality-gate-v8", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 0.938 | 15/16 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 0.900 | 9/10 | final category == labelled category / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 25/25 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/35 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | 1.000 | 1/1 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | 1.000 | 1/1 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/10 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 0.818 | 9/11 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.111 | 1/9 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 1.000 | 10/10 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | 0.000 | 0/1 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.083 | 1/12 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 1.417
- Not evaluated (provider prevented completion): ['ho-drift-attribute']
- LLM latency ms: {'mean': 15332, 'p95': 33501, 'samples': 16}  |  case wall ms: {'mean': 22722, 'p95': 74414}
- Tokens: {'input': 37103, 'output': 25928, 'attempts_with_usage': 16}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| ho-none-config | null_reference | null_reference | pass | accepted | 2 | 4/4 |
| ho-env-int | configuration_error | configuration_error | pass | accepted | 1 | 2/2 |
| ho-redis-down | dependency_unavailable | dependency_unavailable | pass | accepted | 1 | 3/3 |
| ho-upstream-shape | invalid_external_response | invalid_external_response | pass | accepted | 1 | 2/2 |
| ho-typo-method | attribute_error | attribute_error | pass | accepted | 1 | 2/2 |
| ho-internal-key | missing_key | missing_key | pass | accepted | 1 | 2/2 |
| ho-str-price | type_error | type_error | pass | accepted | 1 | 2/2 |
| ho-off-by-one | logic_error | logic_error | fail | needs_review | 3 | 3/3 |
| ho-missing-package | import_error | import_error | pass | accepted | 1 | 3/3 |
| ho-gateway-timeout | unknown (inconclusive) | unknown | fail | needs_review | 1 | 0/0 |
| ho-drift-attribute | attribute_error (inconclusive) | attribute_error | fail | failed | 2 | 2/2 |
| ho-pool-timeout | dependency_unavailable | configuration_error | pass | accepted | 2 | 2/2 |
