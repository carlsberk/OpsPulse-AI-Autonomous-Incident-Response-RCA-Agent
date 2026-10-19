// Pure helpers of the review console: no DOM, no network, no globals, so they can be unit-tested with plain Node
// (tests/js/util.test.js). Loaded before app.js as a classic script (strict CSP: no inline code, no modules needed).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.OpsUtil = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SORT_KEYS = ["created", "score", "attempts", "status", "repo"];
  const DEFAULT_SORT = { key: "created", dir: "desc" };
  const STATUS_OPTIONS = [
    "awaiting_approval", "needs_review", "analysis_ready", "pr_created", "remediation_rejected", "failed", "queued",
    "processing",
  ];

  function humanize(value) {
    return String(value === null || value === undefined ? "" : value).replaceAll("_", " ");
  }

  /** "3 min ago" style label. `now` is injectable for tests. */
  function ago(iso, now) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const seconds = ((now === undefined ? Date.now() : now) - date.getTime()) / 1000;
    if (seconds < 0) return "just now";
    if (seconds < 45) return "just now";
    if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} d ago`;
    return date.toISOString().slice(0, 10);
  }

  function toneForScore(score) {
    if (score >= 0.85) return "ok";
    if (score >= 0.5) return "warn";
    return "bad";
  }

  /** Server text may carry **bold** / `code` markers meant for Markdown renderers; the console shows plain text. */
  function plainMarkup(text) {
    return String(text === null || text === undefined ? "" : text).replaceAll("**", "").replaceAll("`", "");
  }

  function searchText(item) {
    return [item.repo_name, item.affected_file, item.submitted_by, item.status, item.incident_id, item.error_category]
      .map((value) => String(value || "").toLowerCase())
      .join("\n");
  }

  /** Client-side filter over the loaded page(s): free-text query and an optional status chip. */
  function filterItems(items, options) {
    const query = String((options && options.query) || "").trim().toLowerCase();
    const chip = (options && options.chip) || "";
    return items.filter((item) => (!chip || item.status === chip) && (!query || searchText(item).includes(query)));
  }

  function compareValue(item, key) {
    switch (key) {
      case "score": return Number(item.quality_score) || 0;
      case "attempts": return Number(item.iterations) || 0;
      case "status": return String(item.status || "");
      case "repo": return String(item.repo_name || "").toLowerCase();
      default: return Date.parse(item.created_at) || 0;
    }
  }

  /** Stable sort that returns a new array; unknown keys fall back to the default (newest first). */
  function sortItems(items, sort) {
    const key = SORT_KEYS.includes(sort && sort.key) ? sort.key : DEFAULT_SORT.key;
    const sign = (sort && sort.dir) === "asc" ? 1 : -1;
    return items
      .map((item, index) => ({ item, index, value: compareValue(item, key) }))
      .sort((a, b) => (a.value < b.value ? -sign : a.value > b.value ? sign : a.index - b.index))
      .map((entry) => entry.item);
  }

  /** Status -> count, plus the statuses in a stable display order. */
  function statusCounts(items) {
    // No prototype: a status called "constructor", "toString" or "__proto__" must count like any other string.
    const counts = Object.create(null);
    for (const item of items) counts[item.status] = (counts[item.status] || 0) + 1;
    const known = STATUS_OPTIONS.filter((status) => counts[status]);
    const other = Object.keys(counts).filter((status) => !STATUS_OPTIONS.includes(status)).sort();
    return { counts, order: [...known, ...other] };
  }

  /** A hunk-header line number as a safe integer (an absurd value would otherwise render as "1e+21"). */
  function safeLine(text) {
    const value = Number(text);
    return Number.isSafeInteger(value) ? value : 0;
  }

  /**
   * Unified diff -> display rows with real old/new line numbers taken from the hunk headers.
   * Every row: { kind: hunk|meta|add|del|ctx, left, right, text }.
   */
  function parseDiff(patch) {
    const rows = [];
    let oldLine = 0;
    let newLine = 0;
    for (const text of String(patch || "").split("\n")) {
      const hunk = text.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)/);
      if (hunk) {
        oldLine = safeLine(hunk[1]);
        newLine = safeLine(hunk[2]);
        rows.push({ kind: "hunk", left: "", right: "", text });
      } else if (text.startsWith("---") || text.startsWith("+++") || text.startsWith("\\")) {
        rows.push({ kind: "meta", left: "", right: "", text });
      } else if (text.startsWith("+")) {
        rows.push({ kind: "add", left: "", right: String(newLine++), text });
      } else if (text.startsWith("-")) {
        rows.push({ kind: "del", left: String(oldLine++), right: "", text });
      } else {
        rows.push({ kind: "ctx", left: String(oldLine++), right: String(newLine++), text });
      }
    }
    return rows;
  }

  /** Query string -> view state. Everything is validated: a hand-edited URL can never put the page in a bad state. */
  function readState(search) {
    const params = new URLSearchParams(search || "");
    const [key, dir] = String(params.get("sort") || "").split(":");
    const status = params.get("status");
    return {
      status: status === null ? null : status === "all" ? "" : STATUS_OPTIONS.includes(status) ? status : null,
      query: String(params.get("q") || "").slice(0, 100),
      sort: SORT_KEYS.includes(key) ? { key, dir: dir === "asc" ? "asc" : "desc" } : { ...DEFAULT_SORT },
      theme: ["light", "dark", "auto"].includes(params.get("theme")) ? params.get("theme") : null,
    };
  }

  /** View state -> query string (defaults omitted). `theme` is passed through untouched when present. */
  function writeState(view) {
    const params = new URLSearchParams();
    if (view.theme) params.set("theme", view.theme);
    if (view.status !== null && view.status !== undefined) params.set("status", view.status === "" ? "all" : view.status);
    if (view.query) params.set("q", view.query);
    const sort = view.sort || DEFAULT_SORT;
    if (sort.key !== DEFAULT_SORT.key || sort.dir !== DEFAULT_SORT.dir) params.set("sort", `${sort.key}:${sort.dir}`);
    const text = params.toString();
    return text ? `?${text}` : "";
  }

  return {
    SORT_KEYS, DEFAULT_SORT, STATUS_OPTIONS,
    humanize, ago, toneForScore, plainMarkup, filterItems, sortItems, statusCounts, parseDiff, readState, writeState,
  };
});
