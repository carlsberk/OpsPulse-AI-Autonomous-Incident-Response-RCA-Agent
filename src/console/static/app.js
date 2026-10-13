// OpsPulse review console. Plain DOM APIs only: every value from the API (which includes LLM output and
// untrusted log text) is rendered with textContent, never innerHTML. No inline script or style (strict CSP).
"use strict";

const KEY_STORAGE = "opspulse.apiKey";      // sessionStorage: dies with the tab
const THEME_STORAGE = "opspulse.theme";      // localStorage: auto | light | dark
const REFRESH_STORAGE = "opspulse.autoRefresh";
const REFRESH_MS = 15000;
const THEMES = ["auto", "light", "dark"];
const THEME_GLYPH = { auto: "◐", light: "☀", dark: "☾" };
const STATUS_TONE = {
  awaiting_approval: "s-warn", needs_review: "s-warn", processing: "s-neutral", analysis_ready: "s-ok",
  pr_created: "s-ok", pr_skipped_duplicate: "s-ok", remediation_rejected: "s-neutral", pr_failed: "s-bad",
  failed: "s-bad", queued: "s-neutral",
};

const $ = (id) => document.getElementById(id);
const state = {
  askFor: null, askHistory: [], cursor: null, items: [], current: null, evidenceFilter: "all", loading: false, focusIndex: -1, memoryKey: "",
};

// ------------------------------------------------------------------ small helpers

function store(kind, name, value) {
  try {
    const area = kind === "session" ? sessionStorage : localStorage;
    if (value === undefined) return area.getItem(name) || "";
    if (value) area.setItem(name, value); else area.removeItem(name);
  } catch { /* storage unavailable (private mode, blocked): the console still works without it */ }
  return "";
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

function humanize(value) { return String(value || "").replaceAll("_", " "); }

function badge(status) {
  return el("span", humanize(status), `badge ${STATUS_TONE[status] || "s-neutral"}`);
}

function toneForScore(score) {
  if (score >= 0.85) return "ok";
  if (score >= 0.5) return "warn";
  return "bad";
}

function meter(fraction, tone) {
  const bar = el("span", null, `bar ${tone}`);
  const fill = document.createElement("span");
  fill.style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`; // CSSOM: allowed by the CSP
  bar.append(fill);
  return bar;
}

function ago(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const seconds = (Date.now() - date.getTime()) / 1000;
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} d ago`;
  return date.toLocaleDateString();
}

function fullDate(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso || "") : date.toLocaleString();
}

function toast(text, isError = false) {
  const node = el("div", text, isError ? "toast error" : "toast");
  $("toasts").append(node);
  setTimeout(() => node.remove(), isError ? 7000 : 3500);
}

function setMessage(id, text, isError = false) {
  const node = $(id);
  node.textContent = text || "";
  node.classList.toggle("error", isError);
}

function setConnection(kind, text) {
  const node = $("conn");
  node.textContent = text;
  node.className = `conn conn-${kind}`;
}

// ------------------------------------------------------------------ API

function apiKey() { return store("session", KEY_STORAGE) || state.memoryKey; }

function setKey(value) {
  state.memoryKey = value;
  store("session", KEY_STORAGE, value || null);
  $("forget-key").hidden = !value;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "X-API-Key": apiKey(), "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let body = null;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const error = new Error(body && body.error ? body.error.message : `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function failure(error, messageId) {
  if (error.status === 401) {
    setConnection("bad", "Invalid key");
    setMessage(messageId, "Enter a valid API key to continue.", true);
  } else if (error.status === 403) {
    setMessage(messageId, "This key may not perform that action.", true);
  } else {
    setConnection("bad", "Error");
    setMessage(messageId, error.message, true);
  }
}

// ------------------------------------------------------------------ theme

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") root.dataset.theme = mode; else delete root.dataset.theme;
  const button = $("theme");
  button.textContent = THEME_GLYPH[mode] || THEME_GLYPH.auto;
  button.setAttribute("aria-label", `Colour theme: ${mode === "auto" ? "automatic" : mode}`);
  button.title = `Colour theme: ${mode === "auto" ? "automatic" : mode} (click to change)`;
}

function cycleTheme() {
  const current = store("local", THEME_STORAGE) || "auto";
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
  store("local", THEME_STORAGE, next === "auto" ? null : next);
  applyTheme(next);
}

// ------------------------------------------------------------------ list

