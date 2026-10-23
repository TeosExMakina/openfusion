// The floating tool dialog.
//
// Nothing is built until OK: the dialog shows the line it will write, primitives show a
// ghost drawn in the browser (with arrows to drag their sizes), Move moves the body on screen,
// and OK sends one statement to the engine. If that step fails, nothing is written and the
// error stays in the dialog. "Live result" turns on a full engine preview for those who want it.
import { $, esc, post, q, toast } from "./api.js";
import { TOOLS, evalNum, keyLabel, keyWords, normalizeNum, splitList, unquote } from "./tools.js";
import { Gizmo } from "./gizmo.js";
import { Ghost } from "./ghost.js";
import * as THREE from "three";

const SLOT_KINDS = new Set(["anchor", "body", "bodies", "edges", "face", "facename", "line", "faces", "path", "points"]);
const MULTI = new Set(["bodies", "edges", "faces", "path"]);
const WORLD_FRAME = { o: [0, 0, 0], x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const PLANE_HINT = "Click the face (or anchor) to build on, or a dot to start right there. Empty space starts on the bed.";
const START_HINT = "Click the start point anywhere on this plane. Dots snap; Tab cycles; the point rounds to the grid.";
const LIVE_KEY = "of-live-result";

const at = (fr, x, y) => [0, 1, 2].map((i) => fr.o[i] + x * fr.x[i] + y * fr.y[i]);

// ref.offset(x=…, y=…), leaving out zeros
export function offsetRef(ref, x, y) {
  const parts = [];
  if (Math.abs(x) > 1e-9) parts.push(`x=${fmt(x)}`);
  if (Math.abs(y) > 1e-9) parts.push(`y=${fmt(y)}`);
  return parts.length ? `${ref}.offset(${parts.join(", ")})` : ref;
}

function readLive() { try { return localStorage.getItem(LIVE_KEY) === "1"; } catch (e) { return false; } }
function writeLive(v) { try { localStorage.setItem(LIVE_KEY, v ? "1" : "0"); } catch (e) { /* storage unavailable */ } }

export class Dialog {
  constructor(app, toolId, { edit = null, args = {}, preset = {}, frames = {}, plane = null } = {}) {
    this.app = app; this.vp = app.vp; this.id = toolId; this.tool = TOOLS[toolId];
    this.edit = edit;
    this.op = edit ? app.featureOp(edit) : this.tool.op;
    this.values = {}; this.items = {}; this.frames = { ...frames };
    this.active = null; this.cands = null; this.candIdx = 0; this.sizing = null; this.stage = null;
    this.timer = null; this.seq = 0; this.live = readLive(); this.resolveTimers = {};
    for (const f of this.tool.fields || []) this.init(f, args[f.key]);
    Object.assign(this.values, preset);
    for (const [k, v] of Object.entries(preset)) if (this.isMulti(k)) this.items[k] = splitList(v);
    this.render();
    if (this.tool.size) this.ghost = new Ghost(this.vp, (k, v) => this.onArrow(k, v));
    const firstEmpty = (this.tool.fields || []).find((f) => SLOT_KINDS.has(f.kind) && !f.hidden && !f.noAuto && !f.planes && !this.text(f.key));
    this.activate(firstEmpty ? firstEmpty.key : null);
    if (this.tool.size && !edit) {
      if (plane) {                                   // a face was selected: build on it
        this.plane = plane; this.stage = "start";
        this.vp.showLocalGrid(plane.base.frame);
        this.app.setHint(START_HINT);
      } else if (this.active === "at") { this.stage = "plane"; this.app.setHint(PLANE_HINT); }
    }
    if (this.tool.polyline) {
      this.pts = parsePoints(this.values.points);
      this.polyGroup = new THREE.Group(); this.vp.scene.add(this.polyGroup);
      if (!edit && plane) this.beginPoints(plane.base, plane);
      else if (!edit) { this.stage = "plane"; this.active = "at"; this.markActive(); this.app.setHint(PLANE_HINT); }
    }
    if (edit) for (const f of this.tool.fields || []) if (f.kind === "anchor" && this.values[f.key]) this.resolve(f.key);
    if (this.tool.gizmo) this.setupGizmo();
    if (this.tool.inspect && edit && app.state) {
      this.hiddenCopies = app.state.scene.bodies.map((b) => b.id).filter((id) => id.startsWith(edit + "_") && !this.vp.hidden.has(id));
      this.hiddenCopies.forEach((id) => this.vp.hidden.add(id));
      if (this.hiddenCopies.length) this.vp.setScene(app.state.scene);
    }
    if (!firstEmpty && !(this.tool.size && !edit)) this.focusFirst();
    this.schedule();
  }

  field(key) { return (this.tool.fields || []).find((f) => f.key === key); }
  isMulti(key) { const f = this.field(key); return f && MULTI.has(f.kind); }

  init(f, text) {
    if (f.kind === "scale") {          // one factor (uniform) or (sx, sy, sz)
      const t = (text || "").trim();
      const parts = t.startsWith("(") || t.startsWith("[") ? splitList(t) : [t || f.def || "1", t || f.def || "1", t || f.def || "1"];
      [0, 1, 2].forEach((i) => (this.values[`${f.key}.${i}`] = parts[i] || parts[0] || "1"));
      this.values[f.key + ".lock"] = !(t.startsWith("(") || t.startsWith("[")) || new Set(parts).size === 1;
    } else if (f.kind === "pair") {
      const parts = text ? splitList(text) : (f.def || ["", ""]);
      this.values[f.key + ".0"] = parts[0] || ""; this.values[f.key + ".1"] = parts[1] || "";
    } else if (MULTI.has(f.kind)) {
      this.items[f.key] = text ? (text.trim().startsWith("[") ? splitList(text) : [text.trim()]) : [];
    } else if (f.kind === "choice") {
      this.values[f.key] = text ? unquote(text) : f.def;
    } else if (f.kind === "text" || f.kind === "facename") {
      this.values[f.key] = text ? unquote(text) : (f.def || "");
    } else {
      this.values[f.key] = text ?? (f.def || "");
    }
  }

  text(key) {
    const f = this.field(key);
    if (!f) return "";
    if (f.kind === "path") { const l = this.items[key] || []; return l.length === 1 && !l[0].includes(".edge") && !l[0].includes(".edges(") ? l[0] : l.length ? `[${l.join(", ")}]` : ""; }
    if (MULTI.has(f.kind)) return (this.items[key] || []).length ? `[${this.items[key].join(", ")}]` : "";
    return this.values[key] || "";
  }

  // ------------------------------------------------------------------ args for the server
  args() {
    const params = this.app.state?.desc?.params || {};
    const out = {};
    for (const f of this.tool.fields || []) {
      let v = "";
      if (f.kind === "num") v = normalizeNum(this.values[f.key], params);
      else if (f.kind === "scale") {
        const xs = [0, 1, 2].map((i) => normalizeNum(this.values[`${f.key}.${i}`], params));
        if (xs.every((x) => x)) v = this.values[f.key + ".lock"] || new Set(xs).size === 1 ? xs[0] : `(${xs.join(", ")})`;
      } else if (f.kind === "pair") {
        const a = normalizeNum(this.values[f.key + ".0"], params), b = normalizeNum(this.values[f.key + ".1"], params);
        if (a && b) v = `(${a}, ${b})`;
      } else if (f.kind === "choice") {
        const val = this.values[f.key];
        v = val === f.def ? "" : (f.raw || []).includes(val) ? val : q(val);
      } else if (f.kind === "text" || f.kind === "facename") v = this.values[f.key] ? q(this.values[f.key]) : "";
      else v = this.text(f.key);
      if (f.kind === "num" && f.key === "spin" && (v === "0" || v === "0.0")) v = "";
      if (v !== "") out[f.key] = v;
    }
    // a hidden body field (fillet, chamfer) follows the edges
    const hb = (this.tool.fields || []).find((f) => f.kind === "body" && f.hidden);
    if (hb && !out[hb.key] && (out.edges || out.faces)) {
      const m = (splitList(out.edges || out.faces)[0] || "").match(/^([A-Za-z_][A-Za-z0-9_]*(\[\d+\])?)\./);
      if (m) out[hb.key] = m[1];
    }
    if (this.edit) {   // keep arguments this dialog does not show (typed in the script)
      const known = new Set((this.tool.fields || []).map((f) => f.key));
      for (const [k, v] of Object.entries(this.app.featureArgs(this.edit))) if (!known.has(k)) out[k] = v;
    }
    return out;
  }

  missing(args = this.args()) { return (this.tool.fields || []).filter((f) => f.required && !args[f.key]); }

  // numbers for drawing (ghost, gizmo): evaluates P.name, units and arithmetic
  num(key, fallback = 0) {
    const v = evalNum(this.values[key], this.app.state?.desc?.params || {});
    return v === null ? fallback : v;
  }

  // ------------------------------------------------------------------ rendering
  render() {
    const d = $("dialog");
    d.hidden = false; d.className = "dialog";
    const t = this.tool;
    const opSel = t.ops && !this.edit
      ? `<select id="dlg-op" aria-label="Operation">${t.ops.map((o) => `<option value="${o}" ${o === this.op ? "selected" : ""}>${o[0].toUpperCase() + o.slice(1)}</option>`).join("")}</select>` : "";
    const title = this.edit ? `Edit ${esc(this.edit)}` : esc(t.label);
    let html = `<div class="dlg-head"><b>${title}</b>${opSel}<span>${esc(this.op || "")}</span></div>`;
    if (t.help) html += `<div class="dlg-help">${esc(t.help)}</div>`;
    if (t.key) {
      const keys = [t.key, t.alt].filter(Boolean);
      html += `<div class="dlg-key">Shortcut: ${keys.map((k) => `<kbd>${esc(keyLabel(k))}</kbd> <span>${esc(keyWords(k))}</span>`).join(" or ")}</div>`;
    }
    if (this.id === "measure") html += `<div class="measure-out" id="measure-out">Pick the first point.</div>`;
    html += `<div class="fields">`;
    for (const f of t.fields || []) {
      if (f.hidden) continue;
      const id = `fld-${f.key}`;
      html += `<div class="field"><label for="${id}">${esc(f.label)}${f.unit ? ` <small>${esc(f.unit)}</small>` : ""}</label>`;
      const flip = f.flip ? `<button type="button" class="pick flip" tabindex="-1" data-flip="${f.kind === "pair" ? f.key + ".1" : f.key}" title="Flip the direction (negates the value)" aria-label="Flip ${esc(f.label)}">⇄</button>` : "";
      if (f.kind === "num" || f.kind === "text") {
        const input = `<input id="${id}" data-key="${f.key}" value="${esc(this.values[f.key] || "")}" placeholder="${esc(f.placeholder || "")}" autocomplete="off" spellcheck="false">`;
        const file = f.file ? `<button type="button" class="pick" tabindex="-1" data-file="${f.key}" data-accept="${esc(f.file)}">Choose…</button>` : "";
        html += flip ? `<div class="row flipper">${input}${flip}</div>` : file ? `<div class="slot">${input}${file}</div>` : input;
      } else if (f.kind === "scale") {
        const lock = this.values[f.key + ".lock"];
        html += `<div class="row scale-row">${["X", "Y", "Z"].map((a, i) => `<input ${i ? "" : `id="${id}"`} data-key="${f.key}.${i}" aria-label="${esc(f.label)} ${a}" placeholder="${a}" value="${esc(this.values[`${f.key}.${i}`])}" autocomplete="off">`).join("")}` +
          `<button type="button" class="pick lock" data-lock="${f.key}" aria-pressed="${lock}" title="${lock ? "Uniform: X, Y and Z change together. Click to set them separately." : "Separate X, Y and Z. Click to make them change together."}">${lock ? "🔗 Uniform" : "XYZ"}</button></div>`;
      } else if (f.kind === "pair") {
        html += `<div class="row${flip ? " flipper" : ""}"><input id="${id}" data-key="${f.key}.0" placeholder="${esc(f.labels[0])}" value="${esc(this.values[f.key + ".0"])}" autocomplete="off">` +
          `<input data-key="${f.key}.1" aria-label="${esc(f.label)} ${esc(f.labels[1])}" placeholder="${esc(f.labels[1])}" value="${esc(this.values[f.key + ".1"])}" autocomplete="off">${flip}</div>`;
      } else if (f.kind === "choice") {
        html += `<select id="${id}" data-key="${f.key}">${f.options.map(([v, l]) => `<option value="${esc(v)}" ${v === this.values[f.key] ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
      } else {
        const planes = f.planes ? ["XY", "XZ", "YZ"].map((p) => `<button type="button" class="pick" tabindex="-1" data-plane="${p}">${p}</button>`).join("") : "";
        const all = f.kind === "edges" ? `<button type="button" class="pick" tabindex="-1" data-all="${f.key}" title="Every edge of the body">All</button>`
          : f.axes ? ["X", "Y"].map((a) => `<button type="button" class="pick" tabindex="-1" data-axis="${a}" title="The profile's own ${a} axis">${a}</button>`).join("") : "";
        const pickLabel = f.key === "pivot" ? "Set" : "Pick";
        html += `<div class="slot" data-slot="${f.key}"><input id="${id}" data-key="${f.key}" value="${esc(this.text(f.key))}" placeholder="${esc(f.placeholder || "click in the view")}" autocomplete="off" spellcheck="false">` +
          `${planes}${all}<button type="button" class="pick" tabindex="-1" data-pick="${f.key}" title="Pick in the view">${pickLabel}</button></div>`;
      }
      html += `</div>`;
    }
    html += `</div>`;
    if (this.id !== "measure") {
      html += `<div class="dlg-stmt" id="dlg-stmt">…</div><div class="dlg-result" id="dlg-result" role="status"></div>`;
      html += `<div class="dlg-foot"><label class="live" title="Run the operation on every change (slower; errors show as you go)">` +
        `<input type="checkbox" id="dlg-live" tabindex="-1" ${this.live ? "checked" : ""}> Live result</label>` +
        `<button type="button" id="dlg-cancel" tabindex="-1">Cancel</button><button type="button" class="primary" id="dlg-ok" tabindex="-1">${this.edit ? "Update" : "OK"}</button></div>`;
    } else {
      html += `<div class="dlg-foot"><span class="live"></span><button type="button" id="dlg-param" disabled>Make parameter</button><button type="button" id="dlg-cancel">Done</button></div>`;
    }
    d.innerHTML = html;
    d.querySelectorAll("input[data-key], select[data-key]").forEach((el) => {
      el.addEventListener("input", () => this.onInput(el));
      el.addEventListener("change", () => this.onInput(el));
      el.addEventListener("focus", () => {
        if (el.closest(".slot")) this.activate(el.dataset.key, true);
        if (el.select) setTimeout(() => { try { el.select(); } catch (e) { /* not selectable */ } }, 0);
      });
    });
    d.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => this.activate(b.dataset.pick)));
    d.querySelectorAll("[data-file]").forEach((b) => b.addEventListener("click", () => this.chooseFile(b.dataset.file, b.dataset.accept)));
    d.querySelectorAll("[data-lock]").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.lock; this.values[k + ".lock"] = !this.values[k + ".lock"];
      if (this.values[k + ".lock"]) [1, 2].forEach((i) => (this.values[`${k}.${i}`] = this.values[`${k}.0`]));
      this.render(); this.schedule();
    }));
    d.querySelectorAll("[data-all]").forEach((b) => b.addEventListener("click", () => this.allEdges(b.dataset.all)));
    d.querySelectorAll("[data-flip]").forEach((b) => b.addEventListener("click", () => this.flip(b.dataset.flip)));
    d.querySelectorAll("[data-axis]").forEach((b) => b.addEventListener("click", () => {
      const key = b.closest(".slot").dataset.slot; this.setSlot(key, q(b.dataset.axis)); this.advance(key);
    }));
    d.querySelectorAll("[data-plane]").forEach((b) => b.addEventListener("click", () => {
      const key = b.closest(".slot").dataset.slot; this.setSlot(key, q(b.dataset.plane));
    }));
    const op = $("dlg-op"); if (op) op.addEventListener("change", () => { this.op = op.value; this.schedule(); });
    $("dlg-cancel").addEventListener("click", () => this.app.closeDialog());
    const ok = $("dlg-ok"); if (ok) ok.addEventListener("click", () => this.ok());
    const live = $("dlg-live"); if (live) live.addEventListener("change", () => { this.live = live.checked; writeLive(this.live); if (!this.live) this.restoreScene(); this.schedule(); });
    const mp = $("dlg-param"); if (mp) mp.addEventListener("click", () => this.makeParam());
    this.markActive();
  }

  inputs() { return [...$("dialog").querySelectorAll(".fields input, .fields select")]; }

  focusFirst() {
    const sizeKeys = this.tool.size ? [this.tool.size.x || this.tool.size.r] : [];
    const el = (sizeKeys[0] && $("dialog").querySelector(`[data-key="${sizeKeys[0]}"]`)) ||
      this.inputs().find((x) => !x.closest(".slot")) || this.inputs()[0];
    if (el) el.focus();
  }

  markActive() {
    $("dialog").querySelectorAll(".slot").forEach((s) => s.classList.toggle("active", s.dataset.slot === this.active));
  }

  onInput(el) {
    const key = el.dataset.key;
    if (key === "points") {
      // typed points: "(0, 0), (10, 0), ..." becomes a list; a list as typed (it may use P.name) stays
      this.pts = parsePoints(el.value);
      const t = el.value.trim();
      this.values.points = !t || t.startsWith("[") || !this.pts.length ? t : `[${this.pts.map(([x, y]) => `(${fmt(x)}, ${fmt(y)})`).join(", ")}]`;
      this.drawPoly(); this.schedule(); return;
    }
    if (this.isMulti(key)) this.items[key] = el.value.trim().startsWith("[") || !el.value.trim() ? splitList(el.value) : [el.value.trim()];
    else this.values[key] = el.value;
    const m = key.match(/^(.+)\.(\d)$/), sf = m && this.field(m[1]);
    if (sf && sf.kind === "scale" && this.values[m[1] + ".lock"]) {      // uniform: the other two follow
      [0, 1, 2].forEach((i) => { const k2 = `${m[1]}.${i}`; this.values[k2] = el.value; const o = $("dialog").querySelector(`input[data-key="${k2}"]`); if (o && o !== el) o.value = el.value; });
    }
    const f = this.field(key);
    if (f && f.kind === "anchor") { delete this.frames[key]; this.resolve(key); }
    this.schedule();
  }

  setSlot(key, text) {
    if (this.isMulti(key)) this.items[key] = splitList(text);
    else this.values[key] = text;
    const el = $("dialog").querySelector(`input[data-key="${key}"]`);
    if (el) el.value = this.text(key);
    this.schedule();
  }

  setVal(key, v, schedule = true) {
    this.values[key] = v;
    const el = $("dialog").querySelector(`[data-key="${key}"]`);
    if (el) el.value = v;
    if (schedule) this.schedule();
  }

  // Typed anchors: ask the engine where they are, for drawing only.
  resolve(key) {
    clearTimeout(this.resolveTimers[key]);
    const expr = this.values[key];
    if (!expr || this.field(key)?.planes && /^["']/.test(expr)) return;
    this.resolveTimers[key] = setTimeout(async () => {
      try {
        const fr = await post("/api/resolve", { expr });
        if (this.values[key] === expr) { this.frames[key] = fr; this.schedule(); }
      } catch (e) { /* not resolvable yet */ }
    }, 250);
  }

  // a file for a field (Import): copied next to the part on the server, the field gets its name
  chooseFile(key, accept) {
    const inp = document.createElement("input");
    inp.type = "file"; inp.accept = accept; inp.style.display = "none";
    document.body.append(inp);
    inp.addEventListener("change", async () => {
      const file = inp.files && inp.files[0];
      inp.remove();
      if (!file) return;
      try {
        const buf = new Uint8Array(await file.arrayBuffer());
        let bin = "";
        for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
        toast(`Copying ${file.name}…`);
        const out = await post("/api/upload", { name: file.name, data: btoa(bin) });
        this.values[key] = out.name;
        this.render(); this.schedule();
        toast(out.renamed ? `Copied as ${out.name} (a different ${file.name} is already there)` : `Copied ${out.name} next to the part`);
      } catch (e) { toast(e.message, true); }
    });
    inp.click();
  }

  activate(key, fromFocus = false) {
    if (key === "points" && this.tool.polyline && !fromFocus && this.frames.at && this.stage !== "points") {
      return this.beginPoints({ ref: this.values.at, frame: this.frames.at }, this.plane, true);
    }
    if (key !== "at") this.stage = this.stage === "plane" || this.stage === "start" || this.stage === "points" ? null : this.stage;
    this.active = key; this.cands = null; this.sizing = null;
    const af = this.field(key);
    this.vp.setOriginForced(!!(af && af.kind === "anchor"));      // the origin planes are clickable while picking an anchor
    this.vp.showSnaps(null); this.vp.clearTemp();
    this.markActive();
    this.app.setHint(key ? this.hintFor(key) : "Adjust values (Tab moves between them), then OK (Enter).");
    if (!fromFocus && key) { const el = $("dialog").querySelector(`[data-key="${key}"]`); if (el && document.activeElement !== el) el.blur(); }
  }

  hintFor(key) {
    const f = this.field(key);
    if (!f) return "";
    if (f.hint) return f.hint;
    return {
      anchor: "Click anywhere on a face (kept as an offset from its anchor). Dots snap; Tab cycles; empty space picks the bed.",
      body: "Click a body (a face or edge of it works too).", bodies: "Click bodies to add or remove them.",
      edges: "Click edges to add or remove them; a face adds all its edges.", face: "Click a face.", facename: "Click the face that goes down.",
      faces: f.ordered ? "Click the cross-sections in order (profiles or flat faces); click again to remove." : "Click faces to add or remove them.",
      path: "Click an open profile, or edges that join end to end (click again to remove).",
      points: "Click points on the plane; click the first point to close, Enter to finish, Backspace to undo a point.",
      line: f.key === "axis" ? "Click a hole, round face or circular edge (its axis), a straight edge (turn about it), or a flat face (its normal through its center)."
        : "Click a straight edge: the direction runs along it (the positive way of its body's axis; flip with ⇄). A flat face gives its axes.",
    }[f.kind] || "";
  }

  // negate a value: 10 -> -10, -10 -> 10, P.gap -> -P.gap, a + b -> -(a + b)
  flip(key) {
    const v = String(this.values[key] ?? "").trim();
    if (!v) return;
    let out;
    if (/^-\((.*)\)$/.test(v)) out = v.slice(2, -1);
    else if (/^-[^-+*/\s()]+$/.test(v)) out = v.slice(1);
    else if (/^[^-+*/\s()]+$/.test(v)) out = "-" + v;
    else out = `-(${v})`;
    this.setVal(key, out);
  }

  // What a click means for a direction or axis: an edge itself, a round face (its axis),
  // or a flat face (its center anchor: axes for a direction, normal for an axis).
  lineRef(hit) {
    if (!hit) return null;
    if (hit.kind === "edge") return this.app.fixRef(hit.ref, true);
    if (hit.kind !== "face") return null;
    const kind = hit.face && hit.face.kind;
    if (kind === "plane") {
      const c = this.vp.snapCandidates({ face: hit.face, body: hit.body, label: hit.label, point: hit.point });
      const center = c.find((x) => x.kind === "center") || c[0];
      return center ? this.app.fixRef(center.ref, true) : null;
    }
    if (["cylinder", "cone", "sphere", "torus"].includes(kind)) return this.app.fixRef(hit.ref, true);
    return null;
  }

  // ------------------------------------------------------------------ viewport interaction
  hover(ev) {
    if (this.sizing) return this.sizeHover(ev);
    if (this.ghost && (this.ghost.active || this.ghost.hit(ev))) return true;
    if (this.gizmo && (this.gizmo.drag || this.gizmo.hit(ev))) return true;
    if (this.stage === "start") return this.startHover(ev);
    if (this.stage === "points") return this.pointsHover(ev);
    if (this.id === "measure") return this.anchorHover(ev);
    const f = this.field(this.active);
    if (!f) return false;
    if (f.kind === "anchor") return this.anchorHover(ev);
    let hit;
    if (f.kind === "edges" || f.kind === "line" || f.kind === "path") hit = this.vp.pickAuto(ev);
    else if (f.kind === "face" || f.kind === "facename" || f.kind === "faces") hit = this.vp.pick(ev, "face");
    else hit = this.vp.pick(ev, "body");
    this.hoverHit = hit;
    if (!hit) { this.vp.setHover(null); this.app.showTip(null); return true; }
    const key = hit.kind === "body" ? "body:" + hit.body : hit.kind + ":" + hit.ref;
    this.vp.setHover(key);
    const tip = hit.kind === "body" ? this.app.exprFor(hit.body, true)
      : f.kind === "line" ? (this.lineRef(hit) || "not usable here: pick an edge or a round face")
      : f.kind === "path" ? this.pathRef(hit)
      : (f.kind === "face" || f.kind === "faces") ? this.faceRef(hit)
      : hit.kind === "face" && f.kind === "edges" ? `${this.app.fixRef(hit.ref, true)}.edges()` : this.app.fixRef(hit.ref, true);
    this.app.showTip(tip, ev);
    return true;
  }

  // A free point where the cursor meets the plane of `base`, kept as an offset from base's
  // anchor and rounded to the visible grid. feats: the face's points (corners, centre, holes...):
  // the point is then measured from the one nearest the cursor, and the grid moves there, so
  // a grid point by a corner is exactly on the corner's lines whatever the face's odd centre.
  freeCandidate(ev, base, bed = false, feats = null) {
    let fr = base.frame;
    let p = this.vp.planePoint(ev, fr);
    if (!p) return null;
    if (feats && feats.length) {
      const w = at(fr, p.x, p.y);
      const near = this.vp.nearestFeature(feats, w, fr);
      if (near) {
        base = near; fr = near.frame;
        p = this.vp.planePoint(ev, fr);
        if (!p) return null;
        const lf = this.vp.localFrame;
        if (lf && Math.abs((fr.o[0] - lf.o[0]) * fr.z[0] + (fr.o[1] - lf.o[1]) * fr.z[1] + (fr.o[2] - lf.o[2]) * fr.z[2]) < 1e-6
            && (lf.o[0] !== fr.o[0] || lf.o[1] !== fr.o[1] || lf.o[2] !== fr.o[2])) {
          this.vp.showLocalGrid(fr);                // same plane, another feature: the grid follows
          this.vp.updateLocalGrid();
        }
      }
    }
    const s = this.vp.snapStep();
    const x = Math.round(p.x / s) * s, y = Math.round(p.y / s) * s;
    const o = at(fr, x, y);
    const ref = bed ? `world.point(${fmt(o[0])}, ${fmt(o[1])}, ${fmt(o[2])})` : offsetRef(base.ref, x, y);
    return { ref, p: o, frame: { ...fr, o }, kind: bed ? "bed" : "free", body: base.body, dx: x, dy: y };
  }

  anchorHover(ev) {
    const knob = this.vp.pickKnob(ev);
    const org = knob ? null : this.vp.pickOrigin(ev);
    this.vp.setOriginHover(org ? org.plane : null);
    if (knob) {
      this.cands = [{ ref: knob.ref, p: knob.frame.o, frame: knob.frame, kind: "anchor", body: knob.body }]; this.candIdx = 0;
    } else if (org) {
      // an origin plane: a point on it (kept as an offset from the plane's anchor), or its centre
      this.vp.setHover(null);
      const center = { ref: org.ref, p: org.frame.o, frame: org.frame, kind: "center" };
      const free = this.freeCandidate(ev, center);
      this.cands = free ? [free, center] : [center];
      this.candIdx = free && this.vp.nearestSnap(ev, [center]) < 0 ? 0 : this.cands.length - 1;
      this.cands.faceRef = org.ref;
    } else {
      const hit = this.vp.pick(ev, "face");
      if (hit) {
        const sameFace = this.cands && this.cands.faceRef === hit.ref;
        const snaps = this.vp.snapCandidates(hit);
        const center = snaps.find((c) => c.kind === "center") || snaps[0];
        const free = hit.face && hit.face.kind === "plane" ? this.freeCandidate(ev, this.vp.gridBase(snaps), false, snaps) : null;
        const cands = free ? [free, ...snaps] : snaps;
        cands.faceRef = hit.ref;
        const near = this.vp.nearestSnap(ev, snaps);
        const auto = near >= 0 ? near + (free ? 1 : 0) : 0;
        this.candIdx = sameFace && this.tabbed ? Math.min(this.candIdx, cands.length - 1) : auto;
        this.tabbed = sameFace && this.tabbed;
        this.cands = cands;
        this.vp.setHover("face:" + hit.ref);
      } else {
        this.vp.setHover(null);
        const bed = this.freeCandidate(ev, { ref: "", frame: WORLD_FRAME }, true);
        if (!bed) { this.cands = null; this.vp.showSnaps(null); this.app.showTip(null); return true; }
        this.cands = [bed]; this.candIdx = 0;
      }
    }
    return this.showCands(ev);
  }

  showCands(ev) {
    const c = this.cands[this.candIdx];
    const dots = this.cands.filter((x) => x.kind !== "free" && x.kind !== "bed");
    this.vp.showSnaps(dots, dots.indexOf(c));
    this.vp.clearTemp();
    if (c.kind === "free" || c.kind === "bed") this.vp.marker(c.p, "--sel", 1.4);
    this.vp.frameMarker(c.frame);
    let tip = this.app.fixRef(c.ref, true);
    if (this.stage === "plane" && c.kind === "free") tip = `Plane: ${this.app.fixRef(this.cands.find((x) => x.kind === "center")?.ref || c.ref, true)}`;
    this.app.showTip(tip, ev);
    return true;
  }

  // second stage for primitives: any point on the chosen plane
  startHover(ev) {
    const pl = this.plane;
    const free = this.freeCandidate(ev, pl.base, pl.bed, pl.bed ? null : [pl.base, ...(pl.snaps || [])]);
    if (!free) return true;
    const near = this.vp.nearestSnap(ev, pl.snaps);
    this.cands = [free, ...pl.snaps];
    if (!this.tabbed) this.candIdx = near >= 0 ? near + 1 : 0;
    return this.showCands(ev);
  }

  // Tab: in the dialog it moves between fields (wrapping); over the view it cycles snap points.
  key(ev) {
    const inField = ev.target.closest && ev.target.closest("input, select, textarea");
    if (this.stage === "points" && !inField) {
      if (ev.key === "Enter") { ev.preventDefault(); this.finishPoints(); return true; }
      if (ev.key === "Backspace" || ev.key === "Delete") { ev.preventDefault(); this.pts.pop(); this.syncPoints(); return true; }
    }
    if (ev.key !== "Tab") return false;
    const inDialog = ev.target.closest && ev.target.closest("#dialog");
    if (!inDialog && this.cands && this.cands.length > 1 && (this.stage || this.active)) {
      ev.preventDefault();
      this.candIdx = (this.candIdx + (ev.shiftKey ? this.cands.length - 1 : 1)) % this.cands.length;
      this.tabbed = true;
      this.showCands();
      return true;
    }
    const els = this.inputs();
    if (!els.length) return false;
    ev.preventDefault();
    const i = els.indexOf(ev.target);
    const next = i < 0 ? 0 : (i + (ev.shiftKey ? els.length - 1 : 1)) % els.length;
    els[next].focus();
    return true;
  }

  click(ev) {
    if (this.sizing) return this.sizeClick(ev);
    if (this.ghost && this.ghost.hit(ev)) return true;
    if (this.gizmo && this.gizmo.hit(ev)) return true;
    if (this.stage === "points") return this.pointsClick(ev);
    if (this.stage && this.active === "at" && this.cands) {
      const c = this.cands[this.candIdx];
      if (this.tool.polyline && this.stage === "plane") {         // a polyline draws on the plane it was started on
        const snaps = this.cands.filter((x) => x.kind !== "free");
        const center = this.vp.gridBase(snaps) || c;
        const base = c.kind === "free" || c.kind === "bed" ? (c.kind === "bed" ? { ref: "", frame: WORLD_FRAME } : center) : c;
        this.beginPoints(base, { base, snaps, bed: c.kind === "bed" }, false, c.kind === "free" ? [c.dx, c.dy] : c.kind === "bed" ? [c.p[0], c.p[1]] : [0, 0]);
        return true;
      }
      if (this.stage === "plane" && c.kind === "free") {          // a plane: now pick the start point on it
        const snaps = this.cands.filter((x) => x.kind !== "free");
        const center = this.vp.gridBase(snaps);
        this.plane = { base: center, snaps, bed: false };
        this.stage = "start"; this.tabbed = false;
        this.vp.showLocalGrid(center.frame);
        this.app.setHint(START_HINT);
        return true;
      }
      this.stage = null; this.tabbed = false;                     // a snap point, anchor, bed point or start point
      this.frames.at = c.frame;
      this.setSlot("at", this.app.fixRef(c.ref));
      this.vp.showLocalGrid(c.kind === "bed" ? null : c.frame);
      this.advance("at");
      return true;
    }
    if (this.id === "measure") return this.measureClick();
    const f = this.field(this.active);
    if (!f) return false;
    if (f.kind === "anchor") {
      if (!this.cands) return true;
      const c = this.cands[this.candIdx];
      this.frames[f.key] = c.frame;
      this.setSlot(f.key, this.app.fixRef(c.ref));
      if (f.setsBody && c.body && this.field("body")) this.setSlot("body", this.app.exprFor(c.body));
      if (c.frame && this.app.state) this.vp.showLocalGrid(c.kind === "bed" ? null : c.frame);
      this.advance(f.key);
      return true;
    }
    const hit = this.hoverHit;
    if (!hit) return true;
    if (f.kind === "body" || f.kind === "bodies") this.takeBody(hit.body);
    else if (f.kind === "edges") this.toggleEdge(f.key, hit);
    else if (f.kind === "faces") {
      const list = this.items[f.key] || [];
      const own = this.bodyIdOf(this.values.body);
      if (!f.ordered && own && hit.body !== own && !this.isProfile(hit.body) && this.field("body") && !this.field("body").hidden) {
        toast(`Pick faces of ${own} (the body being changed)`, true); return true;
      }
      if (!f.ordered && list.length && this.listBody && this.listBody !== hit.body) { this.items[f.key] = []; this.rawSel = {}; toast(`Faces must be on one body; started over on ${hit.body}`); }
      this.listBody = hit.body;
      this.toggleItem(f.key, this.faceRef(hit), "face:" + hit.ref);
    }
    else if (f.kind === "path") {
      const ref = this.pathRef(hit);
      if (this.wholeProfile(hit)) { this.items[f.key] = []; this.setSlot(f.key, ref); this.advance(f.key); }
      else { if ((this.items[f.key] || []).some((x) => !x.includes(".edge"))) this.items[f.key] = []; this.toggleItem(f.key, ref, "edge:" + hit.ref); }
    }
    else if (f.kind === "line") {
      const ref = this.lineRef(hit);
      if (!ref) { toast("Pick a straight edge, a circular edge, or a round or flat face", true); return true; }
      this.setSlot(f.key, ref); this.advance(f.key);
    }
    else if (f.kind === "face") { this.setSlot(f.key, this.faceRef(hit)); this.advance(f.key); }
    else if (f.kind === "facename") {
      const b = this.vp.body(hit.body);
      if (!b.anchors.find((a) => a.name === hit.label)) { toast(`${hit.label} has no design anchor; pick a primitive face such as -Z or +X`, true); return true; }
      if (this.field("body")) this.setSlot("body", this.app.exprFor(hit.body));
      this.setSlot(f.key, hit.label);
      this.advance(f.key);
    }
    return true;
  }

  // an edge, or a face standing for all its edges
  toggleEdge(key, hit) {
    const list = this.items[key] || [];
    if (this.edgeBody && this.edgeBody !== hit.body && list.length) { list.length = 0; this.edgeRaw = []; toast(`Edges must be on one body; started over on ${hit.body}`); }
    this.edgeBody = hit.body; this.edgeRaw = this.edgeRaw || [];
    const ref = this.app.fixRef(hit.ref) + (hit.kind === "face" ? ".edges()" : "");
    const raw = hit.kind + ":" + hit.ref;
    const i = list.indexOf(ref);
    if (i >= 0) { list.splice(i, 1); this.edgeRaw = this.edgeRaw.filter((r) => r !== raw); } else { list.push(ref); this.edgeRaw.push(raw); }
    this.setSlot(key, `[${list.join(", ")}]`);
    if (this.field("body")) this.values.body = list.length ? this.app.exprFor(hit.body) : "";
    this.vp.setSelected(this.edgeRaw);
  }

  isProfile(id) { const b = this.vp.body(id); return !!b && b.kind === "profile"; }

  // a profile stands for its face: write the profile's name, not profile.face("face")
  faceRef(hit) { return this.isProfile(hit.body) ? this.app.exprFor(hit.body, true) : this.app.fixRef(hit.ref, true); }

  isSketch(id) { const b = this.vp.body(id); return !!b && !!b.sketch; }
  // a click that means the whole profile or sketch (not one of a sketch's curves)
  wholeProfile(hit) { return this.isProfile(hit.body) && !(hit.kind === "edge" && this.isSketch(hit.body)); }

  pathRef(hit) {
    if (this.wholeProfile(hit)) return this.app.exprFor(hit.body, true);
    return this.app.fixRef(hit.ref, true) + (hit.kind === "face" ? ".edges()" : "");
  }

  toggleItem(key, ref, raw) {
    const list = this.items[key] || (this.items[key] = []);
    this.rawSel = this.rawSel || {};
    const raws = this.rawSel[key] || (this.rawSel[key] = []);
    const i = list.indexOf(ref);
    if (i >= 0) { list.splice(i, 1); raws.splice(raws.indexOf(raw), 1); } else { list.push(ref); raws.push(raw); }
    this.setSlot(key, `[${list.join(", ")}]`);
    if (this.field("body") && this.field("body").hidden && list.length) {
      const m = list[0].match(/^([A-Za-z_][A-Za-z0-9_]*(\[\d+\])?)\./); if (m) this.values.body = m[1];
    }
    this.vp.setSelected(Object.values(this.rawSel).flat());
  }

  // ------------------------------------------------------------------ selection box inside a tool
  boxKind() {
    if (this.sizing || this.stage) return null;
    const f = this.field(this.active);
    if (!f) return null;
    return { body: "body", bodies: "body", edges: "edge", path: "edge", faces: "face" }[f.kind] || null;
  }

  boxPick(hits) {
    const f = this.field(this.active);
    if (!f || !hits.length) return;
    if ((f.kind === "faces" && !f.ordered) || f.kind === "edges") {   // these lists belong to one body
      const own = this.bodyIdOf(this.values.body) || (this.items[f.key] || []).map((x) => (x.match(/^([A-Za-z_][\w]*(\[\d+\])?)\./) || [])[1]).find(Boolean);
      const target = own ? (Object.entries(this.app.state?.exprs || {}).find(([, e]) => e === own) || [own])[0] : hits[0].body;
      const before = hits.length;
      hits = hits.filter((h) => h.body === target);
      if (hits.length < before) toast(`Kept the faces of ${target}; this list takes one body`);
    }
    for (const h of hits) {
      if (f.kind === "body") { this.takeBody(h.body); return; }
      if (f.kind === "bodies") { const e = this.app.exprFor(h.body, true); if (!(this.items[f.key] || []).includes(e)) this.takeBody(h.body); continue; }
      const ref = f.kind === "faces" ? this.faceRef(h) : f.kind === "path" ? this.pathRef(h) : this.app.fixRef(h.ref, true);
      if ((this.items[f.key] || []).includes(ref)) continue;
      if (f.kind === "edges") this.toggleEdge(f.key, h); else this.toggleItem(f.key, ref, h.key);
    }
  }

  // ------------------------------------------------------------------ polyline profile: click points on a plane
  beginPoints(base, plane, keep = false, first = null) {
    this.stage = "points"; this.active = "points"; this.markActive();
    this.plane = plane || this.plane || { base, snaps: [], bed: false };
    this.values.at = base.ref || ""; this.frames.at = base.frame;
    const el = $("dialog").querySelector('input[data-key="at"]'); if (el) el.value = this.values.at;
    if (!keep) this.pts = [];
    this.vp.showLocalGrid(base.frame);
    this.vp.updateLocalGrid();                    // know the face grid's step now, for the first point
    if (first) { const st = this.vp.snapStep(); this.pts.push([round3(Math.round(first[0] / st) * st), round3(Math.round(first[1] / st) * st)]); }
    this.syncPoints();
    this.app.setHint(this.hintFor("points"));
  }

  localOf(p) {
    const fr = this.frames.at, o = new THREE.Vector3(...fr.o), d = new THREE.Vector3(...p).sub(o);
    return [round3(d.dot(new THREE.Vector3(...fr.x))), round3(d.dot(new THREE.Vector3(...fr.y)))];
  }

  pointsCandidate(ev) {
    const fr = this.frames.at;
    const free = this.freeCandidate(ev, { ref: this.values.at, frame: fr });
    const snaps = (this.plane && this.plane.snaps) || [];
    const onPlane = snaps.filter((c) => Math.abs(new THREE.Vector3(...c.p).sub(new THREE.Vector3(...fr.o)).dot(new THREE.Vector3(...fr.z))) < 1e-6);
    const near = this.vp.nearestSnap(ev, onPlane);
    if (near >= 0) return { local: this.localOf(onPlane[near].p), world: onPlane[near].p, snap: true };
    return free ? { local: [round3(free.dx), round3(free.dy)], world: free.p, snap: false } : null;
  }

  nearFirst(ev) {
    if (this.pts.length < 3) return false;
    const [sx, sy] = this.vp.toScreen(this.worldOf(this.pts[0]));
    const r = this.vp.canvas.getBoundingClientRect();
    return Math.hypot(ev.clientX - r.left - sx, ev.clientY - r.top - sy) < 10;
  }

  worldOf([x, y]) { return at(this.frames.at, x, y); }

  pointsHover(ev) {
    const c = this.pointsCandidate(ev);
    this.drawPoly(c && (this.nearFirst(ev) ? this.pts[0] : c.local));
    if (!c) return true;
    const closing = this.nearFirst(ev);
    this.app.showTip(closing ? "Close the profile" : `(${fmt(c.local[0])}, ${fmt(c.local[1])})${c.snap ? " · snapped" : ""}`, ev);
    return true;
  }

  pointsClick(ev) {
    if (this.nearFirst(ev)) { this.setVal("closed", "True", false); const el = $("fld-closed"); if (el) el.value = "True"; return this.finishPoints(); }
    const c = this.pointsCandidate(ev);
    if (!c) return true;
    const last = this.pts[this.pts.length - 1];
    if (!last || Math.hypot(last[0] - c.local[0], last[1] - c.local[1]) > 1e-6) this.pts.push(c.local);
    this.syncPoints();
    return true;
  }

  finishPoints() {
    this.stage = null; this.active = null; this.markActive();
    this.vp.clearTemp();
    this.syncPoints();
    this.app.setHint("Adjust the points or other values, then OK (Enter).");
    this.focusFirst();
    return true;
  }

  syncPoints() {
    this.values.points = this.pts.length ? `[${this.pts.map(([x, y]) => `(${fmt(x)}, ${fmt(y)})`).join(", ")}]` : "";
    const el = $("dialog").querySelector('input[data-key="points"]'); if (el) el.value = this.values.points;
    this.drawPoly();
    this.schedule();
  }

  drawPoly(cursor) {
    if (!this.polyGroup) return;
    this.polyGroup.clear();
    if (!this.frames.at) return;
    const closed = this.values.closed !== "False";
    const pts = this.pts.map((p) => new THREE.Vector3(...this.worldOf(p)));
    const line = (a, opacity) => {
      const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(a), new THREE.LineBasicMaterial({ color: col("--accent"), depthTest: false, transparent: true, opacity }));
      l.renderOrder = 14; this.polyGroup.add(l);
    };
    if (pts.length > 1) line(closed && !cursor && pts.length > 2 ? [...pts, pts[0]] : pts, 1);
    if (cursor && pts.length) line([pts[pts.length - 1], new THREE.Vector3(...this.worldOf(cursor))], 0.6);
    for (const [i, p] of pts.entries()) {
      const m = new THREE.Mesh(new THREE.SphereGeometry(1, 10, 6), new THREE.MeshBasicMaterial({ color: i === 0 ? col("--sel") : col("--accent"), depthTest: false }));
      m.position.copy(p); m.userData.px = i === 0 ? 5 : 3.5; m.renderOrder = 15; this.polyGroup.add(m);
    }
    if (cursor) this.vp.marker(this.worldOf(cursor), "--sel", 1.2);
  }

  takeBody(id) {
    const f = this.field(this.active);
    if (f && this.isProfile(id) && (f.kind === "face" || f.kind === "faces" || f.kind === "path")) {
      const e = this.app.exprFor(id, true);
      if (f.kind === "faces") this.toggleItem(f.key, e, "body:" + id);
      else { if (f.kind === "path") this.items[f.key] = []; this.setSlot(f.key, e); this.advance(f.key); }
      return true;
    }
    if (!f || (f.kind !== "body" && f.kind !== "bodies")) return false;
    const e = this.app.exprFor(id);
    if (f.kind === "body") { this.setSlot(f.key, e); this.advance(f.key); if (this.gizmo) this.placeGizmo(); return true; }
    const list = this.items[f.key] || [];
    const i = list.indexOf(e);
    if (i >= 0) list.splice(i, 1); else list.push(e);
    this.setSlot(f.key, `[${list.join(", ")}]`);
    return true;
  }

  takeGroup(name) {
    const f = this.field(this.active);
    if (!f || f.kind !== "bodies") return false;
    const list = this.items[f.key] || [];
    if (!list.includes(name)) list.push(name);
    this.setSlot(f.key, `[${list.join(", ")}]`);
    return true;
  }

  allEdges(key) {
    const id = this.edgeBody || this.bodyIdOf(this.values.body);
    if (!id) { toast("Click one edge of the body first, then All"); return; }
    const e = this.app.exprFor(id);
    this.edgeRaw = [];
    if (this.field("body")) this.values.body = e;
    this.setSlot(key, `[${e}.edges()]`);
  }

  bodyIdOf(expr) {
    if (!expr) return null;
    const ex = this.app.state?.exprs || {};
    return Object.keys(ex).find((k) => ex[k] === expr) || expr;
  }

  advance(key) {
    const fields = (this.tool.fields || []).filter((f) => SLOT_KINDS.has(f.kind) && !f.hidden && !f.noAuto && !f.planes);
    const i = fields.findIndex((f) => f.key === key);
    const next = fields.slice(i + 1).find((f) => !this.text(f.key));
    if (next) return this.activate(next.key);
    this.activate(null);
    if (this.tool.size && !this.edit && key === "at") this.startSizing();
    else this.focusFirst();
  }

  // ------------------------------------------------------------------ primitives: size by clicking, ghost, arrows
  baseFrame() {
    if (this.frames.at) return this.frames.at;
    if (!this.values.at) return this.stage ? null : WORLD_FRAME;
    return null;
  }

  ghostValues(extra = {}) {
    const v = {};
    for (const f of this.tool.fields || []) {
      if (f.kind === "num") v[f.key] = this.num(f.key, NaN);
      if (f.kind === "choice") v[f.key] = this.values[f.key];
    }
    v.spin = this.num("spin", 0);
    return { ...v, ...extra };
  }

  drawGhost(extra) {
    if (!this.ghost) return;
    const base = this.stage === "plane" || this.stage === "start" ? null : (this.sizing ? this.sizing.frame : this.baseFrame());
    this.ghost.update(this.id, this.ghostValues(extra), base, !this.sizing);
  }

  onArrow(key, value) {
    this.setVal(key, fmt(value), false);
    this.drawGhost();
    this.app.showTip(`${this.field(key)?.label || key} ${fmt(value)}`);
    this.scheduleStatement();
  }

  startSizing() {
    this.sizing = { frame: this.frames.at || (this.values.at ? null : WORLD_FRAME) };
    if (!this.sizing.frame) { this.sizing = null; this.focusFirst(); return; }
    this.app.setHint(this.tool.size.kind === "rect" ? "Click the opposite corner, or type sizes." : "Click a point on the rim: it sets the radius and angle 0. Or type values.");
  }

  sizeGeom(ev) {
    const p = this.vp.planePoint(ev, this.sizing.frame);
    if (!p) return null;
    const s = this.vp.snapStep();
    return { ...p, x: Math.round(p.x / s) * s, y: Math.round(p.y / s) * s };
  }

  sizeValues(g) {
    const sz = this.tool.size;
    if (sz.kind === "radial") {
      const r = Math.hypot(g.x, g.y);
      if (r < 1e-6) return null;
      const ang = Math.atan2(g.y, g.x) * 180 / Math.PI;
      return { [sz.r]: r, spin: Math.abs(ang) < 0.05 ? 0 : ang };
    }
    if (Math.abs(g.x) < 1e-6 || Math.abs(g.y) < 1e-6) return null;
    // keep dimensions positive: turn the frame so the box grows toward the click
    const [L, W, spin] = g.x > 0 && g.y > 0 ? [g.x, g.y, 0] : g.x < 0 && g.y > 0 ? [g.y, -g.x, 90]
      : g.x < 0 && g.y < 0 ? [-g.x, -g.y, 180] : [-g.y, g.x, 270];
    return { [sz.x]: L, [sz.y]: W, spin };
  }

  sizeHover(ev) {
    const g = this.sizeGeom(ev);
    this.vp.clearTemp();
    if (!g) return true;
    const vals = this.sizeValues(g);
    this.vp.marker(g.world, "--sel", 1.2);
    if (vals) {
      // the dialog's numbers follow the cursor, so they always match the ghost
      for (const [k, v] of Object.entries(vals)) this.setVal(k, k === "spin" && !v ? "" : fmt(v), false);
      this.scheduleStatement();
      this.drawGhost(vals);
      const sz = this.tool.size;
      this.app.showTip(sz.kind === "radial" ? `r = ${fmt(vals[sz.r])}, angle 0 at ${fmt(vals.spin)}°` : `${fmt(vals[sz.x])} × ${fmt(vals[sz.y])}`, ev);
    }
    return true;
  }

  sizeClick(ev) {
    const g = this.sizeGeom(ev);
    if (!g) return true;
    const vals = this.sizeValues(g);
    if (!vals) return true;
    for (const [k, v] of Object.entries(vals)) this.setVal(k, k === "spin" && !v ? "" : fmt(v), false);
    this.frames.at = this.sizing.frame;
    this.sizing = null; this.vp.clearTemp();
    this.app.setHint("Drag the arrows or type values (Tab moves between them), then OK (Enter).");
    this.schedule();
    this.focusFirst();
    return true;
  }

  // ------------------------------------------------------------------ move gizmo
  setupGizmo() {
    this.gizmo = new Gizmo(this.vp, {
      onStart: () => this.gizmoStart(),
      onDrag: (kind, axis, v) => this.gizmoDrag(kind, axis, v),
      onEnd: () => { this.dragging = false; this.schedule(); },
    });
    this.baseScene = this.app.state.scene;
    if (this.edit) {   // the body as it was before this move
      const zero = { ...this.args(), dx: "0", dy: "0", dz: "0", rx: "0", ry: "0", rz: "0" };
      post("/api/preview", { op: this.op, args: zero, edit: this.edit }).then((pv) => { this.baseScene = pv.scene; this.vp.setScene(pv.scene); this.schedule(); }).catch(() => {});
    }
    this.placeGizmo();
  }

  moveGeometry() {
    const id = this.bodyIdOf(this.values.body);
    const bf = id && this.baseScene ? this.vp.bodyFrame(id, this.baseScene) : null;
    if (!bf) return null;
    let axes = this.values.frame === "world" ? WORLD_FRAME : bf;
    if (this.values.along && this.frames.along && this.values.frame !== "world") axes = this.frames.along;
    const pivot = this.values.pivot && this.frames.pivot ? this.frames.pivot.o : bf.o;
    const X = new THREE.Vector3(...axes.x), Y = new THREE.Vector3(...axes.y), Z = new THREE.Vector3(...axes.z);
    const c = new THREE.Vector3(...pivot);
    const m = new THREE.Matrix4();
    for (const [k, ax] of [["rx", X], ["ry", Y], ["rz", Z]]) {
      const a = this.num(k);
      if (!a) continue;
      const r = new THREE.Matrix4().makeTranslation(c.x, c.y, c.z)
        .multiply(new THREE.Matrix4().makeRotationAxis(ax.clone().normalize(), a * Math.PI / 180))
        .multiply(new THREE.Matrix4().makeTranslation(-c.x, -c.y, -c.z));
      m.premultiply(r);
    }
    const v = X.clone().multiplyScalar(this.num("dx")).add(Y.clone().multiplyScalar(this.num("dy"))).add(Z.clone().multiplyScalar(this.num("dz")));
    m.premultiply(new THREE.Matrix4().makeTranslation(v.x, v.y, v.z));
    return { id, frame: { ...axes, o: c.clone().add(v).toArray() }, m };
  }

  placeGizmo() {
    if (!this.gizmo) return;
    const g = this.moveGeometry();
    if (!g) { this.gizmo.hide(); return; }
    this.gizmo.show(g.frame);
    if (!this.live) {   // show the move on screen without building it
      if (this.vp.data !== this.baseScene) this.vp.setScene(this.baseScene);
      this.vp.setBodyTransform(g.id, g.m);
    }
  }

  gizmoStart() {
    this.dragging = true;
    this.dragStart = Object.fromEntries(["dx", "dy", "dz", "rx", "ry", "rz"].map((k) => [k, this.num(k)]));
    if (this.baseScene && this.vp.data !== this.baseScene) this.vp.setScene(this.baseScene);
    const g = this.moveGeometry(); if (g) this.vp.setBodyTransform(g.id, g.m);
  }

  gizmoDrag(kind, axis, v) {
    const key = (kind === "move" ? "d" : "r") + axis;
    this.setVal(key, fmt(this.dragStart[key] + v), false);
    const g = this.moveGeometry();
    if (!g) return;
    this.vp.setBodyTransform(g.id, g.m);
    this.gizmo.moveTo(g.frame.o);
    this.app.showTip(kind === "move" ? `${axis.toUpperCase()} ${fmt(this.dragStart[key] + v)} mm` : `${axis.toUpperCase()} ${fmt(this.dragStart[key] + v)}°`);
  }

  // ------------------------------------------------------------------ measure
  measureClick() {
    if (!this.cands) return true;
    const c = this.cands[this.candIdx];
    this.mpts = (this.mpts && this.mpts.length < 2) ? this.mpts : [];
    this.mpts.push(c);
    this.vp.clearTemp();
    this.mpts.forEach((m) => this.vp.marker(m.p));
    const out = $("measure-out");
    if (this.mpts.length === 1) { out.textContent = `From ${c.ref}. Pick the second point.`; return true; }
    const [a, b] = this.mpts;
    this.vp.line(a.p, b.p, "--accent");
    const d = [0, 1, 2].map((i) => b.p[i] - a.p[i]);
    this.dist = Math.hypot(...d);
    out.innerHTML = `<b>${fmt(this.dist)} mm</b><br>X ${fmt(d[0])} · Y ${fmt(d[1])} · Z ${fmt(d[2])}<br><small>${esc(a.ref)}<br>→ ${esc(b.ref)}</small>`;
    $("dlg-param").disabled = false;
    return true;
  }

  async makeParam() {
    const name = `dist${Object.keys(this.app.state.desc.params || {}).length + 1}`;
    try { await this.app.action("/api/param", { name, value: `${fmt(this.dist)} * mm` }); toast(`Added parameter ${name}; rename it in the panel`); }
    catch (e) { toast(e.message, true); }
  }

  // ------------------------------------------------------------------ statement, optional live result, OK
  schedule() {
    if (this.id === "measure") return;
    if (this.gizmo && !this.dragging) this.placeGizmo();
    if (this.ghost && !this.sizing) this.drawGhost();
    this.scheduleStatement();
  }

  scheduleStatement() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => (this.tool.inspect ? this.inspect() : this.live ? this.livePreview() : this.statement()), this.live ? 160 : 90);
  }

  setResult(cls, text) { const r = $("dlg-result"); if (r) { r.className = "dlg-result " + cls; r.textContent = text; } }

  async statement() {
    if (this.app.dialog !== this) return;
    const seq = ++this.seq;
    const args = this.args(), miss = this.missing(args);
    const stmtEl = $("dlg-stmt");
    $("dlg-ok").disabled = miss.length > 0;
    if (miss.length) {
      stmtEl.textContent = `${this.edit || "…"} = ${this.op}(…)`;
      this.setResult("", `Needs: ${miss.map((f) => f.label.toLowerCase()).join(", ")}`);
      return;
    }
    try {
      const st = await post("/api/statement", { op: this.op, args, edit: this.edit });
      if (seq !== this.seq || this.app.dialog !== this) return;
      stmtEl.textContent = st.statement;
      this.setResult("", "Runs when you press OK");
      this.app.previewTimeline(this.edit ? null : st.name, null);
    } catch (e) {
      if (seq !== this.seq || this.app.dialog !== this) return;
      stmtEl.textContent = ""; this.setResult("error", e.message);
    }
  }

  // Patterns: run the step without writing it; draw the copies and directions; show errors before OK.
  async inspect() {
    if (this.app.dialog !== this) return;
    const seq = ++this.seq;
    const args = this.args(), miss = this.missing(args);
    const stmtEl = $("dlg-stmt");
    if (miss.length) {
      $("dlg-ok").disabled = true; this.vp.clearPattern();
      stmtEl.textContent = `${this.edit || "…"} = ${this.op}(…)`;
      this.setResult("", `Needs: ${miss.map((f) => f.label.toLowerCase()).join(", ")}`);
      return;
    }
    try {
      const out = await post("/api/inspect", { op: this.op, args, edit: this.edit });
      if (seq !== this.seq || this.app.dialog !== this) return;
      stmtEl.textContent = out.statement;
      const r = out.result;
      const bad = out.error || !r || r.status === "error" || r.status === "skipped";
      $("dlg-ok").disabled = !!bad;
      if (bad) { this.vp.clearPattern(); this.setResult("error", out.error || (r ? r.message : "Not built (after the timeline marker)")); return; }
      this.vp.showPattern(out.info, out.copies);
      const n = out.copies.length;
      this.setResult(r.status, `${n} ${n === 1 ? "copy" : "copies"}${r.warnings.length ? " · " + r.warnings.join("; ") : ""} · runs when you press OK`);
      this.app.previewTimeline(this.edit ? null : out.name, null);
    } catch (e) {
      if (seq !== this.seq || this.app.dialog !== this) return;
      stmtEl.textContent = ""; this.vp.clearPattern(); this.setResult("error", e.message); $("dlg-ok").disabled = true;
    }
  }

  async livePreview() {
    if (this.app.dialog !== this) return;
    const seq = ++this.seq;
    const args = this.args(), miss = this.missing(args);
    const stmtEl = $("dlg-stmt");
    if (miss.length) { $("dlg-ok").disabled = true; stmtEl.textContent = `${this.edit || "…"} = ${this.op}(…)`; this.setResult("", `Needs: ${miss.map((f) => f.label.toLowerCase()).join(", ")}`); return; }
    try {
      const pv = await post("/api/preview", { op: this.op, args, edit: this.edit });
      if (seq !== this.seq || this.app.dialog !== this) return;
      stmtEl.textContent = pv.statement || "";
      const r = pv.result;
      if (pv.script_error) this.setResult("error", pv.script_error);
      else if (!r) this.setResult("", "Not built (after the timeline marker)");
      else this.setResult(r.status, r.status === "ok" ? `OK · ${r.ms.toFixed(1)} ms` : (r.message || r.warnings.join("; ") || r.status));
      $("dlg-ok").disabled = !!pv.script_error || (r && (r.status === "error" || r.status === "skipped"));
      if (this.dragging) return;
      const touched = pv.scene.bodies.filter((b) => b.history.includes(pv.name)).map((b) => b.id);
      this.vp.setScene(pv.scene, { focus: touched });
      if (this.gizmo) this.gizmo.show(this.moveGeometry()?.frame || WORLD_FRAME);
      this.app.previewTimeline(pv.name, r);
      this.app.setPerf(pv.ms);
    } catch (e) {
      if (seq !== this.seq || this.app.dialog !== this) return;
      stmtEl.textContent = ""; this.setResult("error", e.message); $("dlg-ok").disabled = true;
    }
  }

  restoreScene() { if (this.app.state) this.vp.setScene(this.gizmo ? this.baseScene : this.app.state.scene); }

  async ok() {
    if (this.id === "measure") return this.app.closeDialog();
    if (this.stage === "points") this.finishPoints();
    const args = this.args(), miss = this.missing(args);
    if (miss.length) { this.setResult("error", `Needs: ${miss.map((f) => f.label.toLowerCase()).join(", ")}`); return; }
    clearTimeout(this.timer);
    const okBtn = $("dlg-ok"); okBtn.disabled = true;
    this.setResult("", "Building…");
    // long steps (a shell of a lofted part can take a while): show that it is still working
    const t0 = Date.now();
    const tick = setInterval(() => {
      if (this.app.dialog !== this) return clearInterval(tick);
      const s = Math.round((Date.now() - t0) / 1000);
      if (s >= 2) this.setResult("", `Building… ${s} s`);
    }, 1000);
    try {
      const out = await post("/api/apply", { op: this.op, args, edit: this.edit });
      clearInterval(tick);
      this.app.closeDialog(true);
      this.app.setState(out.state);
      toast(this.edit ? `Updated ${this.edit}` : `Added ${out.name}`);
    } catch (e) {
      clearInterval(tick);
      if (this.app.dialog !== this) return;
      okBtn.disabled = false;
      this.setResult("error", `Not added: ${e.message}`);
    }
  }

  close() {
    clearTimeout(this.timer);
    Object.values(this.resolveTimers).forEach(clearTimeout);
    if (this.gizmo) { this.gizmo.dispose(); this.gizmo = null; }
    if (this.ghost) { this.ghost.dispose(); this.ghost = null; }
    this.vp.clearPattern();
    if (this.polyGroup) { this.vp.scene.remove(this.polyGroup); this.polyGroup = null; }
    if (this.hiddenCopies) { this.hiddenCopies.forEach((id) => this.vp.hidden.delete(id)); this.hiddenCopies = null; }
    this.vp.showSnaps(null); this.vp.clearTemp(); this.vp.showLocalGrid(null); this.vp.setSelected([]);
    this.vp.setOriginForced(false); this.vp.setOriginHover(null);
    $("dialog").hidden = true; $("dialog").innerHTML = "";
  }
}

export function fmt(v) { return String(Math.round(v * 1000) / 1000); }
const round3 = (v) => Math.round(v * 1000) / 1000;
const col = (n) => new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue(n).trim() || "#888");

// "[(0, 0), (10, 0), (10, 5)]" -> [[0, 0], [10, 0], [10, 5]] (numbers only; anything else is ignored)
export function parsePoints(text) {
  const out = [];
  const re = /\(\s*(-?[\d.]+(?:e-?\d+)?)\s*,\s*(-?[\d.]+(?:e-?\d+)?)\s*\)/g;
  let m;
  while ((m = re.exec(String(text || "")))) out.push([parseFloat(m[1]), parseFloat(m[2])]);
  return out;
}
