# Reliability report - rca-callers-v3

- Run: `rca-callers-v3-live-2026-10-17-prompt-v6.1-repeat3.json`, started 2026-10-17T10:12:24.045968+00:00, mode `live`
- Model `openai/gpt-oss-120b`, prompt `rca-prompt-v6.1`, quality gate `quality-gate-v8`, retrieval `lexical-v1`

## By case class

| class | cases | runs | accepted | correct category | patch kept | false accepted | stable cases | mean latency ms | mean tokens |
|---|---|---|---|---|---|---|---|---|---|
| caller | 4 | 12 | 0/12 | 11/12 | 0/12 | 0/12 | 3/4 | 17246 | 4061 |
| control | 1 | 3 | 2/3 | 2/3 | 2/3 | 0/3 | 0/1 | 3028 | 2534 |

## By case

| case | class | runs | accepted | correct | patch kept | stable |
|---|---|---|---|---|---|---|
| caller-empty-list | caller | 3 | 0 | 3 | 0 | yes |
| caller-passes-none | caller | 3 | 0 | 3 | 0 | yes |
| caller-symptomatic-patch | caller | 3 | 0 | 3 | 0 | yes |
| caller-two-levels-up | caller | 3 | 0 | 2 | 0 | no |
| control-cause-in-trigger | control | 3 | 2 | 2 | 2 | no |

## Change against baseline (`rca-prompt-v6` / `quality-gate-v8`)

- `caller-empty-list` (caller): accepted 3/3 -> 0/3; patch kept 3/3 -> 0/3
- `caller-two-levels-up` (caller): correct category 3/3 -> 2/3
- `control-cause-in-trigger` (control): accepted 3/3 -> 2/3; correct category 3/3 -> 2/3; patch kept 3/3 -> 2/3

Counts are runs, not percentages: with a few cases per class one run moves a rate a lot.
