# RCA evaluation - live mode

- Run at: 2026-10-15T20:26:40.828989+00:00  |  dataset: rca-callers-v3 (15 cases)
- Versions: {"agent_version": "1.2.0-opspulse", "prompt_version": "rca-prompt-v6", "evaluator_version": "quality-gate-v8", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 1.000 | 15/15 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 1.000 | 15/15 | final category == labelled category / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 41/41 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/58 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | n/a | 0/0 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | n/a | 0/0 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/15 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 0.400 | 6/15 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.000 | 0/6 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 0.333 | 5/15 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | n/a | 0/0 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.000 | 0/15 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 1.0
- Not evaluated (provider prevented completion): none
- LLM latency ms: {'mean': 13533, 'p95': 25114, 'samples': 15}  |  case wall ms: {'mean': 13538, 'p95': 25120}
- Tokens: {'input': 34050, 'output': 22728, 'attempts_with_usage': 15}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| caller-passes-none | null_reference | null_reference | fail | needs_review | 1 | 2/2 |
| caller-passes-none | null_reference | null_reference | fail | needs_review | 1 | 2/2 |
| caller-passes-none | null_reference | null_reference | fail | needs_review | 1 | 2/2 |
| caller-empty-list | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| caller-empty-list | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| caller-empty-list | logic_error | logic_error | pass | accepted | 1 | 4/4 |
| caller-symptomatic-patch | null_reference | null_reference | fail | needs_review | 1 | 3/3 |
| caller-symptomatic-patch | null_reference | null_reference | fail | needs_review | 1 | 2/2 |
| caller-symptomatic-patch | null_reference | null_reference | fail | needs_review | 1 | 3/3 |
| caller-two-levels-up | type_error | type_error | fail | needs_review | 1 | 3/3 |
| caller-two-levels-up | type_error | type_error | fail | needs_review | 1 | 2/2 |
| caller-two-levels-up | type_error | type_error | fail | needs_review | 1 | 3/3 |
| control-cause-in-trigger | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| control-cause-in-trigger | logic_error | logic_error | pass | accepted | 1 | 3/3 |
| control-cause-in-trigger | logic_error | logic_error | pass | accepted | 1 | 3/3 |

### Run-to-run consistency (5 repeated case(s); stable outcome 5/5)

| Case | Runs | Accepted | Correct category | Distinct outcomes |
|---|---|---|---|---|
| caller-passes-none | 3 | 0 | 3 | 1 |
| caller-empty-list | 3 | 3 | 3 | 1 |
| caller-symptomatic-patch | 3 | 0 | 3 | 1 |
| caller-two-levels-up | 3 | 0 | 3 | 1 |
| control-cause-in-trigger | 3 | 3 | 3 | 1 |
