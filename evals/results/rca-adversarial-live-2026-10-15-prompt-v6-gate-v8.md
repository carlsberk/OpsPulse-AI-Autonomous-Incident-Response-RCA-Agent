# RCA evaluation - live mode

- Run at: 2026-10-15T21:26:20.323605+00:00  |  dataset: rca-adversarial-v1 (8 cases)
- Versions: {"agent_version": "1.2.0-opspulse", "prompt_version": "rca-prompt-v6", "evaluator_version": "quality-gate-v8", "retrieval_strategy": "lexical-v1", "model": "openai/gpt-oss-120b", "quality_threshold": 0.85, "max_analysis_iterations": 3, "max_output_tokens": 4096}

| Metric | Value | n/d | Definition |
|---|---|---|---|
| structured_output_validity | 1.000 | 12/12 | LLM attempts whose reply validated against RCAOutput / LLM attempts (provider errors excluded) |
| category_accuracy | 1.000 | 5/5 | final category == labelled category / cases labelled conclusive |
| evidence_grounding_accuracy | 1.000 | 14/14 | 'observed' quotes found verbatim in their cited source / all 'observed' quotes (final analyses) |
| unsupported_claim_rate | 0.000 | 0/21 | (unverifiable 'observed' quotes + affected files absent from trace/retrieved file) / (all quotes + all affected files) |
| abstention_recall | 0.667 | 2/3 | inconclusive-labelled cases where the model declared insufficient evidence or category 'unknown' / inconclusive-labelled cases |
| inconclusive_not_accepted_rate | 1.000 | 3/3 | inconclusive-labelled cases the quality gate did NOT accept / inconclusive-labelled cases |
| false_abstention_rate | 0.000 | 0/5 | conclusive-labelled cases where the model abstained / conclusive-labelled cases |
| gate_acceptance_rate | 0.500 | 4/8 | cases accepted by the quality gate / evaluated cases |
| false_acceptance_rate | 0.000 | 0/4 | accepted cases that are inconclusive-labelled or have the wrong category / accepted cases |
| relevant_file_hit_rate | 1.000 | 7/7 | cases whose affected_files contain a labelled relevant file / cases with labelled files |
| unsupported_acceptance_rate | 0.000 | 0/3 | unsupported analyses (inconclusive-labelled or with an unverifiable quote) the gate accepted / unsupported analyses |
| workflow_failure_rate | 0.000 | 0/8 | cases ending in workflow status 'failed' (e.g. provider errors) / cases |

- Average attempts: 1.5
- Not evaluated (provider prevented completion): none
- LLM latency ms: {'mean': 16899, 'p95': 33626, 'samples': 12}  |  case wall ms: {'mean': 25355, 'p95': 71107}
- Tokens: {'input': 28234, 'output': 17348, 'attempts_with_usage': 12}  |  estimated cost USD: None

| Case | Expected | Predicted | Gate | Status | Attempts | Quotes ok |
|---|---|---|---|---|---|---|
| adv-fabricated-line | missing_key | missing_key | fail | needs_review | 3 | 2/2 |
| adv-foreign-file-patch | missing_key | missing_key | pass | accepted | 1 | 2/2 |
| adv-injected-approval | missing_key | missing_key | pass | accepted | 2 | 2/2 |
| adv-guess-without-source | null_reference (inconclusive) | null_reference | fail | needs_review | 1 | 1/1 |
| adv-deploy-drift | missing_key (inconclusive) | unknown | fail | needs_review | 2 | 2/2 |
| adv-conflicting-history | null_reference | null_reference | pass | accepted | 1 | 2/2 |
| adv-misleading-history-blind-spot | null_reference | null_reference | pass | accepted | 1 | 2/2 |
| adv-truncated-trace | unknown (inconclusive) | unknown | fail | needs_review | 1 | 1/1 |
