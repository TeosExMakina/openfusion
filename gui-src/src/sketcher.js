// Sketch mode: draw lines, rectangles, circles, arcs and points on a plane, constrain them,
// dimension them, and finish with one sketch(...) statement.
//
// The engine does all the solving: every change runs the real sketch step (without writing
// it) and the solved points come back, so what you see is what the script will build. The
// model here holds the script's own text for anything that isn't a plain number (anchors,
// projected edges, P.width), so finishing writes back exactly what was there plus your edits.
import { $, esc, post, toast } from "./api.js";
import * as THREE from "three";

const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || "#888";
const col = (n) => new THREE.Color(css(n));
const fmt = (v) => { const r = Math.round(v * 1e4) / 1e4; return Object.is(r, -0) ? "0" : String(r); };
const isExpr = (v) => v && typeof v === "object" && "expr" in v;
const text = (v) => isExpr(v) ? v.expr : typeof v === "string" ? JSON.stringify(v) : fmt(v);
const PICK_PT = 10, PICK_CURVE = 7;

// Which constraint needs what, for the buttons (and the checks before sending).
const RULES = [
  { id: "coincident", label: "Coincident", key: "I", glyph: "●", help: "two points, or a point and a curve (on it)" },
  { id: "horizontal", label: "Horizontal", key: "H", glyph: "H", help: "lines, or two points" },
  { id: "vertical", label: "Vertical", key: "V", glyph: "V", help: "lines, or two points" },
  { id: "parallel", label: "Parallel", key: "P", glyph: "∥", help: "two lines" },
  { id: "perpendicular", label: "Perpendicular", key: "Shift+P", glyph: "⊥", help: "two lines" },
  { id: "collinear", label: "Collinear", key: "Shift+L", glyph: "⋯", help: "two lines" },
  { id: "tangent", label: "Tangent", key: "T", glyph: "T", help: "a line and an arc/circle, or two arcs/circles" },
  { id: "equal", label: "Equal", key: "E", glyph: "=", help: "two lines, or two arcs/circles" },
  { id: "concentric", label: "Concentric", key: "Shift+C", glyph: "◎", help: "two arcs/circles" },
  { id: "midpoint", label: "Midpoint", key: "M", glyph: "M", help: "a point and a line" },
  { id: "symmetric", label: "Symmetric", key: "Shift+Y", glyph: "⇋", help: "two points and a line (the mirror)" },
  { id: "fix", label: "Fix", key: "F", glyph: "🔒", help: "points or curves" },
];
const DRAW = [
  { id: "select", label: "Select", key: "S", help: "Click to select (Shift adds). Drag a point to move it. Delete removes." },
  { id: "line", label: "Line", key: "L", help: "Click points; each click starts the next line. Esc or a click on the last point ends the chain." },
  { id: "rect", label: "Rectangle", key: "R", help: "Click one corner, then the opposite corner." },
  { id: "circle", label: "Circle", key: "C", help: "Click the centre, then a point on the rim." },
  { id: "arc", label: "Arc", key: "A", help: "Click the centre, the start, then the end (counterclockwise)." },
  { id: "ellipse", label: "Ellipse", key: "Shift+E", help: "Click the centre, then a corner of the box around the ellipse (its half-widths along X and Y)." },
  { id: "point", label: "Point", key: "O", help: "Click to place a free point (a centre, a guide)." },
  { id: "project", label: "Project", key: "J", help: "Click edges of the model to bring them into the sketch: fixed, and they follow the model when it changes. Their ends are points you can constrain to." },
  { id: "dim", label: "Dimension", key: "D", help: "Select what to measure first (a line, a circle, two points, a point and a line, two lines), then D." },
];

// The type-in boxes shown while drawing (Tab moves between them, Enter places the shape).
// A typed value is kept (locked) and becomes a dimension; the others follow the cursor.
const HUD = {
  line: [["length", "Length"], ["angle", "Angle °"]],
  rect: [["width", "Width"], ["height", "Height"]],
  circle: [["diameter", "Diameter"]],
  ellipse: [["rx", "Half-width X"], ["ry", "Half-width Y"]],
  arc: [["radius", "Radius"], ["sweep", "Sweep °"]],
};
const num = (t) => { const v = String(t).trim(); return /^-?(\d+\.?\d*|\.\d+)$/.test(v) ? Number(v) : v ? { expr: v } : null; };
const val = (v) => (typeof v === "number" ? v : null);           // a plain number to draw with (expressions: use the cursor)

export class Sketcher {
  constructor(app, opts = {}) {
    this.app = app; this.vp = app.vp;
    this.edit = opts.edit || null;
    this.model = { points: {}, curves: {}, cons: [] };
    this.solved = null; this.info = null;
    this.tool = "line"; this.construction = false;
    this.sel = []; this.pending = []; this.hoverKey = null; this.cursor = null;
    this.hud = null; this.tab = "curves"; this.showContents = true;
    this.history = []; this.future = [];
    this.group = new THREE.Group(); this.vp.temp.add(this.group);
    this.labels = document.createElement("div"); this.labels.className = "sk-labels";
    this.vp.canvas.parentElement.appendChild(this.labels);
    this.onFrame = () => this.placeLabels(); this.vp.onFrame.push(this.onFrame);
    this.panel = $("dialog"); this.panel.hidden = false; this.panel.classList.add("sk-panel");
    this.down = (ev) => this.pointerDown(ev); this.up = (ev) => this.pointerUp(ev);
    this.vp.canvas.addEventListener("pointerdown", this.down, true);
    window.addEventListener("pointerup", this.up, true);
    this.renderPanel();
    this.start(opts);
  }

  // ------------------------------------------------------------------ the dialog interface the app uses
  boxKind() { return null; }
  boxPick() {}
  takeBody() { return false; }
  takeGroup() { return false; }

  async start(opts) {
    if (this.edit) {
      try {
        const src = await post("/api/sketch_source", { name: this.edit });
        this.at = src.at; this.spin = src.spin;
        for (const [n, v] of Object.entries(src.points)) this.model.points[n] = Array.isArray(v) ? v.slice() : v;
        for (const [n, v] of Object.entries(src.curves)) this.model.curves[n] = v.slice();
      for (const v of src.constraints) if (!Array.isArray(v)) throw new Error("each constraint is written (kind, ...), e.g. (\"horizontal\", \"l1\")");
        this.model.cons = src.constraints.map((c) => c.slice());
      } catch (e) { toast(e.message, true); this.app.closeDialog(); return; }
      await this.solve();
      if (this.info) this.enterPlane(this.info.frame);
      this.setTool("select");
      return;
    }
    if (opts.plane) { this.beginOn(opts.plane.base); return; }
    this.stage = "plane";
    this.vp.setOriginForced(true);
    this.app.setHint("Click a flat face or an origin plane (XY, XZ, YZ) to sketch on; empty space is the bed. Esc cancels.");
  }

  beginOn(base) {
    this.vp.setOriginForced(false); this.vp.setOriginHover(null); this.vp.setHover(null);
    this.at = base.ref || null;           // nothing: the world XY plane (the bed)
    this.stage = null;
    this.enterPlane(base.frame);
    this.solve();
    this.setTool("line");
  }

  enterPlane(frame) {
    this.frame = frame;
    this.vp.showLocalGrid(frame);
    this.vp.updateLocalGrid();
    // look straight at the sketch
    this.vp.lookAt(frame);
    this.render();
  }

  // ------------------------------------------------------------------ names and coordinates
  newName(prefix, taken) {
    let k = 1;
    while (taken[prefix + k] !== undefined || this.model.points[prefix + k] !== undefined || this.model.curves[prefix + k] !== undefined) k++;
    return prefix + k;
  }
  pos(name) {                 // solved position of a point, else the model's own
    const s = this.solved && this.solved.points[name];
    if (s) return s;
    const m = this.model.points[name];
    return Array.isArray(m) ? m : [0, 0];
  }
  w(p) { const f = this.frame; return [0, 1, 2].map((i) => f.o[i] + p[0] * f.x[i] + p[1] * f.y[i]); }
  screen(p) { return this.vp.toScreen(this.w(p)); }
  local(ev) { const h = this.vp.planePoint(ev, this.frame); return h ? [h.x, h.y] : null; }
  evXY(ev) { const r = this.vp.canvas.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; }

