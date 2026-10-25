// OpsPulse review console. Plain DOM APIs only: every value from the API (which includes LLM output and
// untrusted log text) is rendered with textContent, never innerHTML. No inline script or style (strict CSP).
"use strict";

const KEY_STORAGE = "opspulse.apiKey";      // sessionStorage: dies with the tab
const THEME_STORAGE = "opspulse.theme";      // localStorage: auto | light | dark
const REFRESH_STORAGE = "opspulse.autoRefresh";
const DIFF_STORAGE = "opspulse.diffMode";   // localStorage: unified | split
const REFRESH_MS = 15000;
const THEMES = ["auto", "light", "dark"];
const THEME_GLYPH = { auto: "◐", light: "☀", dark: "☾" };
const STATUS_TONE = {
  awaiting_approval: "s-warn", needs_review: "s-warn", processing: "s-neutral", analysis_ready: "s-ok",
  pr_created: "s-ok", pr_skipped_duplicate: "s-ok", remediation_rejected: "s-neutral", pr_failed: "s-bad",
  failed: "s-bad", queued: "s-neutral",
};

const U = window.OpsUtil; // pure helpers (util.js), unit-tested with Node
const $ = (id) => document.getElementById(id);
const DEFAULT_STATUS = "awaiting_approval";
const SORT_DEFAULT_DIR = { created: "desc", score: "desc", attempts: "desc", status: "asc", repo: "asc" };
const state = {
  askFor: null, askHistory: [], cursor: null, items: [], current: null, evidenceFilter: "all", loading: false,
  focusIndex: -1, memoryKey: "", chip: "", sort: { ...U.DEFAULT_SORT }, queue: [],
};
// Latest-request-wins: a slow response for an earlier request must never overwrite a newer view.
const seq = { list: 0, detail: 0 };
let listAbort = null;
let detailAbort = null;
const isAbort = (error) => Boolean(error) && error.name === "AbortError";

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

const { humanize, toneForScore, plainMarkup } = U;

function badge(status) {
  return el("span", humanize(status), `badge ${STATUS_TONE[status] || "s-neutral"}`);
}

function meter(fraction, tone) {
  const bar = el("span", null, `bar ${tone}`);
  const fill = document.createElement("span");
  fill.style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`; // CSSOM: allowed by the CSP
  bar.append(fill);
  return bar;
}

function ago(iso) { return U.ago(iso); }

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
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { "X-API-Key": apiKey(), "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch (cause) {
    if (isAbort(cause)) throw cause;
    const error = new Error("Cannot reach the server. Check the connection; this page retries when you are back online.");
    error.network = true;
    throw error;
  }
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
  if (error.network) {
    setConnection("bad", "Offline");
    setMessage(messageId, error.message, true);
  } else if (error.status === 401) {
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
  const filtered = U.filterItems(state.items, { query: $("search").value, chip: state.chip });
  return U.sortItems(filtered, state.sort);
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
  if (listAbort) listAbort.abort(); // a newer request supersedes an older one instead of being dropped
  listAbort = new AbortController();
  const mine = ++seq.list;
  state.loading = true;
  $("list-retry").hidden = true;
  const params = new URLSearchParams({ limit: "20" });
  const status = $("status-filter").value;
  if (status) params.set("status", status);
  if (append && state.cursor) params.set("cursor", state.cursor);
  if (!append && state.items.length === 0) skeletonRows();
  setMessage("list-message", "");
  try {
    const page = await api(`/incidents?${params}`, { signal: listAbort.signal });
    if (mine !== seq.list) return;
    state.items = append ? state.items.concat(page.items) : page.items;
    state.cursor = page.next_cursor;
    $("load-more").hidden = !page.next_cursor;
    $("net-banner").hidden = true;
    setConnection("on", "Connected");
    renderRows();
  } catch (error) {
    if (isAbort(error) || mine !== seq.list) return;
    state.items = [];
    $("list-card").hidden = true;
    $("empty").hidden = true;
    $("status-chips").hidden = true;
    $("load-more").hidden = true;
    $("list-summary").textContent = "";
    failure(error, "list-message");
    $("list-retry").hidden = error.status === 401;
  } finally {
    if (mine === seq.list) state.loading = false;
  }
}

function renderStatusChips() {
  const box = $("status-chips");
  const { counts, order } = U.statusCounts(state.items);
  if (state.chip && !counts[state.chip]) state.chip = "";
  box.replaceChildren();
  box.hidden = order.length < 2; // one status only: a filter would be noise
  for (const status of order) {
    const button = el("button", humanize(status), "chip-btn");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.chip === status));
    button.append(el("span", counts[status], "count"));
    button.addEventListener("click", () => {
      state.chip = state.chip === status ? "" : status;
      renderRows();
    });
    box.append(button);
  }
}

function applySort(sort) {
  state.sort = sort;
  syncUrl();
  renderRows();
}

function updateSortHeaders() {
  const select = $("sort-select");
  const wanted = `${state.sort.key}:${state.sort.dir}`;
  select.value = [...select.options].some((option) => option.value === wanted) ? wanted : "";
  document.querySelectorAll("th .th-sort").forEach((button) => {
    const active = button.dataset.sort === state.sort.key;
    button.parentElement.setAttribute("aria-sort", active ? (state.sort.dir === "asc" ? "ascending" : "descending") : "none");
  });
}

function syncUrl() {
  try {
    const status = $("status-filter").value;
    const query = U.writeState({
      status: status === DEFAULT_STATUS ? null : status,
      query: $("search").value.trim(),
      sort: state.sort,
      theme: U.readState(location.search).theme,
    });
    history.replaceState(null, "", `${location.pathname}${query}${location.hash}`);
  } catch { /* history unavailable (sandboxed frame): the view still works, it just is not shareable */ }
}

function plural(count, one, many) { return `${count} ${count === 1 ? one : many}`; }

function startQueue(ids) {
  if (!ids.length) return;
  state.queue = ids; // ] and [ now walk the lane from the oldest
  location.hash = `#/incident/${encodeURIComponent(ids[0])}`;
}