function visibleItems() {
  const query = $("search").value.trim().toLowerCase();
  if (!query) return state.items;
  return state.items.filter((item) => [item.repo_name, item.affected_file, item.submitted_by, item.status,
    item.incident_id, item.error_category].some((value) => String(value || "").toLowerCase().includes(query)));
}

function skeletonRows() {
  const body = $("incident-table").querySelector("tbody");
  body.replaceChildren();
  for (let i = 0; i < 5; i += 1) {
    const row = document.createElement("tr");
    row.className = "skeleton";
    for (let c = 0; c < 5; c += 1) {
      const cell = document.createElement("td");
      cell.append(document.createElement("span"));
      row.append(cell);
    }
    body.append(row);
  }
  $("list-card").hidden = false;
  $("empty").hidden = true;
}

async function loadList(append = false) {
  if (state.loading) return;
  state.loading = true;
  const params = new URLSearchParams({ limit: "20" });
  const status = $("status-filter").value;
  if (status) params.set("status", status);
  if (append && state.cursor) params.set("cursor", state.cursor);
  if (!append && state.items.length === 0) skeletonRows();
  setMessage("list-message", "");
  try {
    const page = await api(`/incidents?${params}`);
    state.items = append ? state.items.concat(page.items) : page.items;
    state.cursor = page.next_cursor;
    $("load-more").hidden = !page.next_cursor;
    setConnection("on", "Connected");
    renderRows();
  } catch (error) {
    state.items = [];
    $("list-card").hidden = true;
    $("empty").hidden = true;
    $("load-more").hidden = true;
    $("list-summary").textContent = "";
    failure(error, "list-message");
  } finally {
    state.loading = false;
  }
}

function renderRows() {
  const body = $("incident-table").querySelector("tbody");
  body.replaceChildren();
  const items = visibleItems();
  state.focusIndex = -1;
  for (const item of items) {
    const row = document.createElement("tr");
    row.tabIndex = 0;
    row.dataset.id = item.incident_id;

    const statusCell = document.createElement("td");
    statusCell.append(badge(item.status));
    if (item.error_category) statusCell.append(el("span", humanize(item.error_category), "file"));

    const repoCell = document.createElement("td");
    const link = el("a", item.repo_name, "repo repo-link");
    link.href = `#/incident/${encodeURIComponent(item.incident_id)}`;
    repoCell.append(link, el("span", item.affected_file || "no source file", "file mono"));

    const scoreCell = document.createElement("td");
    const score = Number(item.quality_score) || 0;
    const box = el("div", null, "score-cell");
    box.title = "Deterministic rubric score from the quality gate, not a probability of being correct";
    box.append(el("span", score.toFixed(2), "score-num"), meter(score, toneForScore(score)));
    scoreCell.append(box);

    const when = el("td", ago(item.created_at));
    when.title = fullDate(item.created_at);
    if (item.submitted_by) when.append(el("span", item.submitted_by, "file"));

    row.append(statusCell, repoCell, scoreCell, el("td", item.iterations, "num"), when);
    row.addEventListener("click", (event) => {
      if (event.target.closest("a")) return; // the link navigates by itself (middle-click, copy link, ...)
      location.hash = `#/incident/${encodeURIComponent(item.incident_id)}`;
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter") location.hash = `#/incident/${encodeURIComponent(item.incident_id)}`;
    });
    body.append(row);
  }

  const total = state.items.length;
  const awaiting = state.items.filter((i) => i.status === "awaiting_approval").length;
  $("list-summary").textContent = total
    ? `${total} loaded${items.length !== total ? ` (${items.length} match the search)` : ""} · ${awaiting} awaiting approval`
    : "";
  $("list-card").hidden = items.length === 0;
  $("empty").hidden = items.length !== 0;
  if (items.length === 0) {
    const searching = total > 0;
    $("empty-title").textContent = searching ? "No loaded incident matches your search" : "No incidents match this filter";
    $("empty-text").textContent = searching
      ? "Clear the search box or load older incidents."
      : "Incidents appear here after an application, Sentry or Alertmanager reports a crash.";
  }
}

