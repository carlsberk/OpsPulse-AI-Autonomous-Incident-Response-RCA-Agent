# RCA evaluation - live mode

- Run at: 2026-10-28T17:59:25.403411+00:00  |  dataset: history-real-v1 (15 cases)
- Versions: {"agent_version": "1.9.0-opspulse", "prompt_version": "rca-prompt-v7.2", "evaluator_version": "quality-gate-v10", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 0.957 | 22/23 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 0.769 | 10/13 | final category == labelled category (or an accepted alternative) / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 30/30 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/43 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | n/a | 0/0 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | n/a | 0/0 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/13 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 1.000 | 13/13 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.231 | 3/13 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 1.000 | 13/13 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | n/a | 0/0 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.133 | 2/15 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 1.667
- Not evaluated (provider prevented completion): ['hist-0978de5b', 'hist-20c6370d']
- LLM latency ms: {'mean': 35492, 'p95': 70650, 'samples': 23}  |  case wall ms: {'mean': 57798, 'p95': 117302}
- Tokens: {'input': 104933, 'output': 58769, 'attempts_with_usage': 23}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| hist-87a9a4e0 | logic_error | logic_error | pass | accepted | 3 | 3/3 |
| hist-87a9a4e0 | logic_error | logic_error | pass | accepted | 3 | 4/4 |
| hist-87a9a4e0 | logic_error | logic_error | pass | accepted | 3 | 2/2 |
| hist-92cb7096 | type_error | type_error | pass | accepted | 1 | 2/2 |
| hist-92cb7096 | type_error | logic_error | pass | accepted | 2 | 2/2 |
| hist-92cb7096 | type_error | type_error | pass | accepted | 1 | 2/2 |
| hist-0978de5b | attribute_error | logic_error | pass | accepted | 1 | 2/2 |
| hist-0978de5b | attribute_error | logic_error | fail | failed | 2 | 3/3 |
| hist-0978de5b | attribute_error | logic_error | pass | accepted | 1 | 2/2 |
| hist-20c6370d | logic_error | logic_error | pass | accepted | 1 | 2/2 |
| hist-20c6370d | logic_error | logic_error | pass | accepted | 1 | 2/2 |
| hist-20c6370d | logic_error | logic_error | fail | failed | 2 | 2/2 |
| hist-d2eba037 | logic_error | logic_error | pass | accepted | 1 | 2/2 |
| hist-d2eba037 | logic_error | logic_error | pass | accepted | 1 | 2/2 |
| hist-d2eba037 | logic_error | logic_error | pass | accepted | 2 | 3/3 |

### Run-to-run consistency (5 repeated case(s); stable outcome 4/5)

| Case | Runs | Accepted | Correct category | Distinct outcomes |
|---|---|---|---|---|
| hist-87a9a4e0 | 3 | 3 | 3 | 1 |
| hist-92cb7096 | 3 | 3 | 2 | 2 |
| hist-0978de5b | 2 | 2 | 0 | 1 |
| hist-20c6370d | 2 | 2 | 2 | 1 |
| hist-d2eba037 | 3 | 3 | 3 | 1 |
