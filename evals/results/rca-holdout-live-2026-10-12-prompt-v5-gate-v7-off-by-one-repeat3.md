# RCA evaluation - live mode

- Run at: 2026-10-12T09:35:55.204863+00:00  |  dataset: rca-holdout-v1 (3 cases)
- Versions: {"agent_version": "1.2.0-opspulse", "prompt_version": "rca-prompt-v5", "evaluator_version": "quality-gate-v7", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 1.000 | 8/8 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 1.000 | 3/3 | final category == labelled category / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 8/8 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/11 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | n/a | 0/0 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | n/a | 0/0 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/3 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 0.667 | 2/3 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.000 | 0/2 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 1.000 | 3/3 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | n/a | 0/0 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.000 | 0/3 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 2.667
- Not evaluated (provider prevented completion): none
- LLM latency ms: {'mean': 20589, 'p95': 33364, 'samples': 8}  |  case wall ms: {'mean': 54915, 'p95': 81971}
- Tokens: {'input': 19809, 'output': 14150, 'attempts_with_usage': 8}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| ho-off-by-one | logic_error | logic_error | fail | needs_review | 3 | 2/2 |
| ho-off-by-one | logic_error | logic_error | pass | accepted | 2 | 3/3 |
| ho-off-by-one | logic_error | logic_error | pass | accepted | 3 | 3/3 |

### Run-to-run consistency (1 repeated case(s); stable outcome 0/1)

| Case | Runs | Accepted | Correct category | Distinct outcomes |
|---|---|---|---|---|
| ho-off-by-one | 3 | 2 | 3 | 2 |