function moveFocus(delta) {
  const rows = [...$("incident-table").querySelectorAll("tbody tr[data-id]")];
  if (!rows.length) return;
  state.focusIndex = Math.max(0, Math.min(rows.length - 1, state.focusIndex + delta));
  rows.forEach((row, i) => row.classList.toggle("focused", i === state.focusIndex));
  rows[state.focusIndex].focus();
  rows[state.focusIndex].scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------------ detail

async function showDetail(incidentId) {
  $("list-view").hidden = true;
  $("detail-view").hidden = false;
  setMessage("a-message", "Loading…");
  try {
    const incident = await api(`/incidents/${encodeURIComponent(incidentId)}`);
    state.current = incident;
    setConnection("on", "Connected");
    setMessage("a-message", "");
    renderDetail(incident);
    window.scrollTo(0, 0);
  } catch (error) {
    $("d-title").textContent = "Incident could not be loaded";
    failure(error, "a-message");
  }
}

function showList() {
  document.title = "OpsPulse Console";
  $("detail-view").hidden = true;
  $("list-view").hidden = false;
  loadList();
}

function route() {
  const match = location.hash.match(/^#\/incident\/([0-9A-Fa-f-]{8,64})$/);
  if (match) showDetail(match[1]); else showList();
}

function usageSummary(attempts) {
  const called = attempts.filter((a) => a.latency_ms !== undefined);
  if (!called.length) return "-";
  const sum = (key) => called.reduce((total, a) => total + (Number(a[key]) || 0), 0);
  const tokens = sum("input_tokens") + sum("output_tokens");
  return `${called.length} call(s) · ${tokens.toLocaleString()} tokens · ${(sum("latency_ms") / 1000).toFixed(1)} s`;
}

function fillList(id, values) {
  const list = $(id);
  list.replaceChildren();
  for (const value of values || []) list.append(el("li", value));
  if (!list.children.length) list.append(el("li", "none", "muted"));
}

function renderDetail(incident) {
  const analysis = incident.analysis || {};
  const summary = analysis.incident_summary || {};
  const meta = analysis.execution_metadata || {};
  const evaluation = analysis.evaluation || {};
  const flow = analysis.control_flow || {};
  const root = (analysis.diagnostic_chain || {}).primary_root_cause || {};
  const attempts = analysis.attempts || [];

  $("d-title").textContent = summary.title || String(incident.error_message || "").split("\n")[0] || "Incident";
  document.title = `${$("d-title").textContent} - OpsPulse`;
  $("d-status").replaceWith(Object.assign(badge(incident.status), { id: "d-status" }));
  const severity = $("d-severity");
  severity.hidden = !summary.severity;
  severity.textContent = summary.severity ? `severity ${String(summary.severity).toLowerCase()}` : "";
  $("d-id").textContent = incident.incident_id;

  const pr = $("d-pr");
  const safePr = typeof incident.pr_url === "string" && incident.pr_url.startsWith("https://");
  pr.hidden = !safePr;
  if (safePr) pr.href = incident.pr_url;

  const reason = [humanize(incident.status_reason), incident.error_category && `(${humanize(incident.error_category)})`];
  if (incident.available_at) reason.push(`- retry not before ${fullDate(incident.available_at)}`);
  const reasonText = reason.filter(Boolean).join(" ");
  const banner = $("d-reason");
  banner.hidden = !reasonText;
  banner.textContent = reasonText;
  banner.classList.toggle("bad", incident.status === "failed" || incident.status === "pr_failed");

  $("d-repo").textContent = incident.repo_name;
  $("d-file").textContent = incident.affected_file || "-";
  $("d-category").textContent = humanize(analysis.root_cause_category) || "-";
  const score = Number(incident.quality_score) || 0;
  $("d-score").textContent = `${score.toFixed(2)} after ${incident.iterations} attempt(s)`;
  const confidence = flow.self_assessed_confidence;
  $("d-confidence").textContent = typeof confidence === "number" ? `${confidence.toFixed(2)} (uncalibrated)` : "-";
  $("d-submitter").textContent = incident.submitted_by || "-";
  $("d-usage").textContent = usageSummary(attempts);
  $("d-versions").textContent = [meta.model, meta.prompt_version, meta.evaluator_version].filter(Boolean).join(" · ") || "-";
  $("d-root").textContent = root.technical_explanation || "No analysis available.";

  renderEvidence(analysis.evidence || []);
  fillList("d-uncertainties", analysis.uncertainties);
  fillList("d-tests", analysis.tests_to_run);
  renderChecks(evaluation);
  renderAttempts(attempts);
  const withheld = [...attempts].reverse().find((a) => a.withheld_patch);
  const showWithheld = !incident.suggested_patch && withheld;
  $("d-patch-title").textContent = showWithheld ? "Withheld patch (not proposed)" : "Proposed patch";
  renderDiff(incident.suggested_patch || (withheld ? withheld.withheld_patch : null));
  $("d-error").textContent = incident.error_message || "";

  $("retry").hidden = incident.status !== "failed";
  renderAsk(incident);
  loadGuidance(incident);
  const pending = incident.pending_approval;
  $("approval").hidden = !pending;
  if (pending) $("a-sha").textContent = pending.patch_sha256;
}

function renderEvidence(items) {
  const list = $("d-evidence");
  list.replaceChildren();
  const filter = state.evidenceFilter;
  const shown = items.filter((item) => filter === "all" || (filter === "observed" ? item.kind === "observed"
    : item.kind !== "observed"));
  for (const item of shown) {
    const li = el("li");
    li.append(el("span", item.kind, `kind kind-${item.kind}`), el("span", item.claim));
    if (item.quote) {
      const quote = el("span", item.quote, "quote");
      li.append(quote);
      if (item.source) li.append(el("span", `from ${humanize(item.source)}`, "file"));
    }
    list.append(li);
  }
  if (!shown.length) list.append(el("li", items.length ? "Nothing in this filter." : "No evidence recorded.", "muted"));
}

function renderChecks(evaluation) {
  const list = $("d-checks");
  list.replaceChildren();
  const checks = Object.entries(evaluation.checks || {}).map(([name, check]) => ({
    name, check, fraction: check.max ? check.score / check.max : 0,
  }));
  checks.sort((a, b) => a.fraction - b.fraction || a.name.localeCompare(b.name)); // failures first
  for (const { name, check, fraction } of checks) {
    const full = fraction >= 1 - 1e-9;
    const tone = full ? "ok" : check.blocking ? "bad" : "warn";
    const li = el("li");
    const label = el("span", null, "check-name");
    label.append(document.createTextNode(humanize(name)));
    if (check.blocking) label.append(document.createTextNode(" "), el("span", "blocking", "chip"));
    const gauge = el("span", null, "check-meter");
    gauge.append(meter(fraction, tone),
      el("span", `${Number(check.score).toFixed(2)} / ${Number(check.max).toFixed(2)}`, "score-num"));
    li.append(label, gauge);
    if (check.detail && check.detail !== "ok") li.append(el("span", check.detail, "check-detail"));
    list.append(li);
  }
  const gate = $("d-gate");
  const known = typeof evaluation.quality_gate_passed === "boolean";
  gate.hidden = !known;
  if (known) {
    const passed = evaluation.quality_gate_passed;
    gate.className = `badge ${passed ? "s-ok" : "s-bad"}`;
    gate.textContent = `${passed ? "passed" : "not passed"} · ${Number(evaluation.quality_score).toFixed(2)}`;
  }
  if (!checks.length) list.append(el("li", "No gate result recorded.", "muted"));
}

function renderAttempts(attempts) {
  const list = $("d-attempts");
  list.replaceChildren();
  for (const a of attempts) {
    const failedCall = Boolean(a.error_category);
    const judged = a.gate_score !== undefined;
    const tone = failedCall ? "bad" : judged ? (a.gate_passed ? "ok" : "warn") : "neutral";
    const li = el("li", null, tone);
    const head = el("div", null, "tl-head");
    head.append(el("span", `Attempt ${a.iteration ?? "?"}`));
    if (failedCall) head.append(el("span", humanize(a.error_category), "chip bad"));
    else if (judged) {
      head.append(el("span", `${Number(a.gate_score).toFixed(2)} ${a.gate_passed ? "pass" : "fail"}`,
        `badge ${a.gate_passed ? "s-ok" : "s-warn"}`));
    }
    if (a.truncated) head.append(el("span", "cut at token limit", "chip warn"));
    li.append(head);

    const bits = [];
    if (a.decision) bits.push(a.decision);
    if (a.latency_ms !== undefined) {
      const tokens = (Number(a.input_tokens) || 0) + (Number(a.output_tokens) || 0);
      bits.push(`${(a.latency_ms / 1000).toFixed(1)} s${tokens ? ` · ${tokens.toLocaleString()} tokens` : ""}`);
    }
    if (bits.length) li.append(el("div", bits.join(" - "), "tl-body"));

    const failed = [...(a.failed_checks || []).map(humanize),
      ...(a.schema_error_fields || []).map((f) => `schema ${f}`)];
    if (failed.length) {
      const chips = el("div", null, "chips");
      for (const name of failed) chips.append(el("span", name, "chip src"));
      li.append(chips);
    }
    list.append(li);
  }
  if (!attempts.length) list.append(el("li", "No attempts recorded.", "muted"));
}

function renderDiff(patch) {
  const view = $("d-patch");
  view.replaceChildren();
  $("copy-patch").hidden = !patch;
  $("download-patch").hidden = !patch;
  if (!patch) {
    view.append(el("div", "No patch proposed.", "empty-note"));
    return;
  }
  let oldLine = 0;
  let newLine = 0;
  for (const text of patch.split("\n")) {
    const hunk = text.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)/);
    let kind = "ctx";
    let left = "";
    let right = "";
    if (hunk) {
      kind = "hunk";
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
    } else if (text.startsWith("---") || text.startsWith("+++") || text.startsWith("\\")) {
      kind = "meta";
    } else if (text.startsWith("+")) {
      kind = "add";
      right = String(newLine++);
    } else if (text.startsWith("-")) {
      kind = "del";
      left = String(oldLine++);
    } else {
      left = String(oldLine++);
      right = String(newLine++);
    }
    const row = el("div", null, `row ${kind}`);
    row.append(el("span", left, "ln"), el("span", right, "ln"), el("span", text, "code"));
    view.append(row);
  }
}