function renderQueueCard() {
  const summary = U.queueSummary(state.items, Date.now());
  const { awaiting, review } = summary;
  const card = $("queue-card");
  card.hidden = awaiting.count + review.count === 0;
  if (card.hidden) return;
  const text = $("qc-text");
  text.replaceChildren();
  if (awaiting.count) {
    text.append(el("strong", plural(awaiting.count, "proposal waits", "proposals wait")), document.createTextNode(" for a decision"));
    if (awaiting.oldestAgeMs !== null) {
      text.append(document.createTextNode(`; the oldest has waited ${U.duration(awaiting.oldestAgeMs)}`));
    }
    if (awaiting.stale) {
      text.append(document.createTextNode(" ("), el("span", `${awaiting.stale} over a day`, "qc-warn"), document.createTextNode(")"));
    }
    text.append(document.createTextNode(". "));
  } else {
    text.append(document.createTextNode("Nothing is waiting for a decision. "));
  }
  if (review.count) {
    text.append(el("strong", plural(review.count, "analysis needs", "analyses need")), document.createTextNode(" a human look."));
  }
  $("qc-scope").textContent = state.cursor ? "among the incidents loaded so far" : "";
  for (const [id, lane, label] of [
    ["qc-start-awaiting", awaiting, "Start with the oldest proposal"],
    ["qc-start-review", review, "Review the oldest analysis"],
  ]) {
    const button = $(id);
    button.hidden = lane.count === 0;
    button.textContent = label;
    button.onclick = () => startQueue(lane.ids);
  }
}