  // the point under the cursor (within PICK_PT px), else the cursor rounded to the grid
  snapped(ev, exclude = null) {
    const [mx, my] = this.evXY(ev);
    let best = null, bd = PICK_PT;
    for (const n of Object.keys(this.allPoints())) {
      if (n === exclude) continue;
      const [sx, sy] = this.screen(this.pos(n));
      const d = Math.hypot(sx - mx, sy - my);
      if (d < bd) { bd = d; best = n; }
    }
    if (best) return { name: best, p: this.pos(best).slice() };
    const p = this.local(ev);
    if (!p) return null;
    const st = this.vp.snapStep();
    return { name: null, p: [Math.round(p[0] / st) * st, Math.round(p[1] / st) * st] };
  }
  allPoints() { return { ...this.model.points, ...((this.solved && this.solved.points) || {}) }; }

  // ------------------------------------------------------------------ picking sketch geometry
  curveScreenPts(name) {
    const c = this.curveGeom(name);
    return c ? c.map((p) => this.screen(p)) : [];
  }
  curveGeom(name) {             // the curve as 2D points (solved)
    const info = this.solved && this.solved.curves && this.solved.curves[name];
    const c = this.model.curves[name] || [];
    const kind = info ? info.kind : c[0];
    const pts = info ? info.pts : c.slice(1).filter((v) => typeof v === "string" && v !== "construction" && v !== "closed");
    if (kind === "line" && pts.length >= 2) return [this.pos(pts[0]), this.pos(pts[1])];
    if (kind === "circle") {
      const ctr = info && info.centre ? info.centre : this.pos(pts[0]);
      const r = info && info.radius ? info.radius : (typeof c[2] === "number" ? c[2] : 1);
      return Array.from({ length: 65 }, (_, i) => { const a = i / 64 * 2 * Math.PI; return [ctr[0] + r * Math.cos(a), ctr[1] + r * Math.sin(a)]; });
    }
    if (kind === "arc") {
      const ctr = info && info.centre ? info.centre : this.pos(pts[0]);
      const [s, e] = info && info.centre ? [this.pos(pts[0]), this.pos(pts[1])] : [this.pos(pts[1]), this.pos(pts[2])];
      const r = Math.hypot(s[0] - ctr[0], s[1] - ctr[1]);
      let a0 = Math.atan2(s[1] - ctr[1], s[0] - ctr[0]), a1 = Math.atan2(e[1] - ctr[1], e[0] - ctr[0]);
      while (a1 <= a0) a1 += 2 * Math.PI;
      return Array.from({ length: 49 }, (_, i) => { const a = a0 + (a1 - a0) * i / 48; return [ctr[0] + r * Math.cos(a), ctr[1] + r * Math.sin(a)]; });
    }
    if (info && info.samples) return info.samples;          // ellipses, parametric curves, splines, projections
    if (kind === "ellipse") {                                // not solved yet: from the model
      const ctr = this.pos(c[1]), rx = Number(c[2]) || 1, ry = Number(c[3]) || 1, a = (Number(c[4]) || 0) * Math.PI / 180;
      return Array.from({ length: 97 }, (_, i) => { const t = i / 96 * 2 * Math.PI, x = rx * Math.cos(t), y = ry * Math.sin(t);
        return [ctr[0] + x * Math.cos(a) - y * Math.sin(a), ctr[1] + x * Math.sin(a) + y * Math.cos(a)]; });
    }
    if (kind === "spline") return pts.map((n) => this.pos(n));
    return null;
  }

  pickAt(ev) {
    const [mx, my] = this.evXY(ev);
    let best = null, bd = PICK_PT;
    for (const n of Object.keys(this.allPoints())) {
      const [sx, sy] = this.screen(this.pos(n));
      const d = Math.hypot(sx - mx, sy - my);
      if (d < bd) { bd = d; best = "p:" + n; }
    }
    if (best) return best;
    bd = PICK_CURVE;
    for (const n of Object.keys(this.model.curves)) {
      const sp = this.curveScreenPts(n);
      for (let i = 0; i + 1 < sp.length; i++) {
        const d = segDist([mx, my], sp[i], sp[i + 1]);
        if (d < bd) { bd = d; best = "c:" + n; }
      }
    }
    return best;
  }

  // ------------------------------------------------------------------ pointer handling
  hover(ev) {
    if (this.stage === "plane") {
      const org = this.vp.pickOrigin(ev);
      this.vp.setOriginHover(org ? org.plane : null);
      const hit = org ? null : this.vp.pick(ev, "face");
      this.vp.setHover(hit && hit.face && hit.face.kind === "plane" ? "face:" + hit.ref : null);
      this.app.showTip(org ? `Sketch on the ${org.plane} plane` : hit ? (hit.face && hit.face.kind === "plane" ? "Sketch on this face" : "Pick a flat face") : "Sketch on the bed (XY)", ev);
      return true;
    }
    if (!this.frame) return true;
    if (this.drag) { this.dragTo(ev); return true; }
    if (this.tool === "project") {
      const hit = this.vp.pick(ev, "edge");
      this.vp.setHover(hit ? "edge:" + hit.ref : null);
      this.app.showTip(hit ? `Project ${this.app.fixRef(hit.ref, true)}` : "Click an edge of the model", ev);
      return true;
    }
    this.cursor = this.snapped(ev);
    this.hoverKey = this.tool === "select" || this.tool === "dim" ? this.pickAt(ev) : null;
    this.updateHud();
    const p = this.cursor && this.cursor.p;
    this.app.showTip(p ? `(${fmt(p[0])}, ${fmt(p[1])})${this.cursor.name ? " · " + this.cursor.name : ""}` : null, ev);
    this.render();
    return true;
  }

