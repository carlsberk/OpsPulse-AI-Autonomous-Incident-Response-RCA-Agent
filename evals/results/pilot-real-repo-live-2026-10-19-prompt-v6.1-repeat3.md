# RCA evaluation - live mode

- Run at: 2026-10-19T10:45:50.927660+00:00  |  dataset: pilot-real-repo-v1 (12 cases)
- Versions: {"agent_version": "1.3.0-opspulse", "prompt_version": "rca-prompt-v6.1", "evaluator_version": "quality-gate-v8", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 0.933 | 14/15 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 0.750 | 9/12 | final category == labelled category / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 29/29 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/41 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | n/a | 0/0 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | n/a | 0/0 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/12 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 1.000 | 12/12 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.250 | 3/12 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 1.000 | 12/12 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | n/a | 0/0 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.000 | 0/12 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 1.25
- Not evaluated (provider prevented completion): none
- LLM latency ms: {'mean': 29630, 'p95': 56371, 'samples': 15}  |  case wall ms: {'mean': 37050, 'p95': 99486}
- Tokens: {'input': 56101, 'output': 26997, 'attempts_with_usage': 15}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| finding-codes-missing-key | missing_key | missing_key | pass | accepted | 1 | 2/2 |
| finding-codes-missing-key | missing_key | missing_key | pass | accepted | 1 | 2/2 |
| finding-codes-missing-key | missing_key | missing_key | pass | accepted | 1 | 2/2 |
| field-accuracy-empty-division | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| field-accuracy-empty-division | logic_error | logic_error | pass | accepted | 2 | 2/2 |
| field-accuracy-empty-division | logic_error | logic_error | pass | accepted | 1 | 2/2 |
| dataset-version-bad-group | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| dataset-version-bad-group | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| dataset-version-bad-group | logic_error | logic_error | pass | accepted | 2 | 2/2 |
| retrieval-k-default-dropped | type_error | configuration_error | pass | accepted | 1 | 2/2 |
| retrieval-k-default-dropped | type_error | configuration_error | pass | accepted | 2 | 3/3 |
| retrieval-k-default-dropped | type_error | configuration_error | pass | accepted | 1 | 3/3 |

### Run-to-run consistency (4 repeated case(s); stable outcome 4/4)

| Case | Runs | Accepted | Correct category | Distinct outcomes |
|---|---|---|---|---|
| finding-codes-missing-key | 3 | 3 | 3 | 1 |
| field-accuracy-empty-division | 3 | 3 | 3 | 1 |
| dataset-version-bad-group | 3 | 3 | 3 | 1 |
| retrieval-k-default-dropped | 3 | 3 | 0 | 1 |