// ------------------------------------------------------------------ guidance (what to do now)

function plainMarkup(text) {
  return String(text || "").replaceAll("**", "").replaceAll("`", "");
}

async function loadGuidance(incident) {
  try {
    const guide = await api(`/incidents/${encodeURIComponent(incident.incident_id)}/guidance`);
    if (!state.current || state.current.incident_id !== incident.incident_id) return;
    renderGuidance(guide);
  } catch {
    $("guide").hidden = true; // guidance is an extra; the page works without it
  }
}

function renderGuidance(guide) {
  const card = $("guide");
  card.hidden = false;
  card.className = `card guide guide-${guide.state}`;
  const tone = { ok: "s-ok", attention: "s-warn", blocked: "s-bad", waiting: "s-neutral" }[guide.state];
  $("guide-state").className = `badge ${tone || "s-neutral"}`;
  $("guide-state").textContent = humanize(guide.state);
  $("guide-headline").textContent = guide.headline;
  $("guide-explanation").textContent = guide.explanation;
  const steps = $("guide-steps");
  steps.replaceChildren();
  for (const step of guide.next_steps || []) {
    const li = el("li");
    li.append(el("span", step.audience, "chip"), el("span", plainMarkup(step.text)));
    steps.append(li);
  }
  if (!steps.children.length) steps.append(el("li", "No action is needed right now.", "muted"));
  const facts = $("guide-facts");
  facts.replaceChildren();
  for (const fact of guide.facts || []) facts.append(el("li", fact));
}

