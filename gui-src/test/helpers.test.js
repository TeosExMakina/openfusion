// Unit tests for the app's pure helpers: node --test test/   (npm test)
import test from "node:test";
import assert from "node:assert/strict";
import { normalizeNum, evalNum, splitList, unquote, TOOLS, GROUPS, OP_TO_TOOL, keyLabel, VIEW_KEYS, ICONS, BODY_TOOLS } from "../src/tools.js";
import { parsePoints, offsetRef, fmt } from "../src/dialog.js";
import { exportName, exportUrl, fmtBytes } from "../src/exporter.js";
import { normalize } from "../src/params.js";
import { buildFrame } from "../src/ghost.js";
import { frameFromNormal, viewDir } from "../src/viewport.js";
import { esc, q } from "../src/api.js";

test("normalizeNum: bare numbers stay numbers, units and params pass through", () => {
  assert.equal(normalizeNum("10", {}), "10");
  assert.equal(normalizeNum(" 2.5 ", {}), "2.5");
  assert.equal(normalizeNum("", {}), "");
  assert.equal(normalizeNum("P.w * 2", { w: "40 * mm" }), "P.w * 2");
});

test("evalNum: arithmetic, units and parameters", () => {
  assert.equal(evalNum("2 * 3", {}), 6);
  assert.equal(evalNum("1 * cm", {}), 10);
  assert.equal(evalNum("P.w / 2", { w: "40 * mm" }), 20);
  assert.equal(evalNum("-(4)", {}), -4);
  assert.equal(evalNum("nonsense(", {}), null);
  assert.equal(evalNum("", {}), null);
});

test("splitList: top-level commas only", () => {
  assert.deepEqual(splitList("[a, b.face(\"+Z\"), edge_between(x, y)]"), ["a", "b.face(\"+Z\")", "edge_between(x, y)"]);
  assert.deepEqual(splitList("[]"), []);
  assert.deepEqual(splitList("(3, 10)"), ["3", "10"]);
});

test("unquote strips one pair of quotes", () => {
  assert.equal(unquote('"+Z"'), "+Z");
  assert.equal(unquote("'local'"), "local");
  assert.equal(unquote("plain"), "plain");
});

test("parsePoints reads the polyline text back", () => {
  assert.deepEqual(parsePoints("[(0, 0), (12.5, -3), (1e-3, 4)]"), [[0, 0], [12.5, -3], [0.001, 4]]);
  assert.deepEqual(parsePoints(""), []);
  assert.deepEqual(parsePoints("junk"), []);
});

test("offsetRef leaves out zero offsets", () => {
  assert.equal(offsetRef('b.anchor("+Z")', 0, 0), 'b.anchor("+Z")');
  assert.equal(offsetRef('b.anchor("+Z")', 5, 0), 'b.anchor("+Z").offset(x=5)');
  assert.equal(offsetRef('b.anchor("+Z")', -1.5, 2), 'b.anchor("+Z").offset(x=-1.5, y=2)');
});

test("fmt rounds to 0.001", () => {
  assert.equal(fmt(1 / 3), "0.333");
  assert.equal(fmt(10), "10");
  assert.equal(fmt(-0.0004), "0");
});

test("export names and URLs", () => {
  assert.equal(exportName("part", "stl", ["plate"], false), "part-plate.stl");
  assert.equal(exportName("part", "3mf", ["a", "b"], false), "part.3mf");
  assert.equal(exportName("part", "stl", ["a", "b"], true), "part-stl.zip");
  assert.equal(exportUrl("stl", ["a", "b#1"], true, "fine"), "/api/export?fmt=stl&bodies=a,b%231&each=1&quality=fine");
  assert.equal(fmtBytes(500), "500 B");
  assert.equal(fmtBytes(2048), "2.0 KB");
  assert.equal(fmtBytes(5 * 1048576), "5.0 MB");
});

test("parameter values: bare numbers are millimetres", () => {
  assert.equal(normalize("3.2"), "3.2 * mm");
  assert.equal(normalize("-4"), "-4 * mm");
  assert.equal(normalize("P.w * 2"), "P.w * 2");
});

test("buildFrame mirrors the engine: spin about Z, negative height turns the frame over", () => {
  const base = { o: [0, 0, 0], x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
  const s = buildFrame(base, 90, 5);
  assert.ok(Math.abs(s.x.x) < 1e-9 && Math.abs(s.x.y - 1) < 1e-9);
  const f = buildFrame(base, 0, -5, 10);
  assert.deepEqual([f.z.x, f.z.y, f.z.z], [-0, -0, -1]);
  assert.equal(f.o.y, 10);                       // corner box keeps its footprint
  const fy = f.z.clone().cross(f.x);              // still right-handed
  assert.ok(fy.distanceTo(f.y) < 1e-9);
});

test("frameFromNormal and view directions", () => {
  const fr = frameFromNormal([0, 0, 0], [0, 0, 1]);
  assert.deepEqual(fr.x.map(Math.round), [1, 0, 0]);
  const side = frameFromNormal([0, 0, 0], [1, 0, 0]);
  assert.deepEqual(side.y.map(Math.round), [0, 0, 1]);   // Y points up on walls
  assert.ok(viewDir("top").z > 0.99 && viewDir("front").y < -0.99);
});

test("html escaping and Python string literals", () => {
  assert.equal(esc('<a href="x">&'), "&lt;a href=&quot;x&quot;&gt;&amp;");
  assert.equal(q('+Z'), '"+Z"');
});

test("every tool is consistent: in a group or a menu, an icon, unique shortcuts", () => {
  const inGroups = new Set(GROUPS.flatMap((g) => g.tools));
  const keys = new Map();
  for (const [id, t] of Object.entries(TOOLS)) {
    if (id !== "measure") assert.ok(inGroups.has(id), `${id} is in no toolbar group`);
    assert.ok(ICONS[id], `${id} has no icon`);
    for (const k of [t.key, t.alt].filter(Boolean)) {
      assert.ok(!keys.has(k), `${id} and ${keys.get(k)} share the shortcut ${k}`);
      keys.set(k, id);
    }
    for (const f of t.fields || []) assert.ok(f.key && f.kind, `${id} has a field without key or kind`);
  }
  for (const g of GROUPS) for (const id of [...g.main, ...g.tools]) assert.ok(TOOLS[id], `group ${g.id} lists unknown tool ${id}`);
  for (const id of BODY_TOOLS) assert.ok(TOOLS[id], `BODY_TOOLS lists unknown ${id}`);
  assert.ok(!keys.has("Shift+E") && !keys.has("Shift+V"), "Export / Parameters shortcuts are taken by a tool");
  assert.equal(OP_TO_TOOL.box, "box");
  assert.ok(keyLabel("Shift+F").endsWith("F"));
  assert.equal(new Set(VIEW_KEYS.map((v) => v[0])).size, VIEW_KEYS.length);
});

test("dialog shortcut words", async () => {
  const { keyWords } = await import("../src/tools.js");
  assert.match(keyWords("B"), /^(Control|Alt)\+B$/);
  assert.match(keyWords("Shift+F"), /^(Control|Alt)\+Shift\+F$/);
  assert.equal(keyWords(""), "");
});
