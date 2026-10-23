// "View attributes": everything a body (or a sketch) defines, by name, in tabs: its points
// (design anchors, then the points its faces offer: centres, corners, holes, midpoints), its
// edges and its faces. Hovering a row lights it up in the view; clicking selects it (faces and
// edges) or marks it (points) and copies its reference, ready to paste into a field or the
// script. The panel follows rebuilds: it re-reads the body each time the scene changes.
import { $, esc, q, toast } from "./api.js";

const fmt = (v) => { const r = Math.round(v * 100) / 100; return Object.is(r, -0) ? "0" : String(r); };
const xyz = (p) => `(${fmt(p[0])}, ${fmt(p[1])}, ${fmt(p[2])})`;

function polyLength(p) {
  let L = 0;
  for (let i = 3; i < p.length; i += 3) L += Math.hypot(p[i] - p[i - 3], p[i + 1] - p[i - 2], p[i + 2] - p[i - 1]);
  return L;
}

function meshArea(f) {
  const p = f.p, ix = f.i;
  let A = 0;
  for (let t = 0; t < ix.length; t += 3) {
    const a = ix[t] * 3, b = ix[t + 1] * 3, c = ix[t + 2] * 3;
    const u = [p[b] - p[a], p[b + 1] - p[a + 1], p[b + 2] - p[a + 2]], v = [p[c] - p[a], p[c + 1] - p[a + 1], p[c + 2] - p[a + 2]];
    A += Math.hypot(u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]) / 2;
  }
  return A;
}

function polyMid(p) {
  const n = p.length / 3, k = Math.floor(n / 2) * 3;
  return [p[k], p[k + 1], p[k + 2]];
}

export class AttributesPanel {
  constructor(app, bodyId) {
    this.app = app; this.vp = app.vp; this.id = bodyId;
    this.tab = "points"; this.filter = ""; this.marked = null;
    this.el = $("attrs"); this.el.hidden = false;
    this.render();
  }

  body() { return this.vp.data && this.vp.data.bodies.find((b) => b.id === this.id); }

