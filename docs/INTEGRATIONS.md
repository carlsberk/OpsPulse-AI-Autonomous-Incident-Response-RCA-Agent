# Integrations and observability

Sentry and Alertmanager adapters, reviewer notifications, and Langfuse / Prometheus observability. Moved out of the README.

## Sentry integration

`POST /integrations/sentry` accepts Sentry Integration Platform webhooks (`event_alert` and `error` resources;
others return 204). It is designed from Sentry's public webhook documentation and tested with payloads of the
documented shape - **it has not been exercised against a live Sentry organisation**.

1. In Sentry, create an internal integration with a webhook URL `https://<host>/integrations/sentry`, enable
   alert-rule actions and/or the `error` resource, and copy its *Client Secret*.
2. Configure `SENTRY_CLIENT_SECRET` and map Sentry project ids to allow-listed repositories:
   `SENTRY_PROJECT_REPOS=1234567:your-org/your-repo`.
3. Add the integration as an action to an issue alert rule.

The request is authenticated by `Sentry-Hook-Signature` (HMAC-SHA256 with the Client Secret). Sentry's own
sample signs `JSON.stringify(body)`, so both the raw body and its compact JSON form are accepted. Requests over
1 MB get 413, unmapped projects 422, mapped-but-not-allow-listed repositories 403. The event becomes a
queued incident submitted by the identity `sentry` (so a human reviewer can approve its remediation);
`in_app` frames are turned into a Python-style traceback for the parser, and the incident id is derived from
Sentry's `event_id`, so repeated deliveries return the existing incident instead of analysing it twice.

## Alertmanager integration

`POST /integrations/alertmanager` is a Prometheus Alertmanager `webhook_config` receiver (payload version 4),
authenticated with an ordinary reporter API key sent as a Bearer token (every API endpoint accepts
`Authorization: Bearer <key>` as well as `X-API-Key`). Tested with payloads of the documented shape, **not against
a running Alertmanager**.

```yaml
receivers:
  - name: opspulse
    webhook_configs:
      - url: https://<host>/integrations/alertmanager
        http_config:
          authorization:
            credentials_file: /etc/alertmanager/opspulse-key   # key from: python -m scripts.make_api_key alertmanager reporter
```

Each *firing* alert becomes one incident (resolved alerts are ignored; at most 20 per notification, the rest are
counted as `dropped`). The repository comes from the alert label `repository` (`ALERTMANAGER_REPO_LABEL`) and must
be allow-listed; the message from `alertname`, `summary` and `description`; the stack trace from an optional
`stack_trace` annotation. The response lists each alert as `queued`, `duplicate` or `skipped` (with the reason), so
one alert with a missing label does not fail the batch. Incident ids derive from the alert fingerprint and start
time: Alertmanager's repeat notifications do not create new analyses, and a 503 (database down) is safe to retry.
**An alert without a stack trace usually ends as "insufficient evidence"** - the agent does not invent a cause from
a metric threshold.

## Reviewer notifications

Set `NOTIFY_WEBHOOK_URL` (an `https://` incoming-webhook URL; Slack and Mattermost accept the `{"text": ...}`
payload) to be told when an incident reaches a status in `NOTIFY_ON_STATUSES` (default
`awaiting_approval,failed`). With `PUBLIC_BASE_URL` the message links to the review console. The message
contains only status, repository, a sanitised one-line title (no `@`-mentions or link markup from model text),
quality score and category - never stack traces, source code or patches. Delivery failures are logged and
counted (`opspulse_notifications_total`) and never affect incident processing.

## Observability (Langfuse)

Verified against **langfuse 4.17.0** (the SDK uses OpenTelemetry; the code uses `Langfuse(mask=...)`,
`start_as_current_observation` and `propagate_attributes`). With keys unset, everything is a no-op.

| Observation | Type | Content |
|---|---|---|
| `incident.process` (trace `opspulse.incident`, session = incident id) | chain | versions, final status, attempts, error category |
| graph node spans + LLM generation | via `langfuse.langchain.CallbackHandler` | model, latency, token usage reported by Groq |
| `retrieval.source_context`, `retrieval.historical_incidents` | retriever | found / match count / failure category |
| `evaluation.quality_gate` | evaluator | score, passed, attempt |
| `github.create_pull_request` | tool | outcome / error category |

**Privacy:** a mask function runs on every payload before export: credential-shaped strings are redacted and,
unless `LANGFUSE_CAPTURE_CONTENT=true`, any string longer than 200 characters (prompts, stack traces, source,
model output) is replaced by `[content omitted: N chars]`. Telemetry failures are logged and never break
processing. *Not verified against a live Langfuse project* (no keys were available); behaviour is covered by
unit tests with a fake client.

### Metrics

| Metric | Type | Labels |
|---|---|---|
| `opspulse_incidents_finished_total` | counter | `status`, `error_category` |
| `opspulse_pipeline_duration_seconds` | histogram | - |
| `opspulse_llm_attempts_total` | counter | `outcome` (valid / schema_invalid / malformed / provider_error) |
| `opspulse_llm_call_duration_seconds` | histogram | - |
| `opspulse_llm_tokens_total` | counter | `direction` (input / output, as reported by the provider) |
| `opspulse_quality_gate_evaluations_total` | counter | `result` (passed / rejected) |
| `opspulse_remediation_decisions_total` | counter | `decision` |
| `opspulse_queue_depth` | gauge | - (sampled by the worker) |
| `opspulse_jobs_recovered_total` | counter | `outcome` (requeued / failed) |
| `opspulse_notifications_total` | counter | `outcome` (sent / failed) |
| `opspulse_jobs_deferred_total` | counter | `error_category` (transient provider errors) |

Labels never contain repository names, identities, paths or error text. Counters are per process.
Example alert rules: [`deploy/prometheus/alerts.yml`](../deploy/prometheus/alerts.yml) (validated with `promtool`
in CI); what to do when they fire: [`docs/RUNBOOK.md`](RUNBOOK.md).
