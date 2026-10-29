// Unit tests for the console's pure helpers. Run with: node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const U = require("../../src/console/static/util.js");

const NOW = Date.parse("2026-10-13T12:00:00Z");
const items = [
  { incident_id: "a1", repo_name: "Eval/Shop", affected_file: "app/cart.py", submitted_by: "alice", status: "needs_review", quality_score: 0.4, iterations: 3, created_at: "2026-10-13T10:00:00Z" },
  { incident_id: "b2", repo_name: "eval/api", affected_file: null, submitted_by: "sentry", status: "awaiting_approval", quality_score: 0.99, iterations: 1, created_at: "2026-10-13T11:30:00Z" },
  { incident_id: "c3", repo_name: "eval/shop", affected_file: "app/pool.py", submitted_by: null, status: "needs_review", quality_score: 0.7, iterations: 2, created_at: "2026-10-12T09:00:00Z" },
];

test("ago: buckets, future timestamps and garbage", () => {
  assert.equal(U.ago("2026-10-13T11:59:30Z", NOW), "just now");
  assert.equal(U.ago("2026-10-13T11:55:00Z", NOW), "5 min ago");
  assert.equal(U.ago("2026-10-13T09:00:00Z", NOW), "3 h ago");
  assert.equal(U.ago("2026-10-10T12:00:00Z", NOW), "3 d ago");
  assert.equal(U.ago("2026-09-01T00:00:00Z", NOW), "2026-09-01");
  assert.equal(U.ago("2026-10-13T12:05:00Z", NOW), "just now"); // clock skew: never a negative duration
  assert.equal(U.ago("", NOW), "");
  assert.equal(U.ago("not a date", NOW), "not a date");
});

test("humanize and plainMarkup tolerate null and strip markdown markers", () => {
  assert.equal(U.humanize("awaiting_approval"), "awaiting approval");
  assert.equal(U.humanize(null), "");
  assert.equal(U.plainMarkup("**diff_applies** - use `git apply`"), "diff_applies - use git apply");
  assert.equal(U.plainMarkup(undefined), "");
});

test("toneForScore thresholds", () => {
  assert.deepEqual([0.99, 0.85, 0.84, 0.5, 0.49, 0].map(U.toneForScore), ["ok", "ok", "warn", "warn", "bad", "bad"]);
});

test("filterItems: query is case-insensitive across fields and combines with the status chip", () => {
  assert.deepEqual(U.filterItems(items, { query: "SHOP" }).map((i) => i.incident_id), ["a1", "c3"]);
  assert.deepEqual(U.filterItems(items, { query: "sentry" }).map((i) => i.incident_id), ["b2"]);
  assert.deepEqual(U.filterItems(items, { chip: "needs_review", query: "pool" }).map((i) => i.incident_id), ["c3"]);
  assert.equal(U.filterItems(items, {}).length, 3);
  assert.equal(U.filterItems(items, { query: "   " }).length, 3);
  assert.equal(U.filterItems(items, { query: "no such thing" }).length, 0);
});

test("sortItems: keys, direction, stability, no mutation, unknown key", () => {
  const ids = (sorted) => sorted.map((i) => i.incident_id);
  const before = ids(items);
  assert.deepEqual(ids(U.sortItems(items, { key: "created", dir: "desc" })), ["b2", "a1", "c3"]);
  assert.deepEqual(ids(U.sortItems(items, { key: "created", dir: "asc" })), ["c3", "a1", "b2"]);
  assert.deepEqual(ids(U.sortItems(items, { key: "score", dir: "desc" })), ["b2", "c3", "a1"]);
  assert.deepEqual(ids(U.sortItems(items, { key: "attempts", dir: "asc" })), ["b2", "c3", "a1"]);
  assert.deepEqual(ids(U.sortItems(items, { key: "repo", dir: "asc" })), ["b2", "a1", "c3"]); // ties keep input order
  assert.deepEqual(ids(U.sortItems(items, { key: "bogus", dir: "asc" })), ["c3", "a1", "b2"]); // unknown key: created
  assert.deepEqual(ids(items), before, "the input array must not be mutated");
  const ties = [{ incident_id: "x", quality_score: 1 }, { incident_id: "y", quality_score: 1 }, { incident_id: "z", quality_score: 1 }];
  assert.deepEqual(ids(U.sortItems(ties, { key: "score", dir: "desc" })), ["x", "y", "z"]);
});

