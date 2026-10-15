# Reliability report - rca-holdout-v1

- Run: `rca-holdout-live-2026-10-15-prompt-v6-gate-v8.json`, started 2026-10-15T21:20:08.330859+00:00, mode `live`
- Model `openai/gpt-oss-120b`, prompt `rca-prompt-v6`, quality gate `quality-gate-v8`, retrieval `lexical-v1`

## By case class

| class | cases | runs | accepted | correct category | patch kept | false accepted | stable cases | mean latency ms | mean tokens |
|---|---|---|---|---|---|---|---|---|---|
| other | 12 | 12 | 9/12 | 11/12 | 11/12 | 1/12 | 12/12 | 22722 | 5253 |

## By case

| case | class | runs | accepted | correct | patch kept | stable |
|---|---|---|---|---|---|---|
| ho-drift-attribute | other | 1 | 0 | 1 | 1 | yes |
| ho-env-int | other | 1 | 1 | 1 | 1 | yes |
| ho-gateway-timeout | other | 1 | 0 | 1 | 0 | yes |
| ho-internal-key | other | 1 | 1 | 1 | 1 | yes |
| ho-missing-package | other | 1 | 1 | 1 | 1 | yes |
| ho-none-config | other | 1 | 1 | 1 | 1 | yes |
| ho-off-by-one | other | 1 | 0 | 1 | 1 | yes |
| ho-pool-timeout | other | 1 | 1 | 0 | 1 | yes |
| ho-redis-down | other | 1 | 1 | 1 | 1 | yes |
| ho-str-price | other | 1 | 1 | 1 | 1 | yes |
| ho-typo-method | other | 1 | 1 | 1 | 1 | yes |
| ho-upstream-shape | other | 1 | 1 | 1 | 1 | yes |

## Change against baseline (`rca-prompt-v5` / `quality-gate-v7`)

- `ho-off-by-one` (other): accepted 2/3 -> 0/1

Counts are runs, not percentages: with a few cases per class one run moves a rate a lot.