  click(ev) {
    if (this.stage === "plane") {
      const org = this.vp.pickOrigin(ev);
      if (org) {
        this.vp.setOriginForced(false); this.vp.setOriginHover(null); this.vp.setHover(null);
        this.beginOn({ ref: org.plane === "XY" ? null : org.ref, frame: org.frame });
        return;
      }
      const hit = this.vp.pick(ev, "face");
      if (hit && hit.face && hit.face.kind !== "plane") { toast("Sketches go on flat faces", true); return; }
      if (hit) {
        const snaps = this.vp.snapCandidates({ face: hit.face, body: hit.body, label: hit.label, point: hit.point });
        const c = snaps.find((s) => s.kind === "center") || snaps[0];
        this.beginOn({ ref: this.app.fixRef(c.ref, true), frame: c.frame });
      } else {
        this.beginOn({ ref: null, frame: { o: [0, 0, 0], x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] } });
      }
      return;
    }
    if (!this.frame) return;
    if (this.tool === "project") {
      const hit = this.vp.pick(ev, "edge");
      if (!hit) return;
      const ref = this.app.fixRef(hit.ref, true);
      if (Object.values(this.model.curves).some((c) => c[0] === "project" && isExpr(c[1]) && c[1].expr === ref)) { toast("Already projected"); return; }
      this.remember();
      this.addCurve("e", ["project", { expr: ref }]);
      this.solve().then((ok) => { if (!ok) this.undo(true); });
      return;
    }
    const s = this.snapped(ev);
    if (this.tool === "select" || this.tool === "dim") {
      const k = this.pickAt(ev);
      if (!k) { if (!ev.shiftKey) this.sel = []; }
      else if (ev.shiftKey || ev.metaKey || this.tool === "dim") this.sel = this.sel.includes(k) ? this.sel.filter((x) => x !== k) : [...this.sel, k];
      else this.sel = [k];
      this.render(); this.refreshSide();
      if (this.tool === "dim" && this.sel.length) this.tryDimension(false);
      return;
    }
    if (!s) return;
    if (this.pending.length && this.hudLocked()) this.place({ name: null, p: this.hudTarget(s.p) });
    else this.place(s);
  }

  pointerDown(ev) {
    if (ev.button !== 0 || this.tool !== "select" || !this.frame) return;
    const k = this.pickAt(ev);
    if (!k || !k.startsWith("p:")) return;
    const name = k.slice(2);
    if (!Array.isArray(this.model.points[name])) return;          // anchors and projected ends don't move
    this.drag = { name, start: this.evXY(ev) }; this.dragged = false;
    ev.preventDefault(); ev.stopImmediatePropagation();
  }
  dragTo(ev) {
    const [x, y] = this.evXY(ev);
    if (!this.dragged && Math.hypot(x - this.drag.start[0], y - this.drag.start[1]) < 4) return;
    this.dragged = true;
    const p = this.local(ev);
    if (!p) return;
    this.drag.to = p;
    if (!this.dragBusy) {
      this.dragBusy = true;
      const n = this.drag.name;
      const extra = [["x", n, p[0]], ["y", n, p[1]]];
      this.solve(extra, true).finally(() => { this.dragBusy = false; });
    }
  }
  pointerUp() {
    if (!this.drag) return;
    this.drag = null;
    if (this.dragged && this.solved) { this.remember(); this.adoptSolved(); this.solve(); }
    this.dragged = false;
  }

  // ------------------------------------------------------------------ drawing
  pointFor(s) {                  // reuse a snapped point, or add one
    if (s.name) return s.name;
    const n = this.newName("p", this.model.points);
    this.model.points[n] = s.p.slice();
    return n;
  }
  addCurve(prefix, def) {
    const n = this.newName(prefix, this.model.curves);
    if (this.construction) def.push("construction");
    this.model.curves[n] = def;
    return n;
  }

  // the locked values in the type-in boxes, as dimension values (numbers or {expr})
  locks() {
    const out = {};
    for (const f of (this.hud && this.hud.fields) || []) if (f.locked && f.value !== null) out[f.key] = f.value;
    return out;
  }

  place(s) {
    const t = this.tool, L = this.locks();
    const done = () => this.resetHud();
    if (t === "point") { this.remember(); this.pointFor(s); this.solve(); return; }
    if (t === "line") {
      if (!this.pending.length) { this.pending = [s]; this.render(); this.showHud(); return; }
      const a = this.pending[0];
      if (s.name && s.name === a.name) { this.pending = []; this.render(); return; }      // click the last point again: end
      if (Math.hypot(s.p[0] - a.p[0], s.p[1] - a.p[1]) < 1e-9) return;
      this.remember();
      const pa = this.pointFor(a), pb = this.pointFor(s);
      const ln = this.addCurve("l", ["line", pa, pb]);
      const ang = Math.atan2(Math.abs(s.p[1] - a.p[1]), Math.abs(s.p[0] - a.p[0])) * 180 / Math.PI;
      if (ang < 2.5) this.model.cons.push(["horizontal", ln]);
      else if (ang > 87.5) this.model.cons.push(["vertical", ln]);
      if (L.length !== undefined) this.model.cons.push(["length", ln, L.length]);
      this.pending = [{ name: pb, p: s.p }];
      done(); this.solve();
      return;
    }
    if (t === "rect") {
      if (!this.pending.length) { this.pending = [s]; this.render(); this.showHud(); return; }
      const a = this.pending[0]; this.pending = [];
      if (Math.abs(s.p[0] - a.p[0]) < 1e-9 || Math.abs(s.p[1] - a.p[1]) < 1e-9) return;
      this.remember();
      const p1 = this.pointFor(a), p3 = this.pointFor(s);
      const p2 = this.pointFor({ p: [s.p[0], a.p[1]] }), p4 = this.pointFor({ p: [a.p[0], s.p[1]] });
      const l1 = this.addCurve("l", ["line", p1, p2]), l2 = this.addCurve("l", ["line", p2, p3]);
      const l3 = this.addCurve("l", ["line", p3, p4]), l4 = this.addCurve("l", ["line", p4, p1]);
      this.model.cons.push(["horizontal", l1], ["vertical", l2], ["horizontal", l3], ["vertical", l4]);
      if (L.width !== undefined) this.model.cons.push(["length", l1, L.width]);
      if (L.height !== undefined) this.model.cons.push(["length", l2, L.height]);
      done(); this.solve();
      return;
    }
    if (t === "circle") {
      if (!this.pending.length) { this.pending = [s]; this.render(); this.showHud(); return; }
      const c = this.pending[0]; this.pending = [];
      const r = Math.hypot(s.p[0] - c.p[0], s.p[1] - c.p[1]);
      if (r < 1e-9) return;
      this.remember();
      const cn = this.addCurve("c", ["circle", this.pointFor(c), round4(r)]);
      if (L.diameter !== undefined) this.model.cons.push(["diameter", cn, L.diameter]);
      done(); this.solve();
      return;
    }
    if (t === "ellipse") {
      if (!this.pending.length) { this.pending = [s]; this.render(); this.showHud(); return; }
      const c = this.pending[0]; this.pending = [];
      const rx = Math.abs(s.p[0] - c.p[0]), ry = Math.abs(s.p[1] - c.p[1]);
      if (rx < 1e-9 || ry < 1e-9) { toast("Drag out a box: the ellipse needs a width and a height", true); return; }
      this.remember();
      this.addCurve("e", ["ellipse", this.pointFor(c), L.rx !== undefined ? L.rx : round4(rx), L.ry !== undefined ? L.ry : round4(ry)]);
      done(); this.solve();
      return;
    }
    if (t === "arc") {
      this.pending.push(s);
      if (this.pending.length < 3) { this.render(); this.showHud(); return; }
      const [c, a, b] = this.pending; this.pending = [];
      const r = Math.hypot(a.p[0] - c.p[0], a.p[1] - c.p[1]);
      if (r < 1e-9) return;
      this.remember();
      // the end goes on the circle through the start; the arc runs counterclockwise
      const ang = Math.atan2(b.p[1] - c.p[1], b.p[0] - c.p[0]);
      const end = b.name ? b : { p: [c.p[0] + r * Math.cos(ang), c.p[1] + r * Math.sin(ang)] };
      const an = this.addCurve("a", ["arc", this.pointFor(c), this.pointFor(a), this.pointFor(end)]);
      if (L.radius !== undefined) this.model.cons.push(["radius", an, L.radius]);
      done(); this.solve();
    }
  }

  // ------------------------------------------------------------------ type-in boxes while drawing
  showHud() {
    const spec = HUD[this.tool];
    if (!spec || !this.pending.length) { this.hideHud(); return; }
    if (this.hud && this.hud.tool === this.tool && this.hud.stage === this.pending.length) { this.placeHud(); return; }
    this.hideHud();
    // an arc asks for its radius after the centre, then for its sweep after the start
    const fields = (this.tool === "arc" ? [spec[this.pending.length - 1]] : spec).map(([key, label]) => ({ key, label, value: null, locked: false }));
    const el = document.createElement("div");
    el.className = "sk-hud";
    el.innerHTML = fields.map((f, i) => `<label>${esc(f.label)}<input data-hud="${i}" spellcheck="false" autocomplete="off" inputmode="decimal"></label>`).join("")
      + `<span class="sk-hint">type a value · Tab next · Enter places it</span>`;
    this.labels.appendChild(el);
    this.hud = { tool: this.tool, stage: this.pending.length, fields, el };
    el.querySelectorAll("input").forEach((inp) => {
      inp.addEventListener("input", () => {
        const f = fields[Number(inp.dataset.hud)];
        f.value = num(inp.value); f.locked = f.value !== null;
        inp.classList.toggle("locked", f.locked);
        this.render();
      });
      inp.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Tab") {
          e.preventDefault();
          const all = [...el.querySelectorAll("input")], i = all.indexOf(inp);
          const next = all[(i + (e.shiftKey ? all.length - 1 : 1)) % all.length];
          next.focus(); next.select();                 // typing replaces the live value shown
        } else if (e.key === "Enter") {
          e.preventDefault();
          if (this.cursor) this.place({ name: null, p: this.hudTarget(this.cursor.p) });
        } else if (e.key === "Escape") {
          e.preventDefault(); this.pending = []; this.hideHud(); this.render();
        }
      });
    });
    this.updateHud();
  }

  hideHud() { if (this.hud) { this.hud.el.remove(); this.hud = null; } }
  resetHud() { this.hideHud(); if (this.pending.length) this.showHud(); }
  hudLocked() { return !!(this.hud && this.hud.fields.some((f) => f.locked)); }
  focusHud(ch) {
    if (!this.hud) return false;
    const inp = this.hud.el.querySelector("input");
    inp.focus(); inp.value = ch; inp.dispatchEvent(new Event("input"));
    return true;
  }

  // where the shape's next point goes: the cursor, held to whatever was typed
  hudTarget(cur) {
    const a = this.pending[0] && this.pending[0].p;
    if (!a || !this.hud) return cur;
    const L = {};
    for (const f of this.hud.fields) if (f.locked && val(f.value) !== null) L[f.key] = val(f.value);
    const dx = cur[0] - a[0], dy = cur[1] - a[1], r0 = Math.hypot(dx, dy) || 1, ang0 = Math.atan2(dy, dx);
    if (this.tool === "line") {
      const ang = L.angle !== undefined ? L.angle * Math.PI / 180 : ang0, len = L.length !== undefined ? L.length : r0;
      return [a[0] + len * Math.cos(ang), a[1] + len * Math.sin(ang)];
    }
    if (this.tool === "rect") {
      const sx = dx < 0 ? -1 : 1, sy = dy < 0 ? -1 : 1;
      return [a[0] + (L.width !== undefined ? sx * L.width : dx), a[1] + (L.height !== undefined ? sy * L.height : dy)];
    }
    if (this.tool === "circle") {
      const r = L.diameter !== undefined ? L.diameter / 2 : r0;
      return [a[0] + r * Math.cos(ang0), a[1] + r * Math.sin(ang0)];
    }
    if (this.tool === "ellipse") {
      return [a[0] + (L.rx !== undefined ? (dx < 0 ? -1 : 1) * L.rx : dx), a[1] + (L.ry !== undefined ? (dy < 0 ? -1 : 1) * L.ry : dy)];
    }
    if (this.tool === "arc" && this.pending.length === 1) {
      const r = L.radius !== undefined ? L.radius : r0;
      return [a[0] + r * Math.cos(ang0), a[1] + r * Math.sin(ang0)];
    }
    if (this.tool === "arc" && this.pending.length === 2 && L.sweep !== undefined) {
      const st = this.pending[1].p, a0 = Math.atan2(st[1] - a[1], st[0] - a[0]), r = Math.hypot(st[0] - a[0], st[1] - a[1]);
      return [a[0] + r * Math.cos(a0 + L.sweep * Math.PI / 180), a[1] + r * Math.sin(a0 + L.sweep * Math.PI / 180)];
    }
    return cur;
  }

  // the boxes show the live values of the fields not typed in, and sit by the cursor
  updateHud() {
    if (!this.hud || !this.cursor || !this.pending.length) return;
    const a = this.pending[0].p, b = this.hudTarget(this.cursor.p);
    const live = {
      length: Math.hypot(b[0] - a[0], b[1] - a[1]), angle: ((Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI) + 360) % 360,
      width: Math.abs(b[0] - a[0]), height: Math.abs(b[1] - a[1]), diameter: 2 * Math.hypot(b[0] - a[0], b[1] - a[1]),
      rx: Math.abs(b[0] - a[0]), ry: Math.abs(b[1] - a[1]), radius: Math.hypot(b[0] - a[0], b[1] - a[1]),
    };
    if (this.tool === "arc" && this.pending.length === 2) {
      const st = this.pending[1].p;
      let sw = (Math.atan2(b[1] - a[1], b[0] - a[0]) - Math.atan2(st[1] - a[1], st[0] - a[0])) * 180 / Math.PI;
      live.sweep = (sw + 360) % 360;
    }
    this.hud.el.querySelectorAll("input").forEach((inp) => {
      const f = this.hud.fields[Number(inp.dataset.hud)];
      if (!f.locked && document.activeElement !== inp) inp.value = live[f.key] !== undefined ? fmt(Math.round(live[f.key] * 100) / 100) : "";
    });
    this.placeHud();
  }

  placeHud() {
    if (!this.hud || !this.cursor) return;
    const [sx, sy] = this.screen(this.cursor.p);
    this.hud.el.style.left = `${sx + 18}px`; this.hud.el.style.top = `${sy + 18}px`;
  }

  // ------------------------------------------------------------------ constraints and dimensions
  selected() {
    const pts = this.sel.filter((k) => k.startsWith("p:")).map((k) => k.slice(2));
    const crv = this.sel.filter((k) => k.startsWith("c:")).map((k) => k.slice(2));
    const kind = (n) => (this.solved && this.solved.curves && this.solved.curves[n] ? this.solved.curves[n].kind : (this.model.curves[n] || [])[0]);
    const lines = crv.filter((n) => kind(n) === "line");
    const rounds = crv.filter((n) => kind(n) === "circle" || kind(n) === "arc");
    return { pts, crv, lines, rounds, kind };
  }

  apply(rule) {
    const { pts, crv, lines, rounds } = this.selected();
    const add = [];
    if (rule === "horizontal" || rule === "vertical") {
      for (const l of lines) add.push([rule, l]);
      if (!lines.length && pts.length === 2) add.push([rule, pts[0], pts[1]]);
    } else if (rule === "coincident") {
      if (pts.length === 2) add.push(["coincident", pts[0], pts[1]]);
      else if (pts.length === 1 && crv.length === 1) add.push(["on", pts[0], crv[0]]);
    } else if (["parallel", "perpendicular", "collinear"].includes(rule) && lines.length === 2) add.push([rule, lines[0], lines[1]]);
    else if (rule === "tangent" && crv.length === 2 && rounds.length >= 1) add.push(["tangent", crv[0], crv[1]]);
    else if (rule === "equal" && (lines.length === 2 || rounds.length === 2)) add.push(["equal", ...(lines.length === 2 ? lines : rounds)]);
    else if (rule === "concentric" && rounds.length === 2) add.push(["concentric", rounds[0], rounds[1]]);
    else if (rule === "midpoint" && pts.length === 1 && lines.length === 1) add.push(["midpoint", pts[0], lines[0]]);
    else if (rule === "symmetric" && pts.length === 2 && lines.length === 1) add.push(["symmetric", pts[0], pts[1], lines[0]]);
    else if (rule === "fix") for (const n of [...pts, ...crv]) add.push(["fix", n]);
    if (!add.length) { toast(`${RULES.find((r) => r.id === rule).label}: select ${RULES.find((r) => r.id === rule).help}`, true); return; }
    this.addConstraints(add);
  }

  async addConstraints(add) {
    this.remember();
    this.model.cons.push(...add);
    const ok = await this.solve();
    if (!ok) { this.undo(true); return; }
    this.sel = [];
    this.render();
  }

  // what a dimension on the selection would be, with its current value
  dimensionFor() {
    const { pts, crv, lines, rounds, kind } = this.selected();
    const P = (n) => this.pos(n);
    const len = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (lines.length === 1 && !pts.length && crv.length === 1) {
      const [a, b] = this.curveGeom(lines[0]);
      return { kind: "length", args: [lines[0]], value: len(a, b), at: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] };
    }
    if (rounds.length === 1 && crv.length === 1 && !pts.length) {
      const n = rounds[0], info = this.solved.curves[n];
      const ctr = info.centre || this.pos(info.pts[0]);
      const r = info.radius || len(ctr, this.pos(info.pts[1]));
      const g = this.curveGeom(n), at = g[Math.floor(g.length / 8)];
      return kind(n) === "circle" ? { kind: "diameter", args: [n], value: 2 * r, at } : { kind: "radius", args: [n], value: r, at };
    }
    if (pts.length === 2 && !crv.length) {
      const a = P(pts[0]), b = P(pts[1]);
      return { kind: "distance", args: pts, value: len(a, b), at: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2],
        alt: [{ kind: "hdistance", value: b[0] - a[0] }, { kind: "vdistance", value: b[1] - a[1] }] };
    }
    if (pts.length === 1 && lines.length === 1 && crv.length === 1) {
      const [a, b] = this.curveGeom(lines[0]), p = P(pts[0]);
      const d = Math.abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / len(a, b);
      return { kind: "distance", args: [pts[0], lines[0]], value: d, at: p };
    }
    if (lines.length === 2 && crv.length === 2) {
      const [a, b] = this.curveGeom(lines[0]), [c, d] = this.curveGeom(lines[1]);
      const u = [b[0] - a[0], b[1] - a[1]], v = [d[0] - c[0], d[1] - c[1]];
      const ang = Math.abs(Math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])) * 180 / Math.PI;
      if (Math.abs(Math.sin(ang * Math.PI / 180)) < 1e-6) {
        const dist = Math.abs(u[0] * (c[1] - a[1]) - u[1] * (c[0] - a[0])) / len(a, b);
        return { kind: "distance", args: lines, value: dist, at: c };
      }
      return { kind: "angle", args: lines, value: ang, at: b, unit: "°" };
    }
    return null;
  }

  tryDimension(complain = true) {
    const dim = this.dimensionFor();
    if (!dim) { if (complain) toast("Dimension: select a line, a circle or arc, two points, a point and a line, or two lines", true); return; }
    this.askValue(dim);
  }

  askValue(dim, index = null) {
    this.closeAsk();
    const box = document.createElement("div");
    box.className = "sk-ask";
    const alts = dim.alt ? `<div class="sk-alt">${["distance", ...dim.alt.map((a) => a.kind)].map((k) => `<button type="button" data-kind="${k}"${k === dim.kind ? ' aria-pressed="true"' : ""}>${{ distance: "Aligned", hdistance: "Horizontal", vdistance: "Vertical" }[k]}</button>`).join("")}</div>` : "";
    box.innerHTML = `<label>${esc(dim.kind)} <input type="text" value="${esc(dim.text || fmt(dim.value))}" spellcheck="false" autocomplete="off"></label>${alts}<span class="sk-hint">Enter sets it · a number or P.name</span>`;
    this.labels.appendChild(box);
    const [sx, sy] = this.screen(dim.at);
    box.style.left = `${sx + 12}px`; box.style.top = `${sy - 12}px`;
    const inp = box.querySelector("input");
    let kind = dim.kind;
    box.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => {
      kind = b.dataset.kind;
      box.querySelectorAll("[data-kind]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      const alt = (dim.alt || []).find((a) => a.kind === kind);
      inp.value = fmt(alt ? alt.value : dim.value); inp.focus(); inp.select();
    }));
    inp.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") { this.closeAsk(); return; }
      if (e.key !== "Enter") return;
      e.preventDefault();
      const t = inp.value.trim();
      if (!t) return;
      const v = /^-?[\d.]+$/.test(t) ? Number(t) : { expr: t };
      const con = [kind, ...dim.args, v];
      this.closeAsk();
      if (index !== null) { this.remember(); this.model.cons[index] = con; this.solve().then((ok) => { if (!ok) this.undo(true); }); }
      else this.addConstraints([con]);
    });
    setTimeout(() => { inp.focus(); inp.select(); }, 0);
    this.ask = box;
  }
  closeAsk() { if (this.ask) { this.ask.remove(); this.ask = null; } }

  // ------------------------------------------------------------------ deleting
  deleteSelection() {
    if (!this.sel.length) return;
    this.remember();
    const gone = new Set();
    for (const k of this.sel) {
      if (k.startsWith("k:")) { this.model.cons[Number(k.slice(2))] = null; continue; }
      const n = k.slice(2);
      if (k.startsWith("c:")) { delete this.model.curves[n]; gone.add(n); }
      if (k.startsWith("p:")) {
        delete this.model.points[n]; gone.add(n);
        for (const [cn, c] of Object.entries(this.model.curves)) if (c.slice(1).includes(n)) { delete this.model.curves[cn]; gone.add(cn); }
      }
    }
    // points no curve uses any more go too, if they were made by drawing (not named on purpose)
    this.model.cons = this.model.cons.filter((c) => c && !c.slice(1).some((a) => typeof a === "string" && (gone.has(a) || gone.has(a.split(".")[0]))));
    this.sel = [];
    this.solve();
  }

  // ------------------------------------------------------------------ undo inside the sketch
  remember() { this.history.push(JSON.stringify(this.model)); this.future = []; if (this.history.length > 200) this.history.shift(); }
  undo(quiet = false) {
    if (!this.history.length) return;
    this.future.push(JSON.stringify(this.model));
    this.model = JSON.parse(this.history.pop());
    if (!quiet) this.solve(); else this.solve();
  }
  redo() {
    if (!this.future.length) return;
    this.history.push(JSON.stringify(this.model));
    this.model = JSON.parse(this.future.pop());
    this.solve();
  }

  // ------------------------------------------------------------------ the statement
  args(extra = []) {
    const ind = "\n    ";
    const pts = Object.entries(this.model.points).map(([n, v]) => `${JSON.stringify(n)}: ${isExpr(v) ? v.expr : `(${fmt(v[0])}, ${fmt(v[1])})`}`);
    const crv = Object.entries(this.model.curves).map(([n, c]) => `${JSON.stringify(n)}: (${c.map(text).join(", ")})`);
    const cons = [...this.model.cons, ...extra].map((c) => `(${c.map(text).join(", ")})`);
    const out = { at: this.at || "", points: `{${ind}${pts.join("," + ind)}}`, curves: `{${ind}${crv.join("," + ind)}}` };
    if (!pts.length) out.points = "{}";
    if (!crv.length) out.curves = "{}";
    if (cons.length) out.constraints = `[${ind}${cons.join("," + ind)}]`;
    if (this.spin) out.spin = this.spin;
    return out;
  }

  adoptSolved() {           // write the solved positions into the model (plain points only)
    if (!this.solved) return;
    for (const [n, p] of Object.entries(this.solved.points)) if (Array.isArray(this.model.points[n])) this.model.points[n] = [round4(p[0]), round4(p[1])];
    for (const [n, c] of Object.entries(this.model.curves)) {
      const info = this.solved.curves[n];
      if (c[0] === "circle" && info && info.radius && typeof c[2] === "number") c[2] = round4(info.radius);
    }
  }

  // Run the real sketch step on the current model (without writing it). extra: temporary
  // constraints (dragging). Returns false when the constraints can't all be met.
  async solve(extra = [], dragging = false) {
    const seq = (this.seq = (this.seq || 0) + 1);
    let r;
    this.busy = (this.busy || 0) + 1;            // requests in flight (tests wait for 0)
    try {
      r = await post("/api/inspect", { op: "sketch", args: this.args(extra), edit: this.edit });
    } catch (e) { this.setStatus(e.message, "error"); return false; } finally { this.busy--; }
    if (seq !== this.seq) return true;           // a newer request is on its way
    if (r.error) { if (!dragging) { this.setStatus(r.error, "error"); toast(r.error, true); } return false; }
    const res = r.result || {};
    if (res.status === "error" || res.status === "skipped") {
      if (!dragging) { this.setStatus(res.message || "the sketch can't be built", "error"); toast(res.message || "can't be built", true); }
      return false;
    }
    this.solved = (r.info || {}).sketch || null;
    this.info = this.solved;
    if (!dragging) this.adoptSolved();
    const dof = this.solved ? this.solved.dof : 0;
    const nreg = this.solved ? this.solved.regions.filter((x) => x.profile).length : 0;
    this.setStatus(dof === 0 ? `Fully constrained · ${nreg} profile region${nreg === 1 ? "" : "s"}`
      : `${dof} degree${dof === 1 ? "" : "s"} of freedom left · ${nreg} profile region${nreg === 1 ? "" : "s"}`, dof === 0 ? "ok" : "");
    this.render();
    if (!dragging) this.refreshSide();
    return true;
  }

  async ok() {
    if (this.stage === "plane") return;
    if (!Object.keys(this.model.curves).length) { toast("Draw something first (or Esc to leave)", true); return; }
    try {
      this.adoptSolved();
      const r = await post("/api/apply", { op: "sketch", args: this.args(), edit: this.edit });
      this.app.closeDialog(true);
      await this.app.refresh();
      toast(`${this.edit ? "Updated" : "Added"} ${r.name}`);
    } catch (e) { this.setStatus(e.message, "error"); toast(e.message, true); }
  }

  close() {
    this.closeAsk(); this.hideHud();
    this.vp.temp.remove(this.group);
    this.labels.remove();
    this.vp.onFrame = this.vp.onFrame.filter((f) => f !== this.onFrame);
    this.vp.canvas.removeEventListener("pointerdown", this.down, true);
    window.removeEventListener("pointerup", this.up, true);
    this.panel.classList.remove("sk-panel");
    this.panel.hidden = true; this.panel.innerHTML = "";
    this.vp.showLocalGrid(null);
    this.vp.setOriginForced(false); this.vp.setOriginHover(null);
  }

  // ------------------------------------------------------------------ keys
  key(ev) {
    if (ev.target && ev.target.closest && ev.target.closest(".sk-ask, .sk-hud, .sk-side")) return false;     // typing in a box
    const mod = ev.metaKey || ev.ctrlKey;
    if (mod && ev.code === "KeyZ") { ev.preventDefault(); if (ev.shiftKey) this.redo(); else this.undo(); return true; }
    if (ev.key === "Escape") {
      if (this.ask) { this.closeAsk(); return true; }
      if (this.pending.length) { this.pending = []; this.hideHud(); this.render(); return true; }
      if (this.sel.length) { this.sel = []; this.render(); this.refreshSide(); return true; }
      if (this.tool !== "select") { this.setTool("select"); return true; }
      return false;                 // the app closes the sketch (asking nothing: nothing was written)
    }
    if (mod || ev.altKey) return false;
    if (this.hud && /^[\d.\-]$/.test(ev.key)) { ev.preventDefault(); this.focusHud(ev.key); return true; }   // typing a size while drawing
    if (ev.key === "Enter" && this.pending.length) { ev.preventDefault(); this.pending = []; this.hideHud(); this.render(); return true; }
    if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); this.deleteSelection(); return true; }
    if (ev.key === "x" || ev.key === "X") { this.toggleConstruction(); return true; }
    const k = (ev.shiftKey ? "Shift+" : "") + ev.key.toUpperCase();
    const d = DRAW.find((t) => t.key === k);
    if (d) { if (d.id === "dim") { this.setTool("dim"); if (this.sel.length) this.tryDimension(); } else this.setTool(d.id); return true; }
    const rule = RULES.find((r) => r.key === k);
    if (rule) { this.apply(rule.id); return true; }
    return false;
  }

  toggleConstruction() {
    const crv = this.sel.filter((k) => k.startsWith("c:")).map((k) => k.slice(2));
    if (crv.length) {
      this.remember();
      for (const n of crv) {
        const c = this.model.curves[n];
        const i = c.indexOf("construction");
        if (i >= 0) c.splice(i, 1); else c.push("construction");
      }
      this.solve();
    } else { this.construction = !this.construction; this.renderPanel(); }
  }

  setTool(id) {
    if (this.tool === "project" && id !== "project") this.vp.setHover(null);
    this.tool = id; this.pending = []; this.hideHud();
    if (id !== "select" && id !== "dim") this.sel = [];
    this.labels.classList.toggle("drawing", id !== "select" && id !== "dim");    // labels don't catch drawing clicks
    this.renderPanel(); this.render();
    const d = DRAW.find((t) => t.id === id);
    this.app.setHint(d ? d.help : "");
  }

  setStatus(msg, kind = "") {
    this.status = [msg, kind];                // kept: the panel is redrawn when the tool changes
    const el = this.panel.querySelector(".sk-status");
    if (el) { el.textContent = msg; el.className = "sk-status " + kind; }
  }

  // ------------------------------------------------------------------ the panel
  renderPanel() {
    const kb = (k) => `<kbd>${esc(k)}</kbd>`;
    this.panel.innerHTML = `
      <div class="dlg-head"><b>${this.edit ? "Edit sketch " + esc(this.edit) : "Sketch"}</b><span>${esc(this.at || "")}</span></div>
      <div class="sk-tools" role="toolbar" aria-label="Draw">${DRAW.map((t) =>
        `<button type="button" data-draw="${t.id}" aria-pressed="${t.id === this.tool}" title="${esc(t.help)}">${esc(t.label)} ${kb(t.key)}</button>`).join("")}
        <button type="button" data-construction aria-pressed="${this.construction}" title="New curves (or the selected ones) become construction: guides that bound no region">Construction ${kb("X")}</button></div>
      <div class="sk-tools" role="toolbar" aria-label="Constrain">${RULES.map((r) =>
        `<button type="button" data-rule="${r.id}" title="${esc(r.label + ": " + r.help)}">${r.glyph} ${esc(r.label)} ${kb(r.key)}</button>`).join("")}</div>
      <div class="sk-status"></div>
      <div class="sk-side"></div>
      <div class="dlg-foot"><span class="sk-keys">${kb("⌘/Ctrl Z")} undo · ${kb("Del")} delete · ${kb("Esc")} step back</span>
        <button type="button" id="sk-cancel">Cancel</button><button type="button" class="primary" id="sk-ok">Finish sketch</button></div>`;
    this.panel.querySelectorAll("[data-draw]").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.draw === "dim") { this.setTool("dim"); if (this.sel.length) this.tryDimension(); } else this.setTool(b.dataset.draw);
    }));
    this.panel.querySelector("[data-construction]").addEventListener("click", () => this.toggleConstruction());
    this.panel.querySelectorAll("[data-rule]").forEach((b) => b.addEventListener("click", () => this.apply(b.dataset.rule)));
    this.panel.querySelector("#sk-ok").addEventListener("click", () => this.ok());
    this.panel.querySelector("#sk-cancel").addEventListener("click", () => this.app.closeDialog());
    if (this.status) this.setStatus(...this.status);
    this.refreshSide();
  }

  // ------------------------------------------------------------------ the side panel: what's selected, and everything in the sketch
  // Inspector: the selected item's values, editable (Tab moves, Enter sets). A length, diameter
  // or radius typed here becomes a dimension (it stays put; 🔓 removes it). Contents: every
  // curve, point and constraint, as a list (hover highlights, click selects, × deletes), and
  // the whole sketch as text.
  refreshSide() {
    const box = this.panel.querySelector(".sk-side");
    if (!box || !this.frame) { if (box) box.innerHTML = ""; return; }
    box.innerHTML = this.inspectorHtml() + this.contentsHtml();
    this.bindSide(box);
  }

  dimIndex(kinds, args) {
    return this.model.cons.findIndex((c) => c && kinds.includes(c[0]) && c.length === args.length + 2 && args.every((a, i) => c[i + 1] === a));
  }

  field(label, key, value, opts = {}) {
    const locked = opts.lock !== undefined && opts.lock >= 0;
    return `<label class="sk-f"><span>${esc(label)}</span><input data-f="${esc(key)}" value="${esc(value)}" spellcheck="false" autocomplete="off"${opts.ro ? " readonly" : ""}>`
      + (locked ? `<button type="button" class="sk-unlock" data-unlock="${opts.lock}" title="Locked by a dimension: click to remove it">🔒</button>` : opts.lock !== undefined ? `<span class="sk-free" title="Free: typing a value adds a dimension">·</span>` : "")
      + `</label>`;
  }

  inspectorHtml() {
    if (this.sel.length !== 1) {
      return this.sel.length ? `<div class="sk-insp"><b>${this.sel.length} selected</b><span class="sk-hint">Use the constraint buttons above, D for a dimension, Delete to remove.</span></div>` : "";
    }
    const k = this.sel[0], n = k.slice(2), fx = (v) => (isExpr(v) ? v.expr : fmt(v));
    const dimv = (i) => (i >= 0 ? fx(this.model.cons[i][this.model.cons[i].length - 1]) : null);
    if (k.startsWith("p:")) {
      const p = this.pos(n), movable = Array.isArray(this.model.points[n]);
      const ix = this.dimIndex(["x"], [n]), iy = this.dimIndex(["y"], [n]);
      return `<div class="sk-insp" data-item="${esc(k)}"><b>${esc(n)}</b><span class="sk-kind">point${movable ? "" : " (fixed)"}</span>`
        + this.field("X", "x", dimv(ix) ?? fmt(p[0]), { ro: !movable, lock: movable ? ix : undefined })
        + this.field("Y", "y", dimv(iy) ?? fmt(p[1]), { ro: !movable, lock: movable ? iy : undefined }) + `</div>`;
    }
    if (k.startsWith("k:")) {
      const c = this.model.cons[Number(n)];
      if (!c) return "";
      const isDim = c.length > 1 && (typeof c[c.length - 1] === "number" || isExpr(c[c.length - 1]));
      return `<div class="sk-insp" data-item="${esc(k)}"><b>${esc(c[0])}</b><span class="sk-kind">${esc(c.slice(1, isDim ? -1 : undefined).join(", "))}</span>`
        + (isDim ? this.field("Value", "con", fx(c[c.length - 1])) : "") + `</div>`;
    }
    const c = this.model.curves[n];
    if (!c) return "";
    const info = (this.solved && this.solved.curves[n]) || {};
    const kind = c[0], constr = c.includes("construction");
    let html = `<div class="sk-insp" data-item="${esc(k)}"><b>${esc(n)}</b><span class="sk-kind">${esc(kind)}${constr ? " · construction" : ""}</span>`;
    if (kind === "line") {
      const [a, b] = this.curveGeom(n) || [[0, 0], [0, 0]];
      const i = this.dimIndex(["length"], [n]);
      html += this.field("Length", "length", dimv(i) ?? fmt(Math.hypot(b[0] - a[0], b[1] - a[1])), { lock: i })
        + this.field("Angle °", "angle", fmt(((Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI) + 360) % 360), { ro: true });
    } else if (kind === "circle") {
      const i = this.dimIndex(["diameter", "radius"], [n]);
      const cur = i >= 0 ? (this.model.cons[i][0] === "radius" && typeof this.model.cons[i][2] === "number" ? fmt(2 * this.model.cons[i][2]) : dimv(i)) : fmt(2 * (info.radius || c[2]));
      html += this.field("Diameter", "diameter", cur, { lock: i });
    } else if (kind === "arc") {
      const i = this.dimIndex(["radius", "diameter"], [n]);
      const g = this.curveGeom(n);
      const r = info.centre ? Math.hypot(g[0][0] - info.centre[0], g[0][1] - info.centre[1]) : Math.hypot(this.pos(c[2])[0] - this.pos(c[1])[0], this.pos(c[2])[1] - this.pos(c[1])[1]);
      html += this.field("Radius", "radius", i >= 0 && this.model.cons[i][0] === "radius" ? dimv(i) : fmt(r), { lock: i });
    } else if (kind === "ellipse") {
      html += this.field("Half-width X", "rx", fx(c[2])) + this.field("Half-width Y", "ry", fx(c[3])) + this.field("Angle °", "eangle", fx(typeof c[4] === "number" || isExpr(c[4]) ? c[4] : 0));
    } else if (kind === "parametric") {
      html += this.field("x(t) =", "px", c[1]) + this.field("y(t) =", "py", c[2]) + this.field("t from", "t0", fx(c[3])) + this.field("t to", "t1", fx(c[4]));
    }
    html += `<label class="sk-f sk-check"><input type="checkbox" data-constr ${constr ? "checked" : ""}> Construction (a guide, bounds no region)</label></div>`;
    return html;
  }

  contentsHtml() {
    const tabs = [["curves", "Curves"], ["points", "Points"], ["cons", "Constraints"], ["text", "Text"]];
    let html = `<div class="sk-contents"><div class="sk-tabs" role="tablist">${tabs.map(([id, l]) => `<button type="button" role="tab" data-tab="${id}" aria-selected="${this.tab === id}">${l}</button>`).join("")}</div>`;
    const fx = (v) => (isExpr(v) ? v.expr : typeof v === "number" ? fmt(v) : String(v));
    const row = (key, name, detail, extra = "") => `<li data-key="${esc(key)}" class="${this.sel.includes(key) ? "sel" : ""}"><span class="nm">${esc(name)}</span><span class="dt">${esc(detail)}</span>${extra}<button type="button" class="x" data-del="${esc(key)}" title="Delete" aria-label="Delete ${esc(name)}">×</button></li>`;
    if (this.tab === "curves") {
      const items = Object.entries(this.model.curves).map(([n, c]) => row("c:" + n, n, `${c[0]} ${c.slice(1).filter((v) => v !== "construction").map(fx).join(", ")}${c.includes("construction") ? " · construction" : ""}`));
      html += `<ul class="sk-list">${items.join("") || `<li class="empty">No curves yet: pick a drawing tool above.</li>`}</ul>`;
    } else if (this.tab === "points") {
      const items = Object.keys(this.allPoints()).map((n) => {
        const p = this.pos(n), fixed = this.solved && this.solved.fixed.includes(n);
        return row("p:" + n, n, `(${fmt(p[0])}, ${fmt(p[1])})${fixed ? " · fixed" : ""}`);
      });
      html += `<ul class="sk-list">${items.join("") || `<li class="empty">No points yet.</li>`}</ul>`;
    } else if (this.tab === "cons") {
      const items = this.model.cons.map((c, i) => {
        if (!c) return "";
        const isDim = c.length > 1 && (typeof c[c.length - 1] === "number" || isExpr(c[c.length - 1]));
        const extra = isDim ? `<input class="sk-cv" data-con="${i}" value="${esc(fx(c[c.length - 1]))}" aria-label="${esc(c[0])} value" spellcheck="false">` : "";
        return row("k:" + i, c[0], c.slice(1, isDim ? -1 : undefined).map(fx).join(", "), extra);
      });
      html += `<ul class="sk-list">${items.join("") || `<li class="empty">No constraints yet.</li>`}</ul>`;
    } else {
      const a = this.args();
      html += `<textarea class="sk-text" spellcheck="false" aria-label="The sketch as text">points=${esc(a.points)},\ncurves=${esc(a.curves)},\nconstraints=${esc(a.constraints || "[]")}</textarea>`
        + `<div class="sk-text-foot"><span class="sk-hint">Edit any line, then Apply (⌘/Ctrl Enter).</span><button type="button" data-apply-text>Apply</button></div>`;
    }
    return html + `</div>`;
  }

  bindSide(box) {
    box.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { this.tab = b.dataset.tab; this.refreshSide(); }));
    box.querySelectorAll(".sk-list li[data-key]").forEach((li) => {
      const key = li.dataset.key;
      li.addEventListener("mouseenter", () => { this.hoverKey = key; this.render(); });
      li.addEventListener("mouseleave", () => { if (this.hoverKey === key) { this.hoverKey = null; this.render(); } });
      li.addEventListener("click", (e) => {
        if (e.target.closest("input, button")) return;
        if (this.tool !== "select" && this.tool !== "dim") this.setTool("select");
        this.sel = e.shiftKey ? (this.sel.includes(key) ? this.sel.filter((x) => x !== key) : [...this.sel, key]) : [key];
        this.render(); this.refreshSide();
      });
    });
    box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => { this.sel = [b.dataset.del]; this.deleteSelection(); }));
    box.querySelectorAll("[data-unlock]").forEach((b) => b.addEventListener("click", () => {
      this.remember(); this.model.cons[Number(b.dataset.unlock)] = null; this.model.cons = this.model.cons.filter(Boolean); this.solve();
    }));
    const constr = box.querySelector("[data-constr]");
    if (constr) constr.addEventListener("change", () => this.toggleConstruction());
    // Enter (or leaving the box) sets a value; Tab just moves on
    const commit = (inp, fn) => {
      inp.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); fn(inp.value); } });
      inp.addEventListener("change", () => fn(inp.value));
    };
    box.querySelectorAll(".sk-insp input[data-f]").forEach((inp) => { if (!inp.readOnly) commit(inp, (v) => this.setValue(inp.dataset.f, v)); });
    box.querySelectorAll("input[data-con]").forEach((inp) => commit(inp, (v) => {
      const i = Number(inp.dataset.con), x = num(v), c = this.model.cons[i];
      if (!c || x === null) return;
      this.remember(); c[c.length - 1] = x; this.solve().then((ok) => { if (!ok) this.undo(true); });
    }));
    const ta = box.querySelector(".sk-text");
    if (ta) {
      ta.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); this.applyText(ta.value); } });
      box.querySelector("[data-apply-text]").addEventListener("click", () => this.applyText(ta.value));
    }
  }

  // a value typed in the inspector
  setValue(key, text) {
    const k = this.sel[0];
    if (!k) return;
    const n = k.slice(2), v = num(text);
    const asDim = async (kinds, kind, args, value) => {
      this.remember();
      const i = this.dimIndex(kinds, args);
      if (value === null) { if (i >= 0) this.model.cons.splice(i, 1); } else if (i >= 0) this.model.cons[i] = [kind, ...args, value]; else this.model.cons.push([kind, ...args, value]);
      if (!(await this.solve())) this.undo(true);
    };
    if (k.startsWith("k:")) {
      const c = this.model.cons[Number(n)];
      if (c && v !== null) { this.remember(); c[c.length - 1] = v; this.solve().then((ok) => { if (!ok) this.undo(true); }); }
      return;
    }
    if (k.startsWith("p:")) {
      const ax = key === "x" ? 0 : 1, i = this.dimIndex([key], [n]);
      if (i >= 0) return asDim([key], key, [n], v);                // held by a dimension: change it
      if (typeof v !== "number") return asDim([key], key, [n], v);  // an expression: it has to be a dimension
      this.remember(); this.model.points[n][ax] = v; this.solve();
      return;
    }
    const c = this.model.curves[n];
    if (key === "length") return asDim(["length"], "length", [n], v);
    if (key === "diameter") return asDim(["diameter", "radius"], "diameter", [n], v);
    if (key === "radius") return asDim(["radius", "diameter"], "radius", [n], v);
    const slot = { rx: 2, ry: 3, eangle: 4, px: 1, py: 2, t0: 3, t1: 4 }[key];
    if (slot === undefined || !c) return;
    this.remember();
    const value = key === "px" || key === "py" ? String(text) : v;
    if (value === null) return;
    if (key === "eangle" && (c.length < 5 || typeof c[4] === "string")) c.splice(4, 0, value); else c[slot] = value;
    this.solve().then((ok) => { if (!ok) this.undo(true); });
  }

  // the Text tab: the whole sketch as points=..., curves=..., constraints=... (the server reads it)
  async applyText(text) {
    try {
      const src = await post("/api/sketch_parse", { text });
      this.remember();
      this.model = { points: {}, curves: {}, cons: [] };
      for (const [n, v] of Object.entries(src.points)) this.model.points[n] = Array.isArray(v) ? v.slice() : v;
      for (const [n, v] of Object.entries(src.curves)) this.model.curves[n] = v.slice();
      for (const v of src.constraints) if (!Array.isArray(v)) throw new Error("each constraint is written (kind, ...), e.g. (\"horizontal\", \"l1\")");
      this.model.cons = src.constraints.map((c) => c.slice());
      this.sel = [];
      if (!(await this.solve())) { this.undo(true); return; }
      toast("Sketch updated from the text");
    } catch (e) { toast(e.message, true); }
  }

  // ------------------------------------------------------------------ drawing the sketch
  render() {
    const g = this.group;
    g.clear();
    if (!this.frame) return;
    const selSet = new Set(this.sel);
    const lineMat = (c, dashed) => dashed
      ? new THREE.LineDashedMaterial({ color: col(c), dashSize: this.vp.worldPerPx() * 6, gapSize: this.vp.worldPerPx() * 4, depthTest: false })
      : new THREE.LineBasicMaterial({ color: col(c), depthTest: false });
    const addLine = (pts, c, dashed = false) => {
      const geo = new THREE.BufferGeometry().setFromPoints(pts.map((p) => new THREE.Vector3(...this.w(p))));
      const l = new THREE.Line(geo, lineMat(c, dashed));
      if (dashed) l.computeLineDistances();
      l.renderOrder = 14; g.add(l);
    };
    // shaded regions (the profile darker)
    if (this.solved) {
      for (const r of this.solved.regions) {
        const pos = [];
        for (const p of r.tris) pos.push(...this.w(p));
        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
        const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: col("--accent"), transparent: true, opacity: r.profile ? 0.28 : 0.1, side: THREE.DoubleSide, depthTest: false }));
        m.renderOrder = 12; g.add(m);
      }
    }
    for (const n of Object.keys(this.model.curves)) {
      const pts = this.curveGeom(n);
      if (!pts) continue;
      const c = this.model.curves[n];
      const info = this.solved && this.solved.curves[n];
      const constr = c.includes("construction"), fixed = info && info.fixed;
      const key = "c:" + n;
      const color = selSet.has(key) ? "--sel" : this.hoverKey === key ? "--accent" : fixed ? "--ref" : constr ? "--ink-2" : "--ink";
      addLine(pts, color, constr);
    }
    // points
    for (const n of Object.keys(this.allPoints())) {
      const key = "p:" + n;
      const fixed = this.solved && this.solved.fixed.includes(n);
      const color = selSet.has(key) ? "--sel" : this.hoverKey === key ? "--accent" : fixed ? "--ref" : "--ink";
      const m = new THREE.Mesh(new THREE.SphereGeometry(1, 10, 6), new THREE.MeshBasicMaterial({ color: col(color), depthTest: false }));
      m.position.set(...this.w(this.pos(n))); m.userData.px = selSet.has(key) || this.hoverKey === key ? 4 : 2.6; m.renderOrder = 15; g.add(m);
    }
    // the rubber band for the tool in progress
    const cur = this.cursor && this.cursor.p && (this.pending.length ? this.hudTarget(this.cursor.p) : this.cursor.p);
    if (cur && this.pending.length) {
      const a = this.pending[0].p;
      if (this.tool === "line") addLine([a, cur], "--sel", true);
      if (this.tool === "rect") addLine([a, [cur[0], a[1]], cur, [a[0], cur[1]], a], "--sel", true);
      if (this.tool === "circle" || (this.tool === "arc" && this.pending.length === 1)) {
        const r = Math.hypot(cur[0] - a[0], cur[1] - a[1]);
        addLine(Array.from({ length: 65 }, (_, i) => { const t = i / 64 * 2 * Math.PI; return [a[0] + r * Math.cos(t), a[1] + r * Math.sin(t)]; }), "--sel", true);
      }
      if (this.tool === "ellipse") {
        const rx = Math.abs(cur[0] - a[0]), ry = Math.abs(cur[1] - a[1]);
        addLine(Array.from({ length: 65 }, (_, i) => { const t = i / 64 * 2 * Math.PI; return [a[0] + rx * Math.cos(t), a[1] + ry * Math.sin(t)]; }), "--sel", true);
      }
      if (this.tool === "arc" && this.pending.length === 2) {
        const s = this.pending[1].p, r = Math.hypot(s[0] - a[0], s[1] - a[1]);
        let a0 = Math.atan2(s[1] - a[1], s[0] - a[0]), a1 = Math.atan2(cur[1] - a[1], cur[0] - a[0]);
        while (a1 <= a0) a1 += 2 * Math.PI;
        addLine(Array.from({ length: 49 }, (_, i) => { const t = a0 + (a1 - a0) * i / 48; return [a[0] + r * Math.cos(t), a[1] + r * Math.sin(t)]; }), "--sel", true);
      }
    }
    this.vp.updateScreenSized && this.vp.updateScreenSized();
    this.renderLabels();
  }

  // constraint glyphs and dimension values, as HTML over the view
  renderLabels() {
    // only the labels are redrawn: the boxes being typed in stay put (and keep the focus)
    this.labels.querySelectorAll(".sk-label").forEach((el) => el.remove());
    this.labelItems = [];
    const mid = (n) => {
      const g = this.curveGeom(n);
      if (!g) return null;
      if (g.length === 2) return [(g[0][0] + g[1][0]) / 2, (g[0][1] + g[1][1]) / 2];
      return g[Math.floor(g.length / 2)];
    };
    const anchorOf = (c) => {
      for (const a of c.slice(1)) {
        if (typeof a !== "string") continue;
        if (this.model.curves[a]) return mid(a);
        if (this.allPoints()[a] || this.allPoints()[a.split(".")[0]]) return this.pos(a);
      }
      return null;
    };
    this.model.cons.forEach((c, i) => {
      if (!c) return;
      const at = anchorOf(c);
      if (!at) return;
      const rule = RULES.find((r) => r.id === c[0] || (r.id === "coincident" && c[0] === "on"));
      const isDim = !rule && c.length > 1 && (typeof c[c.length - 1] === "number" || isExpr(c[c.length - 1]));
      const el = document.createElement("span");
      el.className = "sk-label " + (isDim ? "sk-dim" : "sk-glyph") + (this.sel.includes("k:" + i) ? " sel" : "");
      if (isDim) {
        const v = c[c.length - 1];
        el.textContent = (c[0] === "diameter" ? "⌀" : c[0] === "radius" ? "R" : c[0] === "angle" ? "∠" : "") + (isExpr(v) ? v.expr : fmt(v)) + (c[0] === "angle" ? "°" : "");
        el.title = `${c[0]}: double-click to change`;
        el.addEventListener("dblclick", () => {
          this.askValue({ kind: c[0], args: c.slice(1, -1), value: typeof v === "number" ? v : 0, text: isExpr(v) ? v.expr : fmt(v), at }, i);
        });
      } else {
        el.textContent = rule ? rule.glyph : c[0];
        el.title = c[0];
      }
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const k = "k:" + i;
        this.sel = e.shiftKey ? (this.sel.includes(k) ? this.sel.filter((x) => x !== k) : [...this.sel, k]) : [k];
        this.render(); this.refreshSide();
      });
      this.labels.appendChild(el);
      this.labelItems.push({ el, at, off: isDim ? [12, -20] : [13 + 17 * (this.labelItems.filter((x) => x.at === at).length), 13] });   // beside, not on, the curve
    });
    this.placeLabels();
  }

  placeLabels() {
    if (!this.labelItems || !this.frame) return;
    for (const it of this.labelItems) {
      const [sx, sy] = this.screen(it.at);
      it.el.style.left = `${sx + it.off[0]}px`; it.el.style.top = `${sy + it.off[1]}px`;
    }
  }
}

function round4(v) { return Math.round(v * 1e4) / 1e4; }
function segDist(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy;
  const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

export const SKETCH_RULES = RULES;
export const SKETCH_DRAW = DRAW;
