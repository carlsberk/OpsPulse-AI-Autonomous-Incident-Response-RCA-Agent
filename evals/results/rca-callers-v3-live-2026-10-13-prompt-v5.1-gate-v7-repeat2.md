# RCA evaluation - live mode

- Run at: 2026-10-13T10:35:36.202614+00:00  |  dataset: rca-callers-v3 (10 cases)
- Versions: {"agent_version": "1.2.0-opspulse", "prompt_version": "rca-prompt-v5.1", "evaluator_version": "quality-gate-v7", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 1.000 | 17/17 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 1.000 | 10/10 | final category == labelled category / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 21/21 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/32 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | n/a | 0/0 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | n/a | 0/0 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/10 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 0.300 | 3/10 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.000 | 0/3 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 0.300 | 3/10 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | n/a | 0/0 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.000 | 0/10 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 1.7
- Not evaluated (provider prevented completion): none
- LLM latency ms: {'mean': 18176, 'p95': 37025, 'samples': 17}  |  case wall ms: {'mean': 30906, 'p95': 57030}
- Tokens: {'input': 40733, 'output': 27684, 'attempts_with_usage': 17}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| caller-passes-none | null_reference | null_reference | fail | needs_review | 2 | 2/2 |
| caller-passes-none | null_reference | null_reference | fail | needs_review | 2 | 2/2 |
| caller-empty-list | logic_error | logic_error | pass | accepted | 1 | 1/1 |
| caller-empty-list | logic_error | logic_error | fail | needs_review | 2 | 3/3 |
| caller-symptomatic-patch | null_reference | null_reference | fail | needs_review | 2 | 2/2 |
| caller-symptomatic-patch | null_reference | null_reference | fail | needs_review | 2 | 2/2 |
| caller-two-levels-up | type_error | type_error | pass | accepted | 1 | 1/1 |
| caller-two-levels-up | type_error | type_error | pass | accepted | 1 | 2/2 |
| control-cause-in-trigger | logic_error | logic_error | fail | needs_review | 2 | 3/3 |
| control-cause-in-trigger | logic_error | logic_error | fail | needs_review | 2 | 3/3 |

### Run-to-run consistency (5 repeated case(s); stable outcome 4/5)

| Case | Runs | Accepted | Correct category | Distinct outcomes |
|---|---|---|---|---|
| caller-passes-none | 2 | 0 | 2 | 1 |
| caller-empty-list | 2 | 1 | 2 | 2 |
| caller-symptomatic-patch | 2 | 0 | 2 | 1 |
| caller-two-levels-up | 2 | 2 | 2 | 1 |
| control-cause-in-trigger | 2 | 0 | 2 | 1 |
