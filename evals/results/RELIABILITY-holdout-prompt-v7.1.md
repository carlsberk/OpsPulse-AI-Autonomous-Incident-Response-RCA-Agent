# Reliability report - rca-holdout-v2

- Run: `rca-holdout-live-2026-10-23-prompt-v7.1-gate-v8.json`, started 2026-10-23T21:17:11.502249+00:00, mode `live`
- Model `openai/gpt-oss-120b`, prompt `rca-prompt-v7.1`, quality gate `quality-gate-v8`, retrieval `lexical-v1`
- Evaluated runs: 11; **not evaluated** (provider quota/timeout, in no rate): 1
- Estimated cost: n/a (no prices configured: LLM_COST_*_PER_MTOK)
- Ranges are 95 % Wilson intervals. Repeated runs of one case are not independent, so with `--repeat` they are narrower than the truth.
- **Small sample (11 evaluated runs): read these as counts, not as rates.**

## By case class

| class | cases | runs | accepted | correct category | patch kept | false accepted | false rejected | unsafe accepted | evidence verified | mean attempts | stable cases | mean latency ms | mean tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| other | 12 | 11 | 9/11 (52-95%) | 9/11 (52-95%) | 9/11 (52-95%) | 1/11 (2-38%) | 0/11 (0-26%) | 1/11 (2-38%) | 21/21 (85-100%) | 1.0 | 12/12 | 14025 | 4307 |

## By case

| case | class | runs | not evaluated | accepted | correct | patch kept | unsafe accepted | stable |
|---|---|---|---|---|---|---|---|---|
| ho-drift-attribute | other | 0 | 1 | 0 | 0 | 0 | 0 | yes |
| ho-env-int | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-gateway-timeout | other | 1 | 0 | 0 | 0 | 0 | 0 | yes |
| ho-internal-key | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-missing-package | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-none-config | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-off-by-one | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-pool-timeout | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-redis-down | other | 1 | 0 | 0 | 1 | 0 | 0 | yes |
| ho-str-price | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-typo-method | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-upstream-shape | other | 1 | 0 | 1 | 0 | 1 | 1 | yes |

## Data quality

- `ho-drift-attribute`: 1 run(s) not evaluated (provider prevented completion).
- `ho-pool-timeout`: ambiguous label, also accepted: configuration_error. PoolTimeout means the client's connection pool was exhausted. That is a capacity/pool-size setting (configuration_error) as much as a slow provider (dependency_unavailable); live runs consistently chose the former, and a reviewer could defend either, so both are accepted.

Counts are runs, not percentages: with a few cases per class one run moves a rate a lot.