test("statusCounts: counts and a stable display order with unknown statuses last", () => {
  const out = U.statusCounts([...items, { status: "weird" }, { status: "failed" }]);
  assert.equal(out.counts.needs_review, 2);
  assert.deepEqual(out.order, ["awaiting_approval", "needs_review", "failed", "weird"]);
});

test("parseDiff numbers lines from the hunk headers", () => {
  const patch = ["--- a/f.py", "+++ b/f.py", "@@ -10,3 +10,4 @@", " keep", "-old", "+new1", "+new2", " tail", "\\ No newline"].join("\n");
  const rows = U.parseDiff(patch);
  assert.deepEqual(rows.map((r) => r.kind), ["meta", "meta", "hunk", "ctx", "del", "add", "add", "ctx", "meta"]);
  assert.deepEqual(rows[3], { kind: "ctx", left: "10", right: "10", text: " keep" });
  assert.deepEqual(rows[4], { kind: "del", left: "11", right: "", text: "-old" });
  assert.deepEqual([rows[5].right, rows[6].right], ["11", "12"]);
  assert.deepEqual([rows[7].left, rows[7].right], ["12", "13"]);
  assert.deepEqual(U.parseDiff(""), [{ kind: "ctx", left: "0", right: "0", text: "" }]);
});

test("readState validates everything it reads from the URL", () => {
  const view = U.readState("?status=needs_review&q=pool&sort=score:asc&theme=dark");
  assert.deepEqual(view, { status: "needs_review", query: "pool", sort: { key: "score", dir: "asc" }, theme: "dark" });
  assert.equal(U.readState("?status=all").status, "");
  assert.equal(U.readState("?status=<script>").status, null);
  assert.deepEqual(U.readState("?sort=bogus:up").sort, U.DEFAULT_SORT);
  assert.equal(U.readState("?theme=neon").theme, null);
  assert.equal(U.readState("?q=" + "x".repeat(500)).query.length, 100);
  assert.deepEqual(U.readState(""), { status: null, query: "", sort: U.DEFAULT_SORT, theme: null });
});

test("writeState omits defaults and round-trips through readState", () => {
  assert.equal(U.writeState({ status: null, query: "", sort: U.DEFAULT_SORT, theme: null }), "");
  const view = { status: "", query: "a b&c", sort: { key: "attempts", dir: "desc" }, theme: "light" };
  assert.deepEqual(U.readState(U.writeState(view)), view);
});

test("statusCounts: status names that collide with Object.prototype members count like any other", () => {
  const rows = ["constructor", "toString", "__proto__", "constructor"].map((status) => ({ status }));
  const { counts, order } = U.statusCounts(rows);
  assert.equal(counts.constructor, 2);
  assert.equal(counts.toString, 1);
  assert.deepEqual([...order].sort(), ["__proto__", "constructor", "toString"].sort());
  assert.equal(typeof {}.constructor, "function"); // the global Object.prototype is untouched
});

test("parseDiff: an absurd hunk number is shown as a plain integer, not as 1e+21", () => {
  const rows = U.parseDiff("@@ -999999999999999999999,2 +5 @@\n a");
  assert.equal(rows[1].left, "0");
  assert.equal(rows[1].right, "5");
});

test("queuePosition: position, neighbours and the edges of the list", () => {
  const ids = ["a1", "b2", "c3"];
  assert.deepEqual(U.queuePosition(ids, "b2"), { position: 2, total: 3, prev: "a1", next: "c3" });
  assert.deepEqual(U.queuePosition(ids, "a1"), { position: 1, total: 3, prev: null, next: "b2" });
  assert.deepEqual(U.queuePosition(ids, "c3"), { position: 3, total: 3, prev: "b2", next: null });
});

test("queuePosition: an id outside the list, an empty list and garbage input hide the navigation", () => {
  assert.equal(U.queuePosition(["a1"], "zz").position, 0);
  assert.equal(U.queuePosition([], "a1").total, 0);
  assert.deepEqual(U.queuePosition(null, "a1"), { position: 0, total: 0, prev: null, next: null });
  assert.deepEqual(U.queuePosition("a1,b2", "a1"), { position: 0, total: 0, prev: null, next: null });
  assert.equal(U.queuePosition([1, null, "a1"], "a1").total, 1); // non-strings are dropped, never thrown on
});

