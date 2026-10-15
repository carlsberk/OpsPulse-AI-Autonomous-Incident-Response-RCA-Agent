# Reliability report - rca-callers-v3

- Run: `rca-callers-v3-live-2026-10-15-prompt-v6-repeat3.json`, started 2026-10-15T20:26:40.828989+00:00, mode `live`
- Model `openai/gpt-oss-120b`, prompt `rca-prompt-v6`, quality gate `quality-gate-v8`, retrieval `lexical-v1`

## By case class

| class | cases | runs | accepted | correct category | patch kept | false accepted | stable cases | mean latency ms | mean tokens |
|---|---|---|---|---|---|---|---|---|---|
| caller | 4 | 12 | 3/12 | 12/12 | 3/12 | 0/12 | 4/4 | 12952 | 3763 |
| control | 1 | 3 | 3/3 | 3/3 | 3/3 | 0/3 | 1/1 | 15883 | 3872 |

## By case

| case | class | runs | accepted | correct | patch kept | stable |
|---|---|---|---|---|---|---|
| caller-empty-list | caller | 3 | 3 | 3 | 3 | yes |
| caller-passes-none | caller | 3 | 0 | 3 | 0 | yes |
| caller-symptomatic-patch | caller | 3 | 0 | 3 | 0 | yes |
| caller-two-levels-up | caller | 3 | 0 | 3 | 0 | yes |
| control-cause-in-trigger | control | 3 | 3 | 3 | 3 | yes |

## Change against baseline (`rca-prompt-v5.1` / `quality-gate-v8`)

- `caller-passes-none` (caller): accepted 2/2 -> 0/3; patch kept 2/2 -> 0/3
- `caller-symptomatic-patch` (caller): accepted 2/2 -> 0/3; patch kept 2/2 -> 0/3
- `caller-two-levels-up` (caller): accepted 2/2 -> 0/3; patch kept 2/2 -> 0/3

Counts are runs, not percentages: with a few cases per class one run moves a rate a lot.
