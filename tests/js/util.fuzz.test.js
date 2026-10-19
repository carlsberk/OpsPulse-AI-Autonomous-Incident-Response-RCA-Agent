// Fuzzing the console's pure helpers with a seeded generator (no dependencies). They receive server data and the
// URL, i.e. input nobody on the page controls, so the properties are "total" (never throws, right shape) plus a few
// real invariants (a sort is a stable permutation, URL state round-trips, counts add up, no prototype pollution).
// A failure prints its seed: re-run with FUZZ_SEED=<seed> to reproduce it exactly.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const U = require("../../src/console/static/util.js");

const SEED = Number(process.env.FUZZ_SEED || 20261019);
const RUNS = Number(process.env.FUZZ_RUNS || 1500);

function rng(seed) {
  let a = seed >>> 0;
  return function () { // mulberry32
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gen(random) {
  const pick = (list) => list[Math.floor(random() * list.length)];
  const int = (low, high) => low + Math.floor(random() * (high - low + 1));
  const NASTY = [
    "", " ", "__proto__", "constructor", "toString", "hasOwnProperty", "prototype", "\u0000", "‮", "​",
    "é", "\u{1F600}", "<script>alert(1)</script>", "%", "%zz", "%E0%A4%A", "a=b&c=d", "\n", "\r\n", "**bold**",
    "`code`", "x".repeat(300), "0", "-1", "1e999", "NaN", "null", "undefined", "[object Object]",
  ];
  const text = () => (random() < 0.5 ? pick(NASTY) : Array.from({ length: int(0, 12) }, () => String.fromCharCode(int(32, 0x2fff))).join(""));
  const scalar = () => pick([null, undefined, true, false, 0, -0, 1, -1, 0.5, 1e21, NaN, Infinity, -Infinity, text(), text(), [], {}, [1, "a"], { a: 1 }]);
  const status = () => pick(["awaiting_approval", "needs_review", "failed", "queued", "pr_created", text(), null, undefined, 3]);
  const iso = () => pick(["2026-10-13T12:00:00Z", "", null, undefined, "not a date", 0, 1e15, "+275760-09-13T00:00:00Z", "0000-01-01", text(), {}, NaN]);
  const item = () => ({
    incident_id: scalar(), repo_name: scalar(), affected_file: scalar(), submitted_by: scalar(), status: status(),
    error_category: scalar(), quality_score: pick([scalar(), random(), "0.5", "x"]), iterations: pick([scalar(), int(0, 9), "3"]),
    created_at: iso(),
  });
  const items = () => Array.from({ length: int(0, 12) }, item);
  return { pick, int, text, scalar, status, iso, item, items, random };
}

function forAll(name, property) {
  test(name, () => {
    const random = rng(SEED);
    const g = gen(random);
    for (let run = 0; run < RUNS; run++) {
      try {
        property(g, run);
      } catch (error) {
        error.message = `${error.message}\n  [fuzz] seed=${SEED} run=${run} (re-run with FUZZ_SEED=${SEED})`;
        throw error;
      }
    }
  });
}

forAll("ago, humanize, plainMarkup and toneForScore are total and return the right type", (g) => {
  assert.equal(typeof U.ago(g.iso(), g.pick([undefined, NaN, 0, Date.now(), 1e15, "x"])), "string");
  assert.equal(typeof U.humanize(g.scalar()), "string");
  const plain = U.plainMarkup(g.text());
  assert.equal(typeof plain, "string");
  assert.ok(!plain.includes("**") && !plain.includes("`"));
  assert.ok(["ok", "warn", "bad"].includes(U.toneForScore(g.scalar())));
});

forAll("filterItems returns an ordered subset and an empty filter returns everything", (g) => {
  const items = g.items();
  const out = U.filterItems(items, { query: g.pick([g.text(), "", "  ", undefined]), chip: g.pick(["", "failed", g.text(), undefined]) });
  let cursor = -1;
  for (const item of out) {
    const at = items.indexOf(item, cursor + 1);
    assert.ok(at > cursor, "result is not an ordered subset of the input");
    cursor = at;
  }
  assert.deepEqual(U.filterItems(items, {}), items);
  assert.deepEqual(U.filterItems(items, undefined), items);
});

forAll("sortItems is a stable permutation, never mutates, and is idempotent", (g) => {
  const items = g.items();
  const snapshot = [...items];
  const sort = g.pick([undefined, null, {}, { key: g.pick(U.SORT_KEYS), dir: g.pick(["asc", "desc", g.text()]) }, { key: g.text(), dir: "asc" }]);
  const out = U.sortItems(items, sort);
  assert.deepEqual(items, snapshot, "the input array was mutated");
  assert.equal(out.length, items.length);
  assert.deepEqual([...out].sort((a, b) => items.indexOf(a) - items.indexOf(b)), items, "not a permutation");
  assert.deepEqual(U.sortItems(out, sort), out, "sorting twice changed the order");
});

forAll("sorting by score or attempts is monotonic in the requested direction", (g) => {
  const items = g.items();
  const key = g.pick(["score", "attempts"]);
  const dir = g.pick(["asc", "desc"]);
  const value = (item) => Number(key === "score" ? item.quality_score : item.iterations) || 0;
  const out = U.sortItems(items, { key, dir });
  for (let i = 1; i < out.length; i++) {
    const ok = dir === "asc" ? value(out[i - 1]) <= value(out[i]) : value(out[i - 1]) >= value(out[i]);
    assert.ok(ok, `order broken at ${i} for ${key}/${dir}`);
  }
});

forAll("statusCounts adds up, lists every status once and cannot pollute prototypes", (g) => {
  const items = g.items().map((item) => ({ ...item, status: g.pick(["__proto__", "constructor", "toString", "hasOwnProperty", "failed", g.text()]) }));
  const { counts, order } = U.statusCounts(items);
  const keys = new Set(items.map((item) => item.status));
  const total = [...keys].reduce((sum, status) => sum + (Object.hasOwn(counts, status) ? counts[status] : 0), 0);
  assert.equal(total, items.length, "counts do not add up (a special status name was lost or corrupted)");
  assert.equal(new Set(order).size, order.length, "a status is listed twice");
  for (const status of keys) assert.ok(order.includes(status) || status === "__proto__" || !keys.has(status), `${status} missing from order`);
  assert.equal({}.polluted, undefined);
  assert.equal(typeof ({}).constructor, "function");
});

forAll("parseDiff keeps every line, numbers lines monotonically and never throws", (g) => {
  const lines = Array.from({ length: g.int(0, 15) }, () =>
    g.pick(["@@ -1,3 +1,4 @@", "@@ -999999999999999999999,2 +5 @@", "+added", "-removed", " context", "--- a/x", "+++ b/x", "\\ No newline", "", g.text(), "@@ bad @@"]));
  const patch = g.pick([lines.join("\n"), lines.join("\r\n"), g.scalar(), undefined, null]);
  const rows = U.parseDiff(patch);
  const expected = patch === undefined || patch === null || patch === "" ? 1 : String(patch).split("\n").length;
  assert.equal(rows.length, expected);
  for (const row of rows) {
    assert.ok(["hunk", "meta", "add", "del", "ctx"].includes(row.kind));
    assert.equal(typeof row.text, "string");
  }
  let lastLeft = -Infinity;
  for (const row of rows) {
    if (row.kind === "hunk") lastLeft = -Infinity;
    if (row.kind === "del" || row.kind === "ctx") {
      const left = Number(row.left);
      assert.ok(left > lastLeft || Number.isNaN(left) || left === Infinity, "old line numbers must increase inside a hunk");
      lastLeft = left;
    }
  }
});

forAll("readState always yields a valid view state, whatever the URL says", (g) => {
  const parts = Array.from({ length: g.int(0, 5) }, () =>
    `${g.pick(["status", "q", "sort", "theme", g.text()])}=${g.pick(["all", "failed", "score:asc", "created:sideways", "dark", "auto", encodeURIComponent(g.text()), g.text()])}`);
  const state = U.readState(g.pick(["?" + parts.join("&"), parts.join("&"), g.text(), undefined, ""]));
  assert.ok(state.status === null || state.status === "" || U.STATUS_OPTIONS.includes(state.status));
  assert.ok(typeof state.query === "string" && state.query.length <= 100);
  assert.ok(U.SORT_KEYS.includes(state.sort.key) && ["asc", "desc"].includes(state.sort.dir));
  assert.ok(state.theme === null || ["light", "dark", "auto"].includes(state.theme));
});

forAll("view state survives a write and read round trip", (g) => {
  const state = {
    status: g.pick([null, "", ...U.STATUS_OPTIONS]),
    query: g.pick(["", "shop", "a b&c=d", "ünï", "100%", "x".repeat(60)]),
    sort: { key: g.pick(U.SORT_KEYS), dir: g.pick(["asc", "desc"]) },
    theme: g.pick([null, "light", "dark", "auto"]),
  };
  const again = U.readState(U.writeState(state));
  assert.deepEqual(again, state);
});