// ------------------------------------------------------------------ inline diff and side-by-side layout

const join = (parts) => parts.map((part) => part.text).join("");
const changedText = (parts) => parts.filter((part) => part.changed).map((part) => part.text).join("|");

test("inlineDiff: marks only what changed on a one-argument edit", () => {
  const pair = U.inlineDiff('    return STOCK.get(sku)', '    return STOCK.get(sku, 0)');
  assert.equal(join(pair.left), '    return STOCK.get(sku)');
  assert.equal(join(pair.right), '    return STOCK.get(sku, 0)');
  assert.equal(changedText(pair.left), "");
  assert.equal(changedText(pair.right), ", 0");
});

test("inlineDiff: a changed word on both sides", () => {
  const pair = U.inlineDiff("if user is None:", "if account is None:");
  assert.equal(changedText(pair.left), "user");
  assert.equal(changedText(pair.right), "account");
});

test("inlineDiff: unrelated lines, identical lines and over-long lines are not highlighted", () => {
  assert.equal(U.inlineDiff("alpha beta gamma", "x = 1"), null);
  assert.equal(U.inlineDiff("same line", "same line"), null);
  assert.equal(U.inlineDiff("", "x"), null);
  assert.equal(U.inlineDiff("a ".repeat(300), "b ".repeat(300)), null);
});

test("inlineDiff: the pieces always concatenate back to the exact input, whatever it contains", () => {
  const samples = ["", " ", "\t\t", "é ü 漢字", "a<b>&amp;", "x = {'a': [1, 2]}", "😀 emoji", "a\u0000b", "    "];
  for (const a of samples) {
    for (const b of samples) {
      const pair = U.inlineDiff(a, b);
      if (pair) {
        assert.equal(join(pair.left), a);
        assert.equal(join(pair.right), b);
      }
    }
  }
});

const PATCH = [
  "--- a/app.py", "+++ b/app.py", "@@ -1,4 +1,5 @@",
  " def f(x):", "-    return x.value", "+    return x.value or 0", "+    # note", " # tail",
].join("\n");

test("annotateDiff: pairs a removed line with the added line that replaces it and leaves the rest alone", () => {
  const rows = U.annotateDiff(U.parseDiff(PATCH));
  const del = rows.find((row) => row.kind === "del");
  const adds = rows.filter((row) => row.kind === "add");
  assert.equal(del.segments.map((s) => s.text).join(""), "    return x.value");
  assert.equal(adds[0].segments.filter((s) => s.changed).map((s) => s.text).join(""), " or 0");
  assert.equal(adds[1].segments, null); // an extra added line has nothing to pair with
  assert.equal(rows.find((row) => row.kind === "ctx").segments, null);
  assert.equal(rows.find((row) => row.kind === "hunk").segments, null);
});

test("annotateDiff: does not mutate its input and survives a patch with no changes at all", () => {
  const parsed = U.parseDiff(PATCH);
  const copy = JSON.stringify(parsed);
  U.annotateDiff(parsed);
  assert.equal(JSON.stringify(parsed), copy);
  assert.deepEqual(U.annotateDiff([]), []);
  assert.equal(U.annotateDiff(U.parseDiff("")).length, 1);
});

test("splitDiff: context on both sides, a removed block beside its replacement, headers across", () => {
  const split = U.splitDiff(U.annotateDiff(U.parseDiff(PATCH)));
  assert.equal(split.filter((row) => row.full).length, 3); // ---, +++, @@
  const changed = split.filter((row) => row.left && row.right && row.left.kind === "del");
  assert.equal(changed.length, 1);
  assert.equal(changed[0].right.kind, "add");
  const padded = split.find((row) => row.left === null);
  assert.equal(padded.right.text, "+    # note"); // the extra added line sits opposite an empty cell
  const context = split.filter((row) => row.left && row.left === row.right);
  assert.equal(context.length, 2);
});

test("splitDiff: a pure removal and a pure addition leave the other side empty", () => {
  const removed = U.splitDiff(U.parseDiff("@@ -1,2 +1,1 @@\n keep\n-gone"));
  assert.equal(removed[removed.length - 1].right, null);
  const added = U.splitDiff(U.parseDiff("@@ -1,1 +1,2 @@\n keep\n+new"));
  assert.equal(added[added.length - 1].left, null);
});