// ------------------------------------------------------------------ ask about this incident

function suggestedQuestions(incident) {
  const analysis = incident.analysis || {};
  const accepted = ["analysis_ready", "awaiting_approval", "pr_created"].includes(incident.status);
  const questions = [accepted ? "Why did the quality gate accept this analysis?"
    : "Why was this analysis not accepted by the gate?"];
  questions.push("What should I do next?");
  if (incident.suggested_patch) questions.push("Explain what the patch changes and what it does not prove.");
  questions.push("How could this diagnosis be wrong?");
  if ((analysis.uncertainties || []).length) questions.push("Which uncertainties matter most?");
  return questions;
}

function renderChips(container, questions) {
  container.replaceChildren();
  for (const question of questions) {
    const button = el("button", question, "chip-btn");
    button.type = "button";
    button.addEventListener("click", () => ask(question));
    container.append(button);
  }
}

function renderAsk(incident) {
  if (state.askFor === incident.incident_id) return; // keep the transcript while the incident is re-rendered
  state.askFor = incident.incident_id;
  state.askHistory = [];
  $("ask-log").replaceChildren();
  $("ask-clear").hidden = true;
  renderChips($("ask-chips"), suggestedQuestions(incident));
}

function setAskBusy(busy) {
  $("ask-send").disabled = busy;
  $("ask-input").disabled = busy;
  document.querySelectorAll("#ask-chips .chip-btn, .ask-followups .chip-btn").forEach((b) => { b.disabled = busy; });
}