  // what each tab lists: {key, name, detail, ref, kind, point?, frame?, select?}
  items() {
    const b = this.body();
    if (!b) return { points: [], edges: [], faces: [] };
    const id = b.id;
    const points = [], seen = new Set();
    for (const a of b.anchors) {
      const ref = `${id}.anchor(${q(a.name)})`;
      seen.add(ref);
      points.push({ key: "a:" + a.name, name: a.name, detail: `anchor ${xyz(a.o)}`, ref, kind: "point", point: a.o, frame: a, group: "Anchors" });
    }
    for (const f of b.faces) {
      for (const s of f.snaps || []) {
        if (seen.has(s.ref)) continue;
        seen.add(s.ref);
        const m = s.ref.match(/\.face\(("(?:[^"\\]|\\.)*")\)\.(\w+)\((\d*)\)$/);
        const name = m ? `${JSON.parse(m[1])} ${m[2] === "anchor" ? "centre" : m[2] + (m[3] ? " " + m[3] : "")}` : s.ref;
        points.push({ key: "s:" + s.ref, name, detail: `${s.kind} ${xyz(s.o)}`, ref: s.ref, kind: "point", point: s.o, frame: s, group: "On faces" });
      }
    }
    const edges = b.edges.map((e) => ({
      key: "e:" + e.label, name: e.label, detail: `${fmt(polyLength(e.p))} mm${e.construction ? " · construction" : ""}`,
      ref: `${id}.edge(${q(e.label)})`, kind: "edge", point: polyMid(e.p),
    }));
    const faces = b.faces.map((f) => ({
      key: "f:" + f.label, name: f.label, detail: `${f.kind} · ${fmt(meshArea(f))} mm²`,
      ref: `${id}.face(${q(f.label)})`, kind: "face", entry: f, point: f.center || null,
    }));
    return { points, edges, faces };
  }

  render() {
    const b = this.body();
    if (!b) { this.close(); this.app.attrs = null; return; }
    const all = this.items();
    const f = this.filter.trim().toLowerCase();
    const list = all[this.tab].filter((it) => !f || it.name.toLowerCase().includes(f) || it.detail.toLowerCase().includes(f) || it.ref.toLowerCase().includes(f));
    const tabs = [["points", "Points"], ["edges", "Edges"], ["faces", "Faces"]];
    let group = null, rows = "";
    for (const it of list) {
      if (it.group && it.group !== group) { group = it.group; rows += `<li class="at-group">${esc(group)}</li>`; }
      rows += `<li data-key="${esc(it.key)}" class="${this.marked === it.key ? "sel" : ""}" title="${esc(this.app.fixRef(it.ref, true))}">`
        + `<span class="nm">${esc(it.name)}</span><span class="dt">${esc(it.detail)}</span>`
        + `<button type="button" class="cp" data-copy="${esc(it.key)}" title="Copy the reference" aria-label="Copy the reference to ${esc(it.name)}">⧉</button></li>`;
    }
    this.el.innerHTML = `<div class="at-head"><b>${esc(this.app.exprFor(b.id, true))}</b><span>${b.sketch ? "sketch" : b.role} · attributes</span>
        <button type="button" class="at-x" aria-label="Close">×</button></div>
      <div class="at-tabs" role="tablist">${tabs.map(([k, l]) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${this.tab === k}">${l} <span>${all[k].length}</span></button>`).join("")}</div>
      <input class="at-filter" placeholder="Filter by name" value="${esc(this.filter)}" aria-label="Filter" spellcheck="false" autocomplete="off">
      <ul class="at-list">${rows || `<li class="empty">${f ? "Nothing matches." : "None."}</li>`}</ul>
      <div class="at-foot">Hover to see it in the view · click to select and copy its reference</div>`;
    this.bind(all[this.tab]);
  }

  bind(list) {
    const byKey = new Map(list.map((it) => [it.key, it]));
    this.el.querySelector(".at-x").addEventListener("click", () => this.app.closeAttrs());
    this.el.querySelectorAll("[data-tab]").forEach((t) => t.addEventListener("click", () => { this.tab = t.dataset.tab; this.unhover(); this.render(); }));
    const filt = this.el.querySelector(".at-filter");
    filt.addEventListener("input", () => {
      this.filter = filt.value;
      const pos = filt.selectionStart;
      this.render();
      const nf = this.el.querySelector(".at-filter"); nf.focus(); nf.setSelectionRange(pos, pos);
    });
    filt.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") { if (filt.value) { this.filter = ""; this.render(); } else this.app.closeAttrs(); } });
    this.el.querySelectorAll(".at-list li[data-key]").forEach((li) => {
      const it = byKey.get(li.dataset.key);
      li.addEventListener("mouseenter", () => this.hover(it));
      li.addEventListener("mouseleave", () => this.unhover());
      li.addEventListener("click", (e) => { if (!e.target.closest("[data-copy]")) this.pick(it); });
    });
    this.el.querySelectorAll("[data-copy]").forEach((btn) => btn.addEventListener("click", () => {
      const it = byKey.get(btn.dataset.copy);
      this.app.copy(this.app.fixRef(it.ref, true));
    }));
  }

  hover(it) {
    if (it.kind === "point") { this.vp.setPointMark(it.frame, false); this.vp.setHover(null); }
    else { this.vp.setHover(`${it.kind}:${it.ref}`); this.vp.setPointMark(this.markedFrame || null, true); }
    this.app.showTip(null);
  }

  unhover() { this.vp.setHover(null); this.vp.setPointMark(this.markedFrame || null, true); }

  pick(it) {
    const ref = this.app.fixRef(it.ref, true);
    this.marked = it.key;
    if (it.kind === "point") {
      this.markedFrame = it.frame;
      this.vp.setPointMark(it.frame, true);
      this.app.copy(ref, false);
      toast(`${ref} copied`);
    } else {
      this.markedFrame = null;
      this.vp.setPointMark(null);
      if (this.app.dialog) { this.app.copy(ref, false); toast(`${ref} copied`); }
      else {
        const s = { kind: it.kind, body: this.id, ref: it.ref, label: it.name, key: `${it.kind}:${it.ref}` };
        if (it.kind === "face") { s.entry = it.entry; s.point = it.point; }
        if (it.kind === "edge") s.point = it.point;
        this.app.unpickStep();
        this.app.selection = [s];
        this.vp.setSelected([s.key]);
        this.vp.showLocalGrid(null);
        this.app.renderSelection();           // copies the reference too
      }
    }
    this.el.querySelectorAll(".at-list li[data-key]").forEach((li) => li.classList.toggle("sel", li.dataset.key === it.key));
  }

  close() { this.vp.setPointMark(null); this.vp.setHover(null); this.el.hidden = true; this.el.innerHTML = ""; }
}
