# Reliability report - rca-holdout-v2

- Run: `rca-holdout-live-2026-10-24-prompt-v7.1-gate-v8.json`, started 2026-10-24T12:25:25.982319+00:00, mode `live`
- Model `openai/gpt-oss-120b`, prompt `rca-prompt-v7.1`, quality gate `quality-gate-v8`, retrieval `lexical-v1`
- Evaluated runs: 12; **not evaluated** (provider quota/timeout, in no rate): 0
- Estimated cost: n/a (no prices configured: LLM_COST_*_PER_MTOK)
- Ranges are 95 % Wilson intervals. Repeated runs of one case are not independent, so with `--repeat` they are narrower than the truth.
- **Small sample (12 evaluated runs): read these as counts, not as rates.**

## By case class

| class | cases | runs | accepted | correct category | patch kept | false accepted | false rejected | unsafe accepted | evidence verified | mean attempts | stable cases | mean latency ms | mean tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| other | 12 | 12 | 10/12 (55-95%) | 9/12 (47-91%) | 10/12 (55-95%) | 1/12 (1-35%) | 0/12 (0-24%) | 1/12 (1-35%) | 23/23 (86-100%) | 1.2 | 12/12 | 23213 | 5100 |

## By case

| case | class | runs | not evaluated | accepted | correct | patch kept | unsafe accepted | stable |
|---|---|---|---|---|---|---|---|---|
| ho-drift-attribute | other | 1 | 0 | 0 | 0 | 0 | 0 | yes |
| ho-env-int | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-gateway-timeout | other | 1 | 0 | 0 | 0 | 0 | 0 | yes |
| ho-internal-key | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-missing-package | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-none-config | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-off-by-one | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-pool-timeout | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-redis-down | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-str-price | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-typo-method | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-upstream-shape | other | 1 | 0 | 1 | 0 | 1 | 1 | yes |

## Data quality

- `ho-pool-timeout`: ambiguous label, also accepted: configuration_error. PoolTimeout means the client's connection pool was exhausted. That is a capacity/pool-size setting (configuration_error) as much as a slow provider (dependency_unavailable); live runs consistently chose the former, and a reviewer could defend either, so both are accepted.

## Change against baseline (`rca-prompt-v7.1` / `quality-gate-v8`)

- `ho-redis-down` (other): accepted 0/1 -> 1/1; patch kept 0/1 -> 1/1

Counts are runs, not percentages: with a few cases per class one run moves a rate a lot.