function renderAnswer(node, result) {
  node.className = "ask-item ask-a";
  node.textContent = result.answer;
  const meta = el("div", null, "ask-meta");
  meta.append(el("span", result.source === "rules" ? "instant · from the record" : "model", `chip src-${result.source}`));
  if (result.degraded) meta.append(el("span", "model unavailable", "chip warn"));
  if (!result.answerable) meta.append(el("span", "not in the record", "chip warn"));
  else if (result.unverified_quotes && result.unverified_quotes.length) {
    meta.append(el("span", "quoted text not found in the record", "chip bad"));
  } else if (!result.grounded) meta.append(el("span", "cites nothing - treat with caution", "chip bad"));
  for (const name of result.cited_sections || []) meta.append(el("span", name, "chip src"));
  meta.append(el("span", result.source === "model" ? `${result.model} · not executed or verified`
    : "not executed or verified", "muted"));
  node.append(meta);
  for (const quote of result.unverified_quotes || []) {
    node.append(el("div", `Not in the record: ${quote}`, "ask-meta muted"));
  }
  const actions = el("div", null, "ask-actions");
  const copy = el("button", "Copy answer", "btn ghost tiny");
  copy.type = "button";
  copy.addEventListener("click", () => copyText(result.answer, "Answer"));
  actions.append(copy);
  node.append(actions);
  if ((result.follow_ups || []).length) {
    const follow = el("div", null, "ask-followups");
    renderChips(follow, result.follow_ups);
    node.append(follow);
  }
}

async function ask(question) {
  const incident = state.current;
  const text = String(question || "").trim();
  if (!incident || text.length < 3) return;
  const log = $("ask-log");
  log.append(el("li", text, "ask-item ask-q"));
  const answer = el("li", "Reading the incident record…", "ask-item ask-a pending");
  log.append(answer);
  $("ask-clear").hidden = false;
  setAskBusy(true);
  try {
    const result = await api(`/incidents/${encodeURIComponent(incident.incident_id)}/ask`, {
      method: "POST", body: JSON.stringify({ question: text, history: state.askHistory.slice(-6) }),
    });
    renderAnswer(answer, result);
    state.askHistory.push({ role: "user", content: text.slice(0, 1500) },
      { role: "assistant", content: String(result.answer).slice(0, 1500) });
    state.askHistory = state.askHistory.slice(-6);
    $("ask-input").value = "";
  } catch (error) {
    answer.className = "ask-item ask-a error";
    answer.textContent = error.status === 429 ? "Too many questions - wait a moment and try again."
      : error.status === 404 ? "Questions are switched off on this server."
      : error.message;
  } finally {
    setAskBusy(false);
    answer.scrollIntoView({ block: "nearest" });
  }
}

function clearConversation() {
  state.askHistory = [];
  $("ask-log").replaceChildren();
  $("ask-clear").hidden = true;
  $("ask-input").focus();
}

// ------------------------------------------------------------------ actions

async function copyText(text, what) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what} copied`);
  } catch {
    toast("Copy is not available in this browser context", true);
  }
}

function downloadPatch() {
  const incident = state.current;
  if (!incident || !incident.suggested_patch) return;
  const url = URL.createObjectURL(new Blob([incident.suggested_patch], { type: "text/x-diff" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${incident.incident_id.slice(0, 8)}.patch`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function retry() {
  const incident = state.current;
  if (!incident) return;
  $("retry").disabled = true;
  try {
    const updated = await api(`/incidents/${encodeURIComponent(incident.incident_id)}/retry`, { method: "POST" });
    state.current = updated;
    renderDetail(updated);
    toast("Re-queued: a worker will analyse it again");
  } catch (error) {
    setMessage("a-message", error.message, true);
  } finally {
    $("retry").disabled = false;
  }
}

function confirmApproval(incident, pending) {
  const dialog = $("confirm");
  if (typeof dialog.showModal !== "function") {
    return Promise.resolve(window.confirm("Open a draft pull request with this patch?"));
  }
  $("c-repo").textContent = incident.repo_name;
  $("c-file").textContent = pending.target_file || incident.affected_file || "-";
  $("c-sha").textContent = pending.patch_sha256;
  $("c-score").textContent = Number(incident.quality_score).toFixed(2);
  dialog.returnValue = "";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    dialog.showModal();
  });
}

