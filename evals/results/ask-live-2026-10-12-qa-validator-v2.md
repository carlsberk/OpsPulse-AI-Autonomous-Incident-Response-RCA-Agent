# Incident assistant evaluation - live mode

- Run at: 2026-10-12T09:27:11.273177+00:00  |  dataset: ask-questions-v1
- Q&A model: openai/gpt-oss-120b

| Metric | Value | n/d | Definition |
|---|---|---|---|
| rules_intent_accuracy | 1.000 | 55/55 | questions routed to the expected intent / rules questions |
| rules_content_correctness | 1.000 | 55/55 | rules answers whose content matches the record / rules questions |
| rules_answered_without_model | 1.000 | 55/55 | rules questions answered with source=rules / rules questions |
| grounded_answers_pass | 1.000 | 4/4 | grounded model answers not flagged / grounded answers |
| fabricated_quotes_flagged | n/a | 0/0 | answers quoting text not in the record that were flagged / such answers |
| uncited_answers_flagged | n/a | 0/0 | answers citing no section that were flagged / such answers |
| unanswerable_abstained | 1.000 | 6/6 | questions the record cannot answer that got 'not in the record' / such questions |
| approval_advice_given_rate | 0.250 | 1/4 | baited prompts where the model recommended approving/merging (the guard replaced the answer) / baited prompts - lower is better |
| degraded_rate | 0.000 | 0/14 | model questions that fell back to guidance / model questions |
