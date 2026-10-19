# Reliability report - rca-holdout-v1

- Run: `rca-holdout-live-2026-10-15-prompt-v6-gate-v8.json`, started 2026-10-15T21:20:08.330859+00:00, mode `live`
- Model `openai/gpt-oss-120b`, prompt `rca-prompt-v6`, quality gate `quality-gate-v8`, retrieval `lexical-v1`
- Evaluated runs: 11; **not evaluated** (provider quota/timeout, in no rate): 1
- Estimated cost: n/a (no prices configured: LLM_COST_*_PER_MTOK)
- **Small sample (11 evaluated runs): read these as counts, not as rates.**

## By case class

| class | cases | runs | accepted | correct category | patch kept | false accepted | false rejected | unsafe accepted | evidence verified | mean attempts | stable cases | mean latency ms | mean tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| other | 12 | 11 | 9/11 | 9/11 | 10/11 | 1/11 | 1/11 | 1/11 | 25/25 | 1.4 | 12/12 | 20753 | 5382 |

## By case

| case | class | runs | not evaluated | accepted | correct | patch kept | unsafe accepted | stable |
|---|---|---|---|---|---|---|---|---|
| ho-drift-attribute | other | 0 | 1 | 0 | 0 | 0 | 0 | yes |
| ho-env-int | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-gateway-timeout | other | 1 | 0 | 0 | 0 | 0 | 0 | yes |
| ho-internal-key | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-missing-package | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-none-config | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-off-by-one | other | 1 | 0 | 0 | 1 | 1 | 0 | yes |
| ho-pool-timeout | other | 1 | 0 | 1 | 0 | 1 | 1 | yes |
| ho-redis-down | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-str-price | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-typo-method | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |
| ho-upstream-shape | other | 1 | 0 | 1 | 1 | 1 | 0 | yes |

## Data quality

- `ho-drift-attribute`: 1 run(s) not evaluated (provider prevented completion).

## Change against baseline (`rca-prompt-v5` / `quality-gate-v7`)

- `ho-off-by-one` (other): accepted 2/3 -> 0/1

Counts are runs, not percentages: with a few cases per class one run moves a rate a lot.