async function decide(decision) {
  const incident = state.current;
  const pending = incident && incident.pending_approval;
  if (!pending) return;
  if (decision === "approve" && !(await confirmApproval(incident, pending))) return;
  for (const id of ["approve", "reject"]) $(id).disabled = true;
  setMessage("a-message", "Submitting…");
  try {
    const updated = await api(`/incidents/${encodeURIComponent(incident.incident_id)}/remediation/decision`, {
      method: "POST",
      body: JSON.stringify({
        approval_id: pending.approval_id,
        patch_sha256: pending.patch_sha256,
        decision,
        note: $("a-note").value || null,
      }),
    });
    state.current = updated;
    renderDetail(updated);
    setMessage("a-message", "");
    toast(`Decision recorded: ${humanize(updated.status)}`);
  } catch (error) {
    setMessage("a-message", error.message, true);
  } finally {
    for (const id of ["approve", "reject"]) $(id).disabled = false;
  }
}

// ------------------------------------------------------------------ wiring

function typing(target) {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}

document.addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey || $("confirm").open) return;
  if (event.key === "Escape" && !$("detail-view").hidden) {
    location.hash = "#/";
    return;
  }
  if (typing(event.target) || !$("detail-view").hidden) return;
  if (event.key === "/") { event.preventDefault(); $("search").focus(); }
  else if (event.key === "r") loadList();
  else if (event.key === "j") moveFocus(1);
  else if (event.key === "k") moveFocus(-1);
});

let refreshTimer = null;
function setAutoRefresh(on) {
  store("local", REFRESH_STORAGE, on ? "1" : null);
  clearInterval(refreshTimer);
  refreshTimer = on ? setInterval(() => {
    if (!document.hidden && !$("list-view").hidden) loadList();
  }, REFRESH_MS) : null;
}

// Deep-link options: /console?theme=dark|light|auto and /console?status=all|needs_review|... (not persisted).
function applyQueryOptions() {
  const params = new URLSearchParams(location.search);
  const theme = params.get("theme");
  if (THEMES.includes(theme)) applyTheme(theme);
  const wanted = params.get("status");
  if (wanted !== null) {
    const value = wanted === "all" ? "" : wanted;
    const select = $("status-filter");
    if ([...select.options].some((option) => option.value === value)) select.value = value;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(store("local", THEME_STORAGE) || "auto");
  applyQueryOptions();
  $("theme").addEventListener("click", cycleTheme);
  $("key-form").addEventListener("submit", (event) => {
    event.preventDefault();
    setKey($("api-key").value.trim());
    $("api-key").value = "";
    route();
  });
  $("forget-key").addEventListener("click", () => {
    setKey("");
    state.items = [];
    setConnection("off", "Not connected");
    route();
  });
  $("status-filter").addEventListener("change", () => { state.items = []; loadList(); });
  $("search").addEventListener("input", renderRows);
  $("refresh").addEventListener("click", () => loadList());
  $("load-more").addEventListener("click", () => loadList(true));
  $("auto-refresh").checked = Boolean(store("local", REFRESH_STORAGE));
  $("auto-refresh").addEventListener("change", (event) => setAutoRefresh(event.target.checked));
  setAutoRefresh($("auto-refresh").checked);
  $("approve").addEventListener("click", () => decide("approve"));
  $("reject").addEventListener("click", () => decide("reject"));
  $("retry").addEventListener("click", retry);
  $("ask-clear").addEventListener("click", clearConversation);
  $("ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    ask($("ask-input").value);
  });
  $("copy-id").addEventListener("click", () => state.current && copyText(state.current.incident_id, "Incident id"));
  $("copy-patch").addEventListener("click", () => state.current && copyText(state.current.suggested_patch || "", "Patch"));
  $("download-patch").addEventListener("click", downloadPatch);
  for (const button of document.querySelectorAll(".seg-btn")) {
    button.addEventListener("click", () => {
      state.evidenceFilter = button.dataset.kind;
      document.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
      if (state.current) renderEvidence((state.current.analysis || {}).evidence || []);
    });
  }
  // Local demo only: the server embeds a key when CONSOLE_DEMO_KEY is set (scripts/demo_console.py).
  const demoKey = document.querySelector('meta[name="opspulse-demo-key"]');
  if (demoKey && !apiKey() && demoKey.content) {
    setKey(demoKey.content);
    setConnection("on", "Demo key loaded");
  }
  $("forget-key").hidden = !apiKey();
  window.addEventListener("hashchange", route);
  route();
});
