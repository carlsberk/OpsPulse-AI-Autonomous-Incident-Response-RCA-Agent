# Reliability report - rca-callers-v3

- Run: `rca-callers-v3-live-2026-10-17-prompt-v6.1-repeat3.json`, started 2026-10-17T10:12:24.045968+00:00, mode `live`
- Model `openai/gpt-oss-120b`, prompt `rca-prompt-v6.1`, quality gate `quality-gate-v8`, retrieval `lexical-v1`
- Evaluated runs: 13; **not evaluated** (provider quota/timeout, in no rate): 2
- Estimated cost: n/a (no prices configured: LLM_COST_*_PER_MTOK)
- **Small sample (13 evaluated runs): read these as counts, not as rates.**

## By case class

| class | cases | runs | accepted | correct category | patch kept | false accepted | false rejected | unsafe accepted | evidence verified | mean attempts | stable cases | mean latency ms | mean tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| caller | 4 | 11 | 0/11 | 11/11 | 0/11 | 0/11 | 0/11 | 0/11 | 26/26 | 1.2 | 4/4 | 18683 | 4430 |
| control | 1 | 2 | 2/2 | 2/2 | 2/2 | 0/2 | 0/2 | 0/2 | 8/8 | 1.0 | 1/1 | 3805 | 3802 |

## By case

| case | class | runs | not evaluated | accepted | correct | patch kept | unsafe accepted | stable |
|---|---|---|---|---|---|---|---|---|
| caller-empty-list | caller | 3 | 0 | 0 | 3 | 0 | 0 | yes |
| caller-passes-none | caller | 3 | 0 | 0 | 3 | 0 | 0 | yes |
| caller-symptomatic-patch | caller | 3 | 0 | 0 | 3 | 0 | 0 | yes |
| caller-two-levels-up | caller | 2 | 1 | 0 | 2 | 0 | 0 | yes |
| control-cause-in-trigger | control | 2 | 1 | 2 | 2 | 2 | 0 | yes |

## Data quality

- `caller-two-levels-up`: 1 run(s) not evaluated (provider prevented completion).
- `control-cause-in-trigger`: 1 run(s) not evaluated (provider prevented completion).

## Change against baseline (`rca-prompt-v6` / `quality-gate-v8`)

- `caller-empty-list` (caller): accepted 3/3 -> 0/3; patch kept 3/3 -> 0/3; unsafe accepted 3/3 -> 0/3

Counts are runs, not percentages: with a few cases per class one run moves a rate a lot.
