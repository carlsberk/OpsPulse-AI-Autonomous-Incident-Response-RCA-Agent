# A worked example: reading one incident

This page walks through the reviewer-facing records of **one real run** of the end-to-end flow
(`python -m scripts.e2e_flow --dump docs/examples/e2e-trace.json`; the full file is
[`docs/examples/e2e-trace.json`](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/blob/main/docs/examples/e2e-trace.json)).

!!! warning "What is real here and what is not"
    The shapes, fields and the pipeline are real. The **model is a scripted stand-in** and GitHub is a recorder, so the
    values illustrate the format; they say nothing about model quality. The `model` field below shows the *configured*
    model name (`openai/gpt-oss-120b`), but in this run no model was called: latency is `0` and token counts are `null`
    for that reason.

The service under test is `examples/demo_service`: an order for an unknown SKU makes `inventory.py` compare `None` with an
integer (`TypeError`). Alertmanager reports it; the worker analyses it; a reviewer approves one draft PR.

## 1. `GET /incidents/{id}/trace`: what ran

```json
{
  "request_id": "fb125e79bb3749b290522c05bdbae3ac",
  "status": "awaiting_approval",
  "versions": {
    "agent_version": "1.9.0-opspulse",
    "prompt_version": "rca-prompt-v7.1",
    "evaluator_version": "quality-gate-v8",
    "retrieval_strategy": "lexical-v1",
    "model": "openai/gpt-oss-120b"
  },
  "attempts": [
    { "iteration": 1, "latency_ms": 0, "schema_valid": true, "gate_score": 0.985,
      "gate_passed": true, "failed_checks": ["self_assessment"], "decision": "quality gate passed" }
  ],
  "totals": { "llm_calls": 1, "estimated_cost_usd": null }
}
```

- `request_id` is the `X-Request-ID` of the request that submitted the incident, and the same id appears as `request_id`
  in the JSON logs. It is the handle for "show me everything about this incident".
- `versions` pins the exact agent, prompt, quality gate and model, so a result can be compared with another version's.
- Each `attempts` entry is one model call and the gate's verdict on it. `failed_checks` lists checks that scored below
  full marks even though the gate passed (here `self_assessment`: that check scores the model's own, uncalibrated confidence, which was below 1.0).
- `estimated_cost_usd` is `null` unless both `LLM_COST_*_PER_MTOK` prices are configured; no price is assumed.

## 2. `GET /incidents/{id}/evidence-graph`: why to believe it

```json
{
  "summary": { "evidence": 3, "observed": 2, "observed_verified": 2, "anchoring_patch": 0 },
  "flags": ["no evidence quote touches the code the patch changes"]
}
```

Every piece of evidence has a status: `verified_in_record` (the quote was re-checked here against the stored trace or the
stored code window), `verified_by_gate` (the gate matched it in text that is not stored), `unverified`, or `not_checked`
(an inference or hypothesis, never quote-checked). Both observed quotes are verified.

The **flag is the interesting part**: the scripted analysis quotes the caller's line, while the patch changes the callee.
The evidence graph says so instead of staying silent. A reviewer should read that as "the diagnosis and the change are
not tied together by any quoted line; check where the bad value really comes from before approving".

## 3. `GET /incidents/{id}/guidance`: what to do now

Headline: **"A patch waits for your decision"** (state `attention`). The card lists the reviewer's checks (read the
quotes and the patch; the approval is bound to this exact patch; you cannot decide on incidents you submitted), notes
that the patch was not executed, and repeats that a gate score is a rubric score, not a probability. It is derived from the
stored record and the runbook without a model, so it is available even when the provider is down.

## 4. `GET /incidents/{id}/audit`: who did what

| event | actor | detail |
|---|---|---|
| `submitted` | alice | incident reported for demo/inventory |
| `analysed` | agent | gate score 0.985, 1 attempt; model, prompt and gate versions |
| `proposed` | agent | open a draft PR changing `inventory.py`; patch SHA-256 `b46e89c6…`; expires in 72 h |
| `approved` | bob | "guard matches the existing contract"; the same SHA-256 |
| `pr_opened` | system | draft PR on branch `opspulse/fix-…` |

The response also carries `chain`: the approval log is a hash chain (here 3 entries: proposed, approved, executed), and
`status: intact` means no entry was edited, removed from the middle or reordered, and the approval rows agree with it.
Keep the `head` hash somewhere the database administrator cannot write if you need to notice the newest entries being cut.

Two rules are visible here: the submitter (`alice`, a reporter) is not the decider (`bob`, a reviewer), and the approval
carries the hash of the patch that was proposed. Merging is not an event, because this service never merges.

## 5. Debugging a failed incident, without exposing data

1. `GET /incidents/{id}` gives `status`, `error_category` and `status_reason` (for example `llm_rate_limited`,
   `time_budget_exhausted`, `retry_budget_exhausted`, `source_unavailable`). None of these contain source code or secrets.
2. `GET /incidents/{id}/guidance` says what the category means and what to do; the [runbook](RUNBOOK.md) has the procedure.
3. `GET /incidents/{id}/trace` shows which attempt failed which gate check, and the model-time it used.
4. In the JSON logs filter by `incident_id` or `request_id`: messages pass through the redaction filter, and they are written to record events and categories,
   not request bodies.
5. `POST /incidents/{id}/retry` (reviewer role) puts a `failed` incident back in the queue.

The stored code windows the model saw are **not** returned by any of these endpoints.