// ------------------------------------------------------------------ queue summary

const SUMMARY_NOW = Date.parse("2026-10-26T12:00:00Z");
const waiting = (id, status, createdAt) => ({ incident_id: id, status, created_at: createdAt });

test("duration: short phrases, never negative or NaN", () => {
  assert.equal(U.duration(0), "less than a minute");
  assert.equal(U.duration(59_999), "less than a minute");
  assert.equal(U.duration(5 * 60_000), "5 min");
  assert.equal(U.duration(3 * 3_600_000 + 20 * 60_000), "3 h");
  assert.equal(U.duration(47 * 3_600_000), "47 h");
  assert.equal(U.duration(72 * 3_600_000), "3 d");
  for (const bad of [-5, NaN, Infinity, "x", null, undefined, {}]) assert.equal(U.duration(bad), "less than a minute");
});

test("queueSummary: oldest first per lane, counts, age of the oldest and the over-a-day count", () => {
  const items = [
    waiting("new", "awaiting_approval", "2026-10-26T11:30:00Z"),
    waiting("old", "awaiting_approval", "2026-10-24T08:00:00Z"),
    waiting("day", "awaiting_approval", "2026-10-25T10:00:00Z"),
    waiting("r1", "needs_review", "2026-10-26T09:00:00Z"),
    waiting("done", "pr_created", "2026-10-01T09:00:00Z"),
  ];
  const summary = U.queueSummary(items, SUMMARY_NOW);
  assert.deepEqual(summary.awaiting.ids, ["old", "day", "new"]);
  assert.equal(summary.awaiting.count, 3);
  assert.equal(summary.awaiting.oldest.incident_id, "old");
  assert.equal(summary.awaiting.oldestAgeMs, SUMMARY_NOW - Date.parse("2026-10-24T08:00:00Z"));
  assert.equal(summary.awaiting.stale, 2); // "old" and "day" are past 24 h; "new" is not
  assert.deepEqual(summary.review.ids, ["r1"]);
  assert.equal(summary.review.stale, 0);
});

test("queueSummary: empty lanes, bad dates and garbage input", () => {
  const empty = U.queueSummary([], SUMMARY_NOW);
  assert.deepEqual([empty.awaiting.count, empty.awaiting.oldest, empty.awaiting.oldestAgeMs], [0, null, null]);
  const bad = U.queueSummary([waiting("a", "awaiting_approval", "not a date"), waiting("b", "awaiting_approval", "2026-10-26T11:00:00Z")], SUMMARY_NOW);
  assert.deepEqual(bad.awaiting.ids, ["b", "a"]); // an unreadable date sorts last, never first
  assert.equal(bad.awaiting.oldest.incident_id, "b");
  assert.equal(bad.awaiting.stale, 0);
  const future = U.queueSummary([waiting("f", "awaiting_approval", "2026-12-01T00:00:00Z")], SUMMARY_NOW);
  assert.equal(future.awaiting.oldestAgeMs, 0); // clock skew: never a negative wait
  for (const junk of [null, undefined, "x", 3, {}, [null, 1, "a", { status: "awaiting_approval" }, { incident_id: 5, status: "awaiting_approval" }]]) {
    assert.equal(U.queueSummary(junk, SUMMARY_NOW).awaiting.count, 0);
  }
});

test("queueSummary: ties break by id so the order is stable", () => {
  const same = "2026-10-26T10:00:00Z";
  const summary = U.queueSummary([waiting("b", "awaiting_approval", same), waiting("a", "awaiting_approval", same)], SUMMARY_NOW);
  assert.deepEqual(summary.awaiting.ids, ["a", "b"]);
});

test("queueSummary: an incident that appears twice (overlapping pages) counts once, at its oldest entry", () => {
  const summary = U.queueSummary([
    waiting("a", "awaiting_approval", "2026-10-26T11:00:00Z"),
    waiting("a", "awaiting_approval", "2026-10-26T10:00:00Z"),
  ], SUMMARY_NOW);
  assert.deepEqual(summary.awaiting.ids, ["a"]);
  assert.equal(summary.awaiting.count, 1);
});
