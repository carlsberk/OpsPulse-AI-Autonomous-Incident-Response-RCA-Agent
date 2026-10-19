# RCA evaluation - live mode

- Run at: 2026-10-17T10:12:24.045968+00:00  |  dataset: rca-callers-v3 (15 cases)
- Versions: {"agent_version": "1.3.0-opspulse", "prompt_version": "rca-prompt-v6.1", "evaluator_version": "quality-gate-v8", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 1.000 | 15/15 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 1.000 | 13/13 | final category == labelled category / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 34/34 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/49 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | n/a | 0/0 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | n/a | 0/0 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/13 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 0.154 | 2/13 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.000 | 0/2 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 1.000 | 13/13 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | n/a | 0/0 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.133 | 2/15 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 1.133
- Not evaluated (provider prevented completion): ['caller-two-levels-up', 'control-cause-in-trigger']
- LLM latency ms: {'mean': 14200, 'p95': 43021, 'samples': 15}  |  case wall ms: {'mean': 14402, 'p95': 43027}
- Tokens: {'input': 35312, 'output': 21018, 'attempts_with_usage': 15}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| caller-passes-none | null_reference | null_reference | fail | needs_review | 1 | 2/2 |
| caller-passes-none | null_reference | null_reference | fail | needs_review | 1 | 2/2 |
| caller-passes-none | null_reference | null_reference | fail | needs_review | 2 | 1/1 |
| caller-empty-list | logic_error | logic_error | fail | needs_review | 1 | 3/3 |
| caller-empty-list | logic_error | logic_error | fail | needs_review | 1 | 2/2 |
| caller-empty-list | logic_error | logic_error | fail | needs_review | 1 | 3/3 |
| caller-symptomatic-patch | null_reference | null_reference | fail | needs_review | 1 | 2/2 |
| caller-symptomatic-patch | null_reference | null_reference | fail | needs_review | 1 | 2/2 |
| caller-symptomatic-patch | null_reference | null_reference | fail | needs_review | 2 | 3/3 |
| caller-two-levels-up | type_error | type_error | fail | needs_review | 1 | 3/3 |
| caller-two-levels-up | type_error | type_error | fail | needs_review | 1 | 3/3 |
| caller-two-levels-up | type_error | None | fail | failed | 1 | 0/0 |
| control-cause-in-trigger | logic_error | None | fail | failed | 1 | 0/0 |
| control-cause-in-trigger | logic_error | logic_error | pass | accepted | 1 | 4/4 |
| control-cause-in-trigger | logic_error | logic_error | pass | accepted | 1 | 4/4 |

### Run-to-run consistency (5 repeated case(s); stable outcome 5/5)

| Case | Runs | Accepted | Correct category | Distinct outcomes |
|---|---|---|---|---|
| caller-passes-none | 3 | 0 | 3 | 1 |
| caller-empty-list | 3 | 0 | 3 | 1 |
| caller-symptomatic-patch | 3 | 0 | 3 | 1 |
| caller-two-levels-up | 2 | 0 | 2 | 1 |
| control-cause-in-trigger | 2 | 2 | 2 | 1 |
