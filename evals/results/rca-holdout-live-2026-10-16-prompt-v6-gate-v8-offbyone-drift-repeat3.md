# RCA evaluation - live mode

- Run at: 2026-10-16T12:04:52.060853+00:00  |  dataset: rca-holdout-v1 (6 cases)
- Versions: {"agent_version": "1.2.0-opspulse", "prompt_version": "rca-prompt-v6", "evaluator_version": "quality-gate-v8", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 0.714 | 5/7 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 1.000 | 3/3 | final category == labelled category / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 11/11 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/15 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | 1.000 | 1/1 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | 1.000 | 1/1 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/3 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 0.750 | 3/4 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.000 | 0/3 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 1.000 | 4/4 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | 0.000 | 0/1 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.333 | 2/6 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 1.5
- Not evaluated (provider prevented completion): ['ho-drift-attribute', 'ho-drift-attribute']
- LLM latency ms: {'mean': 7545, 'p95': 17216, 'samples': 7}  |  case wall ms: {'mean': 15803, 'p95': 46913}
- Tokens: {'input': 15429, 'output': 10823, 'attempts_with_usage': 7}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| ho-off-by-one | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| ho-off-by-one | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| ho-off-by-one | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| ho-drift-attribute | attribute_error (inconclusive) | attribute_error | fail | failed | 2 | 1/2 |
| ho-drift-attribute | attribute_error (inconclusive) | attribute_error | fail | failed | 2 | 2/2 |
| ho-drift-attribute | attribute_error (inconclusive) | unknown | fail | needs_review | 2 | 2/2 |

### Run-to-run consistency (1 repeated case(s); stable outcome 1/1)

| Case | Runs | Accepted | Correct category | Distinct outcomes |
|---|---|---|---|---|
| ho-off-by-one | 3 | 3 | 3 | 1 |