function renderRows() {
  const body = $("incident-table").querySelector("tbody");
  body.replaceChildren();
  renderQueueCard();
  renderStatusChips();
  updateSortHeaders();
  const items = visibleItems();
  state.queue = items.map((item) => item.incident_id); // the order the reviewer is working through
  state.focusIndex = -1;
  for (const item of items) {
    const row = document.createElement("tr");
    row.tabIndex = 0;
    row.dataset.id = item.incident_id;
    const target = `#/incident/${encodeURIComponent(item.incident_id)}`;

    const statusCell = document.createElement("td");
    statusCell.append(badge(item.status));
    if (item.error_category) statusCell.append(el("span", humanize(item.error_category), "file"));

    const repoCell = document.createElement("td");
    const link = el("a", item.repo_name, "repo repo-link");
    link.href = target;
    repoCell.append(link, el("span", item.affected_file || "no source file", "file mono"));

    const scoreCell = document.createElement("td");
    const score = Number(item.quality_score) || 0;
    const box = el("div", null, "score-cell");
    box.title = "Deterministic rubric score from the quality gate, not a probability of being correct";
    box.append(el("span", score.toFixed(2), "score-num"), meter(score, toneForScore(score)));
    scoreCell.append(box);

    const when = el("td", ago(item.created_at));
    when.dataset.created = item.created_at || "";
    when.title = fullDate(item.created_at);
    if (item.submitted_by) when.append(el("span", item.submitted_by, "file"));

    row.append(statusCell, repoCell, scoreCell, el("td", item.iterations, "num"), when);
    row.addEventListener("click", (event) => {
      if (event.target.closest("a")) return; // the link navigates by itself (middle-click, copy link, ...)
      location.hash = target;
    });
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target === row) location.hash = target;
    });
    body.append(row);
  }

  const total = state.items.length;
  const awaiting = state.items.filter((i) => i.status === "awaiting_approval").length;
  $("list-summary").textContent = total
    ? `${total} loaded${items.length !== total ? ` (${items.length} shown)` : ""} · ${awaiting} awaiting approval`
    : "";
  $("list-card").hidden = items.length === 0;
  $("empty").hidden = items.length !== 0;
  if (items.length === 0) {
    const filtering = total > 0;
    $("empty-title").textContent = filtering ? "No loaded incident matches" : "No incidents match this filter";
    $("empty-text").textContent = filtering
      ? "Clear the search box or the status chip, or load older incidents."
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

function renderQueue() {
  const nav = $("queue");
  const here = state.current ? state.current.incident_id : (location.hash.match(/^#\/incident\/([0-9A-Fa-f-]{8,64})$/) || [])[1];
  const place = U.queuePosition(state.queue, here);
  nav.hidden = place.position === 0 || place.total < 2;
  if (nav.hidden) return;
  $("queue-pos").textContent = `${place.position} of ${place.total}`;
  for (const [id, target] of [["queue-prev", place.prev], ["queue-next", place.next]]) {
    const link = $(id);
    link.href = target ? `#/incident/${encodeURIComponent(target)}` : "#/";
    if (target) link.removeAttribute("aria-disabled"); else link.setAttribute("aria-disabled", "true");
    link.tabIndex = target ? 0 : -1;
  }
}

function stepQueue(delta) {
  const here = state.current && state.current.incident_id;
  const place = U.queuePosition(state.queue, here);
  const target = delta < 0 ? place.prev : place.next;
  if (target) location.hash = `#/incident/${encodeURIComponent(target)}`;
}

function resetDetail() {
  state.current = null;
  $("d-title").textContent = "Loading incident…";
  document.title = "Loading incident - OpsPulse";
  for (const id of ["guide", "tests-card", "trail", "audit", "approval", "retry", "d-pr", "d-severity", "d-reason"]) {
    $(id).hidden = true;
  }
  for (const id of ["d-evidence", "d-checks", "d-attempts", "d-patch", "d-uncertainties", "d-tests"]) {
    $(id).replaceChildren();
  }
}

async function showDetail(incidentId) {
  if (detailAbort) detailAbort.abort();
  detailAbort = new AbortController();
  const mine = ++seq.detail;
  $("list-view").hidden = true;
  $("detail-view").hidden = false;
  resetDetail();
  setMessage("a-message", "");
  try {
    const incident = await api(`/incidents/${encodeURIComponent(incidentId)}`, { signal: detailAbort.signal });
    if (mine !== seq.detail) return;
    state.current = incident;
    $("net-banner").hidden = true;
    setConnection("on", "Connected");
    renderDetail(incident);
    renderQueue();
    if (!state.items.length) loadList().then(renderQueue, () => {}); // opened by link: fetch the list quietly for the position
    window.scrollTo(0, 0);
    $("d-title").focus({ preventScroll: true });
  } catch (error) {
    if (isAbort(error) || mine !== seq.detail) return;
    $("d-title").textContent = "Incident could not be loaded";
    failure(error, "a-message");
  }
}

function showList(fromDetail = false) {
  seq.detail += 1; // any detail response still in flight is now stale
  if (detailAbort) detailAbort.abort();
  document.title = "OpsPulse Console";
  $("detail-view").hidden = true;
  $("list-view").hidden = false;
  state.current = null;
  loadList();
  if (fromDetail) $("list-title").focus({ preventScroll: true });
}

function route() {
  const match = location.hash.match(/^#\/incident\/([0-9A-Fa-f-]{8,64})$/);
  if (match) showDetail(match[1]); else showList(!$("detail-view").hidden);
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
  renderDiff(incident.suggested_patch);
  $("d-error").textContent = incident.error_message || "";

  $("retry").hidden = incident.status !== "failed";
  renderAsk(incident);
  renderTests(analysis);
  loadGuidance(incident);
  loadTrail(incident);
  loadAudit(incident);
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

function diffCode(row) {
  const code = el("span", null, "code");
  if (!row.segments) { code.textContent = row.text; return code; }
  code.append(document.createTextNode(row.text.slice(0, 1))); // the "+" / "-" marker
  for (const part of row.segments) {
    if (part.changed) code.append(el("span", part.text, "chg")); else code.append(document.createTextNode(part.text));
  }
  return code;
}

function diffPane(row, side) {
  const pane = el("div", null, `pane ${row ? row.kind : "gap"}`);
  if (!row) return pane;
  pane.append(el("span", side === "left" ? row.left : row.right, "ln"), diffCode(row));
  return pane;
}

function renderDiff(patch) {
  const view = $("d-patch");
  view.replaceChildren();
  const split = store("local", DIFF_STORAGE) === "split";
  view.classList.toggle("split", split);
  $("copy-patch").hidden = !patch;
  $("download-patch").hidden = !patch;
  $("diff-mode").hidden = !patch;
  $("diff-mode").setAttribute("aria-pressed", String(split));
  $("diff-mode").textContent = split ? "Unified" : "Side by side";
  if (!patch) {
    view.append(el("div", "No patch proposed.", "empty-note"));
    return;
  }
  const rows = U.annotateDiff(U.parseDiff(patch));
  if (split) {
    for (const pair of U.splitDiff(rows)) {
      if (pair.full) {
        const full = el("div", null, `row full ${pair.full.kind}`);
        full.append(el("span", pair.full.text, "code"));
        view.append(full);
      } else {
        const row = el("div", null, "row");
        row.append(diffPane(pair.left, "left"), diffPane(pair.right, "right"));
        view.append(row);
      }
    }
    return;
  }
  for (const line of rows) {
    const row = el("div", null, `row ${line.kind}`);
    row.append(el("span", line.left, "ln"), el("span", line.right, "ln"), diffCode(line));
    view.append(row);
  }
}

// ------------------------------------------------------------------ guidance (what to do now)

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

// ------------------------------------------------------------------ test evidence (sandbox + reproduction)

const SANDBOX_TONE = {
  fixes_failing_tests: "s-ok", no_regression: "s-ok", regression: "s-bad", still_failing: "s-warn",
  reproduced_and_fixed: "s-ok", reproduced_not_fixed: "s-bad", not_reproduced: "s-warn", fails_for_another_reason: "s-warn",
};

function renderTests(analysis) {
  const sandbox = analysis && analysis.sandbox;
  const test = analysis && analysis.reproduction_test;
  const card = $("tests-card");
  card.hidden = !(sandbox || test);
  if (card.hidden) return;
  const list = $("tests-list");
  list.replaceChildren();
  let headline = null;
  const reproduction = sandbox && sandbox.reproduction;
  if (reproduction) {
    headline = reproduction;
    const item = el("li");
    item.append(el("span", humanize(reproduction.status), `chip ${SANDBOX_TONE[reproduction.status] || "s-neutral"}`));
    item.append(el("span", ` Reproduction test: ${reproduction.summary}`));
    list.append(item);
  } else if (test) {
    list.append(el("li", "A reproduction test was proposed but not run (the sandbox is off or could not run it)."));
  }
  if (sandbox) {
    headline = headline || sandbox;
    const item = el("li");
    item.append(el("span", humanize(sandbox.status), `chip ${SANDBOX_TONE[sandbox.status] || "s-neutral"}`));
    item.append(el("span", ` The repository's own tests: ${sandbox.summary}`));
    list.append(item);
  }
  const badge = $("tests-badge");
  badge.textContent = headline ? humanize(headline.status) : "not run";
  badge.className = `badge ${(headline && SANDBOX_TONE[headline.status]) || "s-neutral"}`;
  const details = $("repro-details");
  details.hidden = !(test && test.content);
  if (!details.hidden) {
    $("repro-path").textContent = String(test.path || "");
    $("repro-code").textContent = String(test.content || "");
  }
}

// ------------------------------------------------------------------ evidence trail

async function loadTrail(incident) {
  try {
    const graph = await api(`/incidents/${encodeURIComponent(incident.incident_id)}/evidence-graph`);
    if (!state.current || state.current.incident_id !== incident.incident_id) return;
    renderTrail(graph);
  } catch {
    $("trail").hidden = true; // an extra view; the page works without it
  }
}

function renderTrail(graph) {
  $("trail").hidden = false;
  const summary = graph.summary || {};
  $("trail-summary").textContent = `${summary.observed_verified || 0}/${summary.observed || 0} observed verified`;
  const flags = $("trail-flags");
  flags.replaceChildren();
  for (const flag of graph.flags || []) flags.append(el("li", flag, "trail-flag"));
  const anchors = new Set((graph.edges || []).filter((e) => e.relation === "anchors").map((e) => e.from));
  const items = $("trail-items");
  items.replaceChildren();
  const tone = { verified_in_record: "s-ok", verified_by_gate: "s-ok", unverified: "s-bad", not_checked: "s-neutral" };
  for (const node of (graph.nodes || []).filter((n) => n.type === "evidence")) {
    const li = el("li");
    li.append(el("span", humanize(node.status), `chip ${tone[node.status] || "s-neutral"}`));
    const body = el("span");
    body.append(el("span", `${node.kind} \u00b7 ${node.source}: ${node.claim}`));
    if (node.quote) body.append(el("code", node.quote, "trail-quote"));
    if (anchors.has(node.id)) body.append(el("span", "touches the patched code", "muted small"));
    li.append(body);
    items.append(li);
  }
  if (!items.children.length) items.append(el("li", "No evidence recorded.", "muted"));
}

// ------------------------------------------------------------------ review history

const AUDIT_TONE = { approved: "ok", pr_opened: "ok", rejected: "bad", pr_failed: "bad", expired: "warn" };

async function loadAudit(incident) {
  try {
    const audit = await api(`/incidents/${encodeURIComponent(incident.incident_id)}/audit`);
    if (!state.current || state.current.incident_id !== incident.incident_id) return;
    renderAudit(audit);
  } catch {
    $("audit").hidden = true; // an extra view; the page works without it
  }
}

function renderAudit(audit) {
  const list = $("audit-list");
  list.replaceChildren();
  for (const event of audit.events || []) {
    const li = el("li", null, AUDIT_TONE[event.event] || "");
    const head = el("div", null, "tl-head");
    head.append(el("span", humanize(event.event)));
    if (event.actor) head.append(el("span", event.actor, "chip"));
    li.append(head);
    const when = event.at ? new Date(event.at).toLocaleString() : "";
    li.append(el("div", [when, event.detail].filter(Boolean).join(" \u00b7 "), "tl-body"));
    list.append(li);
  }
  $("audit").hidden = !list.children.length;
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
    if (!state.current || state.current.incident_id !== incident.incident_id) return; // moved on meanwhile
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
    if (!state.current || state.current.incident_id !== incident.incident_id) return;
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
    if (state.current && state.current.incident_id === incident.incident_id) {
      state.current = updated;
      renderDetail(updated);
    }
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
  if (!typing(event.target) && !$("detail-view").hidden && (event.key === "[" || event.key === "]")) {
    stepQueue(event.key === "[" ? -1 : 1);
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

// Deep-link options, all validated by U.readState: ?theme=dark|light|auto, ?status=all|needs_review|...,
// ?q=<search>, ?sort=score:asc. The view keeps them in the URL (replaceState), so a reload or a copied link restores it.
function applyQueryOptions() {
  const view = U.readState(location.search);
  if (view.theme) applyTheme(view.theme);
  if (view.status !== null) {
    const select = $("status-filter");
    if ([...select.options].some((option) => option.value === view.status)) select.value = view.status;
  }
  $("search").value = view.query;
  state.sort = view.sort;
  const diffMode = new URLSearchParams(location.search).get("diff"); // ?diff=split|unified: a shareable link opens in that view
  if (diffMode === "split" || diffMode === "unified") store("local", DIFF_STORAGE, diffMode);
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
  $("status-filter").addEventListener("change", () => { state.items = []; state.chip = ""; syncUrl(); loadList(); });
  $("search").addEventListener("input", () => { syncUrl(); renderRows(); });
  $("list-retry").addEventListener("click", () => loadList());
  document.querySelectorAll(".th-sort").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      applySort(state.sort.key === key
        ? { key, dir: state.sort.dir === "asc" ? "desc" : "asc" }
        : { key, dir: SORT_DEFAULT_DIR[key] || "desc" });
    });
  });
  $("sort-select").addEventListener("change", (event) => {
    const [key, dir] = event.target.value.split(":");
    if (key) applySort({ key, dir: dir === "asc" ? "asc" : "desc" });
  });
  // Relative times ("5 min ago") would otherwise go stale on a page that stays open.
  setInterval(() => {
    document.querySelectorAll("td[data-created]").forEach((cell) => {
      if (cell.firstChild) cell.firstChild.nodeValue = ago(cell.dataset.created);
    });
  }, 30000);
  window.addEventListener("offline", () => {
    $("net-banner").hidden = false;
    setConnection("bad", "Offline");
  });
  window.addEventListener("online", () => {
    $("net-banner").hidden = true;
    route();
  });
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
  $("diff-mode").addEventListener("click", () => {
    store("local", DIFF_STORAGE, store("local", DIFF_STORAGE) === "split" ? "unified" : "split");
    if (state.current) renderDiff(state.current.suggested_patch);
  });
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
