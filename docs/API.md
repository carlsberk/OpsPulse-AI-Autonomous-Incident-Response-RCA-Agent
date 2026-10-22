# API reference

Generated from the application's OpenAPI schema by `python -m scripts.export_api_docs` (a unit test fails when this file is out of date). The live, interactive version is served at `/docs`.

**Authentication.** Send an API key as `X-API-Key: <key>` or `Authorization: Bearer <key>`. Roles: `reporter` (submit and read), `reviewer` (also decide remediation), `admin`. The Sentry webhook is authenticated by its HMAC signature instead.

## Incidents

### `GET /incidents`

Newest first. Filter e.g. `?status=awaiting_approval` to find proposals waiting for review.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `status` | query | one of `queued`, `processing`, `failed`, `needs_review`, `analysis_ready`, `awaiting_approval`, `remediation_rejected`, `pr_created`, `pr_skipped_duplicate`, `pr_failed` or null |  |  |
| `repo_name` | query | string or null |  |  |
| `limit` | query | integer |  |  |
| `cursor` | query | string or null |  |  |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 400 | Bad Request |
| 422 | Validation Error |

### `POST /webhook/incident`

Accept an incident report (roles: reporter, reviewer, admin).

The incident is queued and analysed by a worker (poll `GET /incidents/{id}`), or synchronously with `wait=true`.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `wait` | query | boolean |  | Run the analysis synchronously and return the full result |

**JSON body**

| Field | Type | Required | Description |
|---|---|---|---|
| `repo_name` | string | yes | GitHub 'owner/repo'. Must be listed in ALLOWED_REPOSITORIES. |
| `error_message` | string | yes |  |
| `stack_trace` | string |  |  |
| `commit_sha` | string or null |  | Git commit the failing code was deployed from. When given, source is read at this commit instead of the default branch head (the deployed code may differ from it). A commit that cannot be read sends the incident to review; it is never replaced by the head. |
| `incident_id` | string or null |  | Optional client-generated id. Re-sending the same id is idempotent (no re-analysis). |

| Status | Meaning |
|---|---|
| 202 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 200 | Synchronous result (wait=true) or existing incident for a repeated incident_id |
| 409 | Conflict |
| 429 | Per-identity rate limit exceeded (see Retry-After) |
| 422 | Validation Error |

### `GET /incidents/{incident_id}`

Read Incident

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `incident_id` | path | string | yes |  |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 404 | Not Found |
| 422 | Validation Error |

### `POST /incidents/{incident_id}/retry`

Re-queue a `failed` incident (roles: reviewer, admin), e.g. after a provider quota reset.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `incident_id` | path | string | yes |  |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 404 | Not Found |
| 409 | Conflict |
| 422 | Validation Error |

### `POST /incidents/{incident_id}/ask`

Ask a question about one incident (roles: reporter, reviewer, admin).

Common questions (why not accepted, what to do next, what the patch changes) are answered by rules from the
record without an LLM (`source=rules`); others go to the model, which must cite the record sections it used and
whose quoted text is verified against the record. If the model is unavailable the deterministic guidance is
returned (`degraded=true`). The server keeps no conversation state; `history` is client-held and untrusted. No
answer triggers an action, and none was executed or verified.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `incident_id` | path | string | yes |  |

**JSON body**

| Field | Type | Required | Description |
|---|---|---|---|
| `question` | string | yes | A question about this incident's record |
| `history` | list of AskTurn |  | Earlier turns of the conversation (client-held, untrusted); only used to resolve references |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 404 | Not Found |
| 429 | Question rate limit exceeded (see Retry-After) |
| 502 | The LLM provider could not answer |
| 504 | The LLM provider timed out |
| 422 | Validation Error |

### `GET /incidents/{incident_id}/trace`

Versions, per-attempt latency/tokens and gate verdicts of one incident's run, from the stored record.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `incident_id` | path | string | yes |  |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 404 | Not Found |
| 422 | Validation Error |

### `GET /incidents/{incident_id}/evidence-graph`

Claim -> evidence -> patch -> file links with a verification status on each piece of evidence and explicit
flags for missing, unverified or conflicting evidence. Derived from the stored record; no LLM.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `incident_id` | path | string | yes |  |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 404 | Not Found |
| 422 | Validation Error |

### `GET /incidents/{incident_id}/audit`

Review timeline: submission, analysis versions, every proposal and who decided it (with the note), and the
resulting pull request. Derived from stored rows; merging is never done by this service.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `incident_id` | path | string | yes |  |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 404 | Not Found |
| 422 | Validation Error |

### `GET /incidents/{incident_id}/guidance`

What state the incident is in, why, and what a person can do next - derived deterministically from the
stored record and the runbook (no LLM, always available). Never claims a fix is correct.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `incident_id` | path | string | yes |  |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 404 | Not Found |
| 422 | Validation Error |

## Remediation decisions

### `POST /incidents/{incident_id}/remediation/decision`

Approve or reject the PR proposed for an incident (roles: reviewer, admin).

The request must name the pending approval and echo the SHA-256 of the proposed patch, so a decision can
only apply to the exact change the reviewer saw. The reviewer is the authenticated identity, and it may not
be the identity that submitted the incident (four-eyes rule). Decisions are final; a repeat returns 409.

**Auth:** API key or Bearer token

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `incident_id` | path | string | yes |  |

**JSON body**

| Field | Type | Required | Description |
|---|---|---|---|
| `approval_id` | string | yes |  |
| `patch_sha256` | string | yes |  |
| `decision` | one of `approve`, `reject` | yes |  |
| `note` | string or null |  |  |

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 401 | Missing or invalid API key |
| 403 | Role not allowed, repository not allowed, or self-approval |
| 503 | Database unavailable or authentication not configured |
| 404 | Not Found |
| 409 | Conflict |
| 422 | Validation Error |

## Inbound integrations

### `POST /integrations/sentry`

Sentry issue-alert / error webhooks. Authenticated by `Sentry-Hook-Signature` (HMAC of the body with the
integration's Client Secret); the Sentry project must be mapped to an allow-listed repository.

**Auth:** none (see description)

| Status | Meaning |
|---|---|
| 202 | Successful Response |
| 200 | Repeated delivery of an already-received event |
| 204 | Webhook resource that does not describe an error (ignored) |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 413 | Payload too large |
| 422 | Unprocessable request |
| 429 | Too Many Requests |

### `POST /integrations/alertmanager`

Prometheus Alertmanager `webhook_config` receiver (payload version 4), authenticated with a reporter API key
as a Bearer token. One incident per firing alert; the repository comes from the `ALERTMANAGER_REPO_LABEL`
label (default `repository`) and must be allow-listed. Repeated notifications are idempotent.

**Auth:** API key or Bearer token

| Status | Meaning |
|---|---|
| 202 | Successful Response |
| 401 | Unauthorized |
| 413 | Payload too large |
| 422 | Unprocessable request |
| 429 | Too Many Requests |
| 503 | Service Unavailable |

## Health and metrics

### `GET /healthz`

Liveness: the process is up. Does not touch external services.

**Auth:** none (see description)

| Status | Meaning |
|---|---|
| 200 | Successful Response |

### `GET /readyz`

Readiness: the database answers. Reports only ok/unavailable, never connection details.

**Auth:** none (see description)

| Status | Meaning |
|---|---|
| 200 | Successful Response |
| 503 | Service Unavailable |

### `GET /metrics`

Prometheus metrics: counters/histograms with status and outcome labels only (no content, no identities).

**Auth:** none (see description)

| Status | Meaning |
|---|---|
| 200 | Successful Response |
