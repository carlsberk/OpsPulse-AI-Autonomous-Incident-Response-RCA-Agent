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
