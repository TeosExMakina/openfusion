// OpenFusion modeling app.
import { $, esc, get, post, q, toast } from "./api.js";
import { Viewport } from "./viewport.js";
import { ViewCube } from "./viewcube.js";
import { Dialog } from "./dialog.js";
import { Sketcher, SKETCH_DRAW, SKETCH_RULES } from "./sketcher.js";
import { SectionView } from "./section.js";
import { BODY_TOOLS, GROUPS, ICONS, IS_MAC, OPS_MOD, OP_TO_TOOL, TOOLS, VIEW_KEYS, VIEW_MOD, keyLabel, viewKeyLabel } from "./tools.js";
import { ScriptEditor } from "./editor.js";
import { ExportPanel, quickExport } from "./exporter.js";
import { ParamsPanel } from "./params.js";

const IDLE_HINT = "Click a face or edge to select it; double-click selects the body. Drag a box to select (→ inside, ← touching). Right-drag orbits, two fingers pan, pinch or wheel zooms.";

class App {
  constructor() {
    this.vp = new Viewport($("view"));
    this.cube = new ViewCube($("viewcube"), this.vp, $("home-view"));
    this.state = null; this.dialog = null; this.filter = "auto"; this.selection = [];
    this.editor = null; this.version = -1; this.exporter = null;
    this.buildToolbar(); this.bindViewport(); this.bindKeys(); this.bindChrome(); this.buildHelp();
    this.vp.onFrame.push(() => {
      const local = this.vp.localFrame && this.vp.localStep;
      const key = local ? "f" + this.vp.localStep : "w" + this.vp.gridStep;
      if (key === this._grid) return;
      this._grid = key;
      $("grid-step").textContent = local ? `Face grid ${fmtStep(this.vp.localStep)} · snaps to its lines`
        : `Grid ${fmtStep(this.vp.gridStep)} · snap ${fmtStep(this.vp.snapStep())}`;
    });
    this.refresh(true);
    setInterval(() => this.poll(), 600);
  }

  // ------------------------------------------------------------------ server state
  async refresh(first = false) { this.setState(await get("/api/state"), first); }

  async poll() {
    try {
      const v = await get("/api/version");
      // a different document always reloads; edits to the same one wait for an open tool to finish
      const otherDoc = String(v.version).split(":")[0] !== String(this.version).split(":")[0];
      if (v.version !== this.version && (!this.dialog || otherDoc)) await this.refresh();
    } catch (e) { this.banner("Lost the connection to openfusion gui. Is it still running?", true); }
  }

  setState(st, first = false) {
    this.version = st.version;
    document.body.classList.toggle("in-app", !!st.app);
    if (st.start) { this.state = st; this.showStart(st); return; }
    $("start").hidden = true;
    if (this.openFile !== st.file) {          // another document: start fresh
      if (this.openFile !== undefined) first = true;
      this.openFile = st.file;
      if (this.dialog) this.closeDialog();
      if (this.exporter) this.closeExport();
      this.selection = []; this.vp.hidden.clear(); this.showRemoved = false;
      this.state = st; this.loadOpacity();
    }
    this.state = st;
    $("file").textContent = st.file.split(/[\\/]/).pop(); $("file").title = st.app ? `${st.file}\nClick for the File menu` : st.file;
    document.title = `${$("file").textContent} · OpenFusion`;
    if (!this.dialog) this.vp.setScene(st.scene, { keepCamera: !first });
    this.renderBodies(); this.renderParams(); this.renderTimeline();
    if (this.paramsPanel) this.paramsPanel.render();
    const undoKey = IS_MAC ? "⌘Z" : "Ctrl+Z";
    $("undo").disabled = !st.undo; $("undo").title = st.undo ? `Undo ${st.undo} (${undoKey})` : "Nothing to undo";
    $("redo").disabled = !st.redo; $("redo").title = st.redo ? `Redo ${st.redo} (⇧${undoKey})` : "Nothing to redo";
    this.setPerf(st.ms);
    const d = st.desc, sc = st.scene;
    if (!d.ok) this.banner(`The script has a syntax error (${d.error}). Showing the last version that worked until you fix it in the script pane.`, true);
    else if (sc.script_error) this.banner(`The script stopped at line ${sc.script_error_line}: ${sc.script_error}`, true);
    else if (!d.gui) this.banner(`Code-only mode: line ${d.blockers.join(", ")} uses code the timeline cannot show (a loop or function). New features still work; edit existing ones in the script.`);
    else this.banner(null);
    if (this.editor) this.editor.load(st.text, st.hash);
    const ids = new Set(sc.bodies.map((b) => b.id));
    this.selection = this.selection.filter((s) => ids.has(s.body) && (s.kind === "body" || this.vp.faceMeshes.some((m) => m.userData.ref === s.ref) || this.vp.edgeLines.some((l) => l.userData.ref === s.ref)));
    this.vp.setSelected(this.selection.map((s) => s.key));
    this.renderSelection();
    if (this.exporter) this.exporter.load();
  }

  // ------------------------------------------------------------------ documents (desktop app)
  showStart(st) {
    const el = $("start");
    document.title = "OpenFusion";
    if (this.dialog) this.closeDialog();
    if (this.exporter) this.closeExport();
    this.openFile = null;
    const mod = IS_MAC ? "⌘" : "Ctrl+";
    const recent = (st.recent || []).map((r) => `<li><button type="button" data-open="${esc(r.path)}" title="${esc(r.path)}">
        <span class="st-nm">${esc(r.name)}</span><span class="st-meta">${esc(r.folder)} · ${esc(r.modified)}</span></button></li>`).join("");
    const examples = (st.examples || []).map((x) => `<li><button type="button" data-example="${esc(x.name)}">
        <span class="st-nm">${esc(x.name)}</span><span class="st-meta">${esc(x.title)}</span></button></li>`).join("");
    el.innerHTML = `<div class="st-card" role="dialog" aria-label="Start">
      <div class="st-head"><svg viewBox="0 0 32 32" width="34" height="34" aria-hidden="true"><path d="M16 3 28 10v12L16 29 4 22V10z" fill="var(--accent)"/><path d="M16 3 28 10 16 17 4 10z" fill="color-mix(in srgb, var(--accent) 70%, white)"/></svg>
        <div><b>OpenFusion</b><span>Parametric parts for 3D printing</span></div></div>
      <div class="st-actions"><button type="button" class="primary" id="st-new">New part <kbd>${mod}N</kbd></button>
        <button type="button" id="st-open">Open… <kbd>${mod}O</kbd></button></div>
      <div class="st-cols">
        <section><h2>Recent</h2>${recent ? `<ul>${recent}</ul>` : `<p class="st-empty">Parts you open show up here.</p>`}</section>
        <section><h2>Examples</h2>${examples ? `<ul>${examples}</ul>` : `<p class="st-empty">No examples installed.</p>`}
          <p class="st-note">Examples open as a copy in Documents/OpenFusion.</p></section>
      </div></div>`;
    el.hidden = false;
    $("st-new").onclick = () => this.fileAction("new");
    $("st-open").onclick = () => this.fileAction("open");
    el.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => this.fileAction("open", { path: b.dataset.open })));
    el.querySelectorAll("[data-example]").forEach((b) => (b.onclick = () => this.fileAction("example", { name: b.dataset.example })));
  }

  async fileAction(act, body = {}) {
    this.closeMenus();
    try {
      const out = await post("/api/file/" + act, body);
      if (out.cancelled) return out;
      await this.refresh();
      return out;
    } catch (e) { toast(e.message, true); return null; }
  }

  async fileMenu() {
    if (!this.state || !this.state.app) return;
    let recent = [], examples = [];
    try { const r = await post("/api/file/recent", {}); recent = r.recent; examples = r.examples; } catch (e) { /* offline */ }
    const mod = IS_MAC ? "⌘" : "Ctrl+";
    const b = $("file").getBoundingClientRect();
    this.contextMenu("File", [
      [`New part…   ${mod}N`, () => this.fileAction("new")],
      [`Open…   ${mod}O`, () => this.fileAction("open")],
      null,
      ...recent.filter((r) => r.path !== this.openFile).slice(0, 6).map((r) => [`Open ${r.name}`, () => this.fileAction("open", { path: r.path })]),
      null,
      ...examples.map((x) => [`Example: ${x.name}`, () => this.fileAction("example", { name: x.name })]),
      null,
      ["Show in Finder", () => this.fileAction("reveal")],
      [`Export…   ${keyLabel("Shift+E")}`, () => this.openExport()],
      ["Close part", () => this.fileAction("close")],
    ], b.left, b.bottom + 4);
  }

  async action(path, body) {
    try {
      const out = await post(path, body);
      if (out.state) this.setState(out.state);
      return out;
    } catch (e) { toast(e.message, true); throw e; }
  }

  // Body ids become script expressions: pattern copies are written group[k].
  exprFor(id, quiet = false) {
    const e = this.state && this.state.exprs ? this.state.exprs[id] : id;
    if (e === null && !quiet) { toast(`${id} comes from a statement without a name. Right-click it in the timeline and rename it first.`, true); return id; }
    return e || id;
  }

  fixRef(ref, quiet = false) {
    const m = String(ref).match(/^([A-Za-z_][A-Za-z0-9_]*)(.*)$/s);
    if (!m || !this.state || !this.state.exprs || !(m[1] in this.state.exprs)) return ref;
    return this.exprFor(m[1], quiet) + m[2];
  }

  featureOp(name) { return this.state.desc.features.find((f) => f.name === name)?.op; }
  featureArgs(name) { return this.state.desc.features.find((f) => f.name === name)?.args || {}; }

  banner(msg, err = false) { const b = $("banner"); b.hidden = !msg; if (msg) { b.textContent = msg; b.className = "banner" + (err ? " err" : ""); } }
  setPerf(ms) { if (ms) $("perf").textContent = `build ${ms.build} ms · mesh ${ms.mesh} ms`; }
  setHint(text) { $("hover").textContent = text || ""; }

  showTip(text, ev) {
    const tip = $("tip");
    if (!text) { tip.hidden = true; return; }
    tip.textContent = text; tip.hidden = false;
    if (ev) { const r = $("view").getBoundingClientRect(); tip.style.left = ev.clientX - r.left + "px"; tip.style.top = ev.clientY - r.top + "px"; }
    $("hover").textContent = text;
  }

  // ------------------------------------------------------------------ toolbar
  buildToolbar() {
    const nav = $("tools");
    const tip = (t) => `${t.label}${t.key ? `  (${keyLabel(t.key)})` : ""}${t.help ? "\n" + t.help : ""}`;
    for (const g of GROUPS) {
      const grp = document.createElement("div"); grp.className = "rgroup";
      const row = document.createElement("div"); row.className = "rbtns";
      for (const id of g.main) {
        const t = TOOLS[id];
        const b = document.createElement("button"); b.className = "rbtn"; b.dataset.tool = id; b.title = tip(t);
        b.innerHTML = `${ICONS[id] || ""}<span>${esc(t.label)}</span>`;
        b.addEventListener("click", () => this.openTool(id));
        row.append(b);
      }
      const wrap = document.createElement("div"); wrap.className = "menu-wrap";
      const lab = document.createElement("button"); lab.className = "rlabel"; lab.innerHTML = `${esc(g.label)} <span aria-hidden="true">▾</span>`;
      lab.setAttribute("aria-haspopup", "true");
      const menu = document.createElement("div"); menu.className = "menu"; menu.hidden = true;
      for (const id of g.tools) {
        const t = TOOLS[id];
        const mb = document.createElement("button"); mb.dataset.tool = id; mb.title = t.help || "";
        mb.innerHTML = `<span class="mi">${ICONS[id] || ""}${esc(t.label)}</span><kbd>${keyLabel(t.key)}</kbd>`;
        mb.addEventListener("click", () => { menu.hidden = true; this.openTool(id); });
        menu.append(mb);
      }
      lab.addEventListener("click", (e) => {
        e.stopPropagation(); const open = menu.hidden; this.closeMenus(); menu.hidden = !open;
        if (open) placeMenu(menu, lab);
      });
      wrap.append(lab, menu);
      grp.append(row, wrap);
      nav.append(grp);
    }
    document.addEventListener("click", () => this.closeMenus());
    window.addEventListener("resize", () => this.closeMenus());
    nav.addEventListener("scroll", () => this.closeMenus());
  }

  closeMenus() { document.querySelectorAll(".menu").forEach((m) => (m.hidden = true)); $("ctx").hidden = true; }

  // Selection feeds the tool: body tools take the bodies of whatever is selected, edge tools
  // take edges (a face counts as all its edges), Push/Pull takes a face, primitives build on it.
  openTool(id, opts = {}) {
    this.closeMenus();
    if (this.dialog) this.closeDialog();
    if (this.exporter) this.closeExport();
    const t = TOOLS[id];
    if (t.section) {
      document.querySelectorAll("[data-tool]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tool === id)));
      this.dialog = new SectionView(this);
      return;
    }
    if (t.sketch) {
      const face = this.selection.find((s) => s.kind === "face" && s.entry && s.entry.kind === "plane");
      if (face && !opts.edit) {
        const snaps = this.vp.snapCandidates({ face: face.entry, body: face.body, label: face.label, point: face.point });
        const c = snaps.find((x) => x.kind === "center") || snaps[0];
        opts.plane = { base: { ref: this.fixRef(c.ref, true), frame: c.frame } };
      }
      document.querySelectorAll("[data-tool]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tool === id)));
      this.dialog = new Sketcher(this, opts);
      return;
    }
    if (!opts.edit) {
      const preset = {}, frames = {};
      const sel = this.selection;
      const bodies = [...new Set(sel.map((s) => s.body))];
      const fields = t.fields || [];
      const has = (key) => fields.some((f) => f.key === key);
      const bodyField = fields.find((f) => f.kind === "body" && !f.hidden && !f.noAuto);
      if (bodyField && bodies.length && (BODY_TOOLS.has(id) || sel.some((s) => s.kind === "body"))) preset[bodyField.key] = this.exprFor(bodies[0], true);
      if (has("tools") && bodies.length > 1) preset.tools = `[${bodies.slice(1).map((b) => this.exprFor(b, true)).join(", ")}]`;
      if (has("edges")) {
        const first = sel.find((s) => s.kind === "edge" || s.kind === "face");
        const mine = sel.filter((s) => (s.kind === "edge" || s.kind === "face") && first && s.body === first.body);
        if (mine.length) preset.edges = `[${mine.map((s) => this.fixRef(s.ref, true) + (s.kind === "face" ? ".edges()" : "")).join(", ")}]`;
      }
      const face = sel.find((s) => s.kind === "face");
      if (has("face") && face) preset.face = this.fixRef(face.ref, true);
      // selected faces (or profiles) feed face-list fields; a single-face field takes one only
      // when the tool has no list (Draft's faces come from the selection, its plane is picked)
      const isProfile = (id) => this.vp.body(id)?.kind === "profile";
      const faceText = (s) => isProfile(s.body) ? this.exprFor(s.body, true) : this.fixRef(s.ref, true);
      const faceSel = sel.filter((s) => s.kind === "face" || (s.kind === "body" && isProfile(s.body)));
      const listField = fields.find((f) => f.kind === "faces" && !f.hidden);
      if (listField && faceSel.length) {
        const same = listField.ordered ? faceSel : faceSel.filter((s) => s.body === faceSel[0].body);
        preset[listField.key] = `[${same.map(faceText).join(", ")}]`;
        if (id === "shell" && !isProfile(same[0].body)) preset.body = this.exprFor(same[0].body, true);
      }
      const oneFace = fields.find((f) => f.kind === "face" && !f.hidden && f.key !== "face");
      if (oneFace && !listField && faceSel.length) preset[oneFace.key] = faceText(faceSel[0]);
      if ((t.size || t.polyline) && face && face.entry && face.entry.kind === "plane") {
        const snaps = this.vp.snapCandidates({ face: face.entry, body: face.body, label: face.label, point: face.point }).map((c) => ({ ...c, ref: this.fixRef(c.ref, true) }));
        const center = snaps.find((c) => c.kind === "center") || snaps[0];
        opts.plane = { base: center, snaps, bed: false };
      }
      if (id === "place" && face && face.entry) {
        const snaps = this.vp.snapCandidates({ face: face.entry, body: face.body, label: face.label, point: face.point });
        preset.source = this.fixRef(snaps[0].ref, true); frames.source = snaps[0].frame;
      }
      opts.preset = preset; opts.frames = frames;
    }
    document.querySelectorAll("[data-tool]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tool === id)));
    this.dialog = new Dialog(this, id, opts);
  }

  editFeature(name) {
    const f = this.state.desc.features.find((x) => x.name === name);
    if (!f) return;
    if (!this.state.desc.gui) { toast("Code-only mode: edit this feature in the script", true); this.toggleEditor(true); this.editor.showLines(f.line, f.end_line); return; }
    const tool = f.op === "move" && f.args.to ? "move_p2p"
      : f.op === "profile" ? (f.args.points ? "profile_line" : f.args.r ? "profile_circle" : "profile_rect")
      : f.op === "plane" ? (f.args.points ? null : f.args.mid ? "midplane" : f.args.about && !/^["']?[XY]["']?$/.test(f.args.about) ? "plane_edge" : "plane")
      : f.op === "axis" && f.args.points ? null
      : OP_TO_TOOL[f.op];
    if (!tool) { this.toggleEditor(true); this.editor.showLines(f.line, f.end_line); return; }
    this.openTool(tool, { edit: name, args: f.args });
  }

  closeDialog(applied = false) {
    if (!this.dialog) return;
    this.dialog.close(); this.dialog = null;
    document.querySelectorAll("[data-tool]").forEach((b) => b.setAttribute("aria-pressed", "false"));
    this.setHint(IDLE_HINT);
    this.showTip(null);
    if (!applied && this.state) { this.vp.setScene(this.state.scene); this.renderTimeline(); }
    if (applied) this.selection = [];
    this.vp.setSelected(this.selection.map((s) => s.key));
    this.renderSelection();
  }

  // ------------------------------------------------------------------ viewport selection
  pickSel(ev) {
    const f = this.filter;
    const hit = f === "auto" ? this.vp.pickAuto(ev) : this.vp.pick(ev, f);
    if (!hit) return null;
    if (hit.kind === "body") return { kind: "body", body: hit.body, ref: hit.body, key: "body:" + hit.body };
    return { kind: hit.kind, body: hit.body, ref: hit.ref, label: hit.label, entry: hit.face, point: hit.point, key: hit.kind + ":" + hit.ref };
  }

  bindViewport() {
    const canvas = $("view");
    let down = null;
    canvas.addEventListener("pointermove", (ev) => {
      if (this.dialog && this.dialog.hover(ev)) return;
      if (this.exporter) {
        const hit = this.vp.pick(ev, "body");
        this.vp.setHover(hit ? "body:" + hit.body : null);
        this.showTip(hit ? `${hit.body}: click to ${this.exporter.chosen.has(hit.body) ? "leave out of" : "add to"} the export` : null, ev);
        return;
      }
      const s = this.pickSel(ev);
      if (!s) { this.vp.setHover(null); this.showTip(null); this.setHint(this.dialog ? "" : IDLE_HINT); return; }
      this.vp.setHover(s.key);
      this.showTip(s.kind === "body" ? this.exprFor(s.body, true) : this.fixRef(s.ref, true), ev);
    });
    canvas.addEventListener("pointerleave", () => { this.vp.setHover(null); this.showTip(null); });
    // Left: click picks, drag draws a selection box. Right: drag orbits (the orbit controls), a
    // click without dragging opens the body menu. Middle drag pans.
    canvas.addEventListener("pointerdown", (ev) => {
      down = { x: ev.clientX, y: ev.clientY, button: ev.button, box: ev.button === 0 && !ev.defaultPrevented && this.boxAllowed() };
      if (down.box) canvas.setPointerCapture(ev.pointerId);
    });
    canvas.addEventListener("pointermove", (ev) => {
      if (!down || !down.box || !(ev.buttons & 1)) return;
      if (!this.box && Math.hypot(ev.clientX - down.x, ev.clientY - down.y) <= 4) return;
      this.box = { x0: down.x, y0: down.y, x1: ev.clientX, y1: ev.clientY };
      this.drawBox(this.box);
    });
    canvas.addEventListener("pointerup", (ev) => {
      if (!down) return;
      const d = down; down = null;
      if (this.box) { const b = this.box; this.box = null; this.drawBox(null); this.boxSelect(b, ev.shiftKey || ev.metaKey); return; }
      if (Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 4) return;
      if (ev.button === 2) {           // right click without a drag: the body menu
        const hit = this.vp.pick(ev, "body");
        if (hit && !this.dialog) this.bodyMenu(hit.body, ev.clientX, ev.clientY);
        return;
      }
      if (ev.button !== 0) return;
      if (this.exporter) { this.exporter.viewClick(ev); return; }
      if (this.dialog) { if (ev.pointerType !== "mouse") this.dialog.hover(ev); this.dialog.click(ev); return; }
      const s = this.pickSel(ev);
      if (!s) this.selection = [];
      else if (ev.shiftKey || ev.metaKey) this.selection = this.selection.some((x) => x.key === s.key) ? this.selection.filter((x) => x.key !== s.key) : [...this.selection, s];
      else this.selection = [s];
      this.vp.setSelected(this.selection.map((x) => x.key));
      this.vp.showLocalGrid(s && s.kind === "face" && s.entry && s.entry.kind === "plane" ? this.vp.snapCandidates({ face: s.entry, body: s.body, label: s.label, point: s.point })[0].frame : null);
      this.renderSelection();
    });
    canvas.addEventListener("dblclick", (ev) => {
      if (this.dialog) return;
      const hit = this.vp.pick(ev, "body");
      if (!hit) return;
      this.selection = [{ kind: "body", body: hit.body, ref: hit.body, key: "body:" + hit.body }];
      this.vp.setSelected(this.selection.map((x) => x.key)); this.renderSelection();
    });
    canvas.addEventListener("contextmenu", (ev) => ev.preventDefault());   // opened on release, unless it was an orbit
  }

  // A box makes sense when nothing else owns a left drag: no export panel, and in a tool only
  // while it is collecting bodies, faces or edges (not while placing or sizing).
  boxAllowed() {
    if (this.exporter) return true;
    if (!this.dialog) return true;
    return this.dialog.boxKind() !== null;
  }

  drawBox(b) {
    const el = $("selbox");
    if (!b) { el.hidden = true; return; }
    const r = $("view").getBoundingClientRect();
    const crossing = b.x1 < b.x0;
    el.hidden = false; el.className = "selbox" + (crossing ? " crossing" : "");
    el.style.left = Math.min(b.x0, b.x1) - r.left + "px"; el.style.top = Math.min(b.y0, b.y1) - r.top + "px";
    el.style.width = Math.abs(b.x1 - b.x0) + "px"; el.style.height = Math.abs(b.y1 - b.y0) + "px";
    this.setHint(crossing ? "Crossing: selects everything the box touches" : "Window: selects what is entirely inside the box");
  }

  boxSelect(b, add) {
    const rect = { x0: Math.min(b.x0, b.x1), y0: Math.min(b.y0, b.y1), x1: Math.max(b.x0, b.x1), y1: Math.max(b.y0, b.y1) };
    const mode = b.x1 < b.x0 ? "crossing" : "window";
    if (this.exporter) { for (const h of this.vp.boxPick(rect, mode, "body")) this.exporter.toggle(h.body, true); return; }
    if (this.dialog) { this.dialog.boxPick(this.vp.boxPick(rect, mode, this.dialog.boxKind())); return; }
    const kind = this.filter === "face" ? "face" : this.filter === "edge" ? "edge" : "body";
    const hits = this.vp.boxPick(rect, mode, kind);
    const keys = new Set(add ? this.selection.map((x) => x.key) : []);
    this.selection = [...(add ? this.selection : []), ...hits.filter((h) => !keys.has(h.key))];
    this.vp.setSelected(this.selection.map((x) => x.key));
    this.vp.showLocalGrid(null);
    this.renderSelection();
    this.setHint(hits.length ? `${hits.length} selected` : "Nothing in the box");
  }

  // ------------------------------------------------------------------ selection readout
  selectionItems() {
    return this.selection.map((s) => {
      if (s.kind === "body") return { kind: "body", body: s.body, name: s.body };
      if (s.kind === "face") {
        const m = this.vp.faceMeshes.find((x) => x.userData.ref === s.ref);
        return { kind: "face", body: s.body, label: s.label || m?.userData.label, name: s.label || m?.userData.label };
      }
      const l = this.vp.edgeLines.find((x) => x.userData.ref === s.ref);
      const pts = l?.userData.points || [];
      const k = Math.floor(pts.length / 6) * 3;
      return { kind: "edge", body: s.body, label: s.label || l?.userData.label, name: s.label || l?.userData.label,
        point: s.point || (pts.length >= 3 ? [pts[k], pts[k + 1], pts[k + 2]] : null) };
    });
  }

  updateSelInfo() {
    clearTimeout(this._selTimer);
    const box = $("selinfo");
    if (!this.selection.length) { box.hidden = true; return; }
    const items = this.selectionItems();
    const key = JSON.stringify(items) + this.version;
    this._selTimer = setTimeout(async () => {
      try {
        const out = await post("/api/selinfo", { items });
        if (JSON.stringify(this.selectionItems()) + this.version !== key) return;     // selection moved on
        this.renderSelInfo(out);
      } catch (e) { box.hidden = true; }
    }, 120);
  }

  renderSelInfo(out) {
    const box = $("selinfo");
    const num = (v, unit) => typeof v === "number" ? `${v >= 1000 ? v.toFixed(1) : v.toFixed(3)} ${unit}` : `${v} ${unit}`;
    const table = (rows) => `<table>${rows.map(([k, v, u]) => `<tr><td>${esc(k)}</td><td class="v">${esc(num(v, u))}</td></tr>`).join("")}</table>`;
    let html = "";
    const items = out.items || [];
    if (items.length <= 3) {
      for (const it of items) html += `<h3>${esc(it.what)}<span class="nm">${esc(it.name || "")}</span></h3>${table(it.rows || [])}`;
    } else {
      html += `<h3>${items.length} selected</h3>`;
    }
    if (out.pair && out.pair.rows.length) html += `<h3>${items.length === 2 ? "Between them" : "All together"}</h3>${table(out.pair.rows)}`;
    box.innerHTML = html;
    box.hidden = !html;
  }

  renderSelection() {
    this.updateSelInfo();
    const n = this.selection.length;
    $("sel").textContent = n ? `Selected: ${this.selection.map((s) => s.kind === "body" ? this.exprFor(s.body, true) : this.fixRef(s.ref, true)).join(", ")}` : "";
    document.querySelectorAll(".bodies li").forEach((li) => li.classList.toggle("sel", this.selection.some((s) => s.kind === "body" && s.body === li.dataset.body)));
    if (n === 1) this.copy(this.selection[0].kind === "body" ? this.exprFor(this.selection[0].body, true) : this.fixRef(this.selection[0].ref, true), false);
  }

  copy(text, announce = true) {
    try { navigator.clipboard.writeText(text).then(() => announce && toast("Copied " + text), () => {}); } catch (e) { /* clipboard unavailable */ }
  }

  // ------------------------------------------------------------------ browser
  renderBodies() {
    const ul = $("bodies"); ul.innerHTML = "";
    const list = this.state.scene.bodies;
    if (!list.length) { ul.innerHTML = `<li class="empty">No bodies yet. Use Create to add one.</li>`; return; }
    const live = list.filter((b) => b.role !== "removed"), gone = list.filter((b) => b.role === "removed");
    for (const b of live) ul.append(this.bodyRow(b));
    if (gone.length) {
      const li = document.createElement("li"); li.className = "group";
      li.innerHTML = `<button class="eye">${this.showRemoved ? "▾" : "▸"}</button><span class="nm">Removed (${gone.length})</span><span></span>`;
      li.addEventListener("click", () => { this.showRemoved = !this.showRemoved; this.renderBodies(); });
      li.title = "Bodies consumed by a combine or removed. Their design anchors can still be used.";
      ul.append(li);
      if (this.showRemoved) for (const b of gone) ul.append(this.bodyRow(b));
    }
  }

  bodyRow(b) {
    const li = document.createElement("li"); li.dataset.body = b.id;
    const hidden = this.vp.hidden.has(b.id);
    li.innerHTML = `<button class="eye" title="${hidden ? "Show" : "Hide"}" aria-pressed="${!hidden}">${hidden ? "○" : "●"}</button>` +
      `<span class="nm">${esc(b.id)}</span><span class="role ${b.role}">${b.role}${b.mirrored ? " · mirrored" : ""}</span>`;
    li.querySelector(".eye").addEventListener("click", (e) => {
      e.stopPropagation(); hidden ? this.vp.hidden.delete(b.id) : this.vp.hidden.add(b.id);
      this.vp.setScene(this.state.scene); this.renderBodies();
    });
    li.addEventListener("click", () => {
      if (this.exporter && this.exporter.toggle(b.id)) return;
      if (this.dialog && this.dialog.takeBody(b.id)) return;
      this.selection = [{ kind: "body", body: b.id, ref: b.id, key: "body:" + b.id }];
      this.vp.setSelected(this.selection.map((s) => s.key)); this.renderSelection();
    });
    li.addEventListener("mouseenter", () => this.vp.setHover("body:" + b.id));
    li.addEventListener("mouseleave", () => this.vp.setHover(null));
    li.addEventListener("contextmenu", (e) => { e.preventDefault(); this.bodyMenu(b.id, e.clientX, e.clientY); });
    li.title = `${b.id}: ${(b.volume / 1000).toFixed(2)} cm³, built by ${b.history.join(", ")}`;
    return li;
  }

  bodyMenu(id, x, y) {
    const b = this.vp.body(id); if (!b) return;
    const creator = b.history[0];
    const sel = () => { this.selection = [{ kind: "body", body: id, ref: id, key: "body:" + id }]; };
    // right-clicking one of several selected bodies exports all of them
    const picked = [...new Set(this.selection.map((s) => s.body))];
    const ids = picked.length > 1 && picked.includes(id) ? picked : [id];
    const what = ids.length > 1 ? `${ids.length} selected bodies` : id;
    const exportable = b.role !== "removed";
    this.contextMenu(id, [
      [`Edit ${creator}`, () => this.editFeature(creator)],
      ["Move", () => { sel(); this.openTool("move"); }],
      ["Place", () => this.openTool("place")],
      ["Place on bed", () => { sel(); this.openTool("place_on_bed"); }],
      null,
      b.role === "reference" ? ["Make solid", () => this.insert("set_role", { body: id, role: q("solid") })]
        : ["Make reference (ghost)", () => this.insert("set_role", { body: id, role: q("reference") })],
      b.role !== "removed" ? ["Remove from here on", () => this.insert("remove", { body: id })] : null,
      null,
      { opacity: ids },
      null,
      exportable ? [`Export ${what} as STL`, () => quickExport("stl", ids)] : null,
      exportable ? [`Export ${what} as 3MF`, () => quickExport("3mf", ids)] : null,
      exportable ? [`Export ${what} as STEP`, () => quickExport("step", ids)] : null,
      exportable ? [`Export ${what}…`, () => this.openExport(ids)] : null,
      null,
      [`Delete ${creator}…`, () => this.deleteFeature(creator), true],
    ], x, y);
  }

  // ------------------------------------------------------------------ export
  openExport(ids) {
    this.closeMenus();
    if (this.dialog) this.closeDialog();
    if (this.exporter) this.exporter.close();
    if (ids === undefined) {
      const picked = [...new Set(this.selection.map((s) => s.body))];
      ids = picked.length ? picked : null;
    }
    this.showTip(null);
    this.exporter = new ExportPanel(this, ids);
    $("export-btn").setAttribute("aria-pressed", "true");
  }

  openParams() {
    this.closeMenus();
    if (this.state?.start) return;
    if (this.paramsPanel) return;
    this.paramsPanel = new ParamsPanel(this);
    $("params-btn").setAttribute("aria-pressed", "true");
  }

  closeParams() {
    if (!this.paramsPanel) return;
    this.paramsPanel.close(); this.paramsPanel = null;
    $("params-btn").setAttribute("aria-pressed", "false");
  }

  closeExport() {
    if (!this.exporter) return;
    this.exporter.close(); this.exporter = null;
    this.showTip(null); this.setHint(IDLE_HINT);
    $("export-btn").setAttribute("aria-pressed", "false");
  }

  async insert(op, args) {
    try { const out = await this.action("/api/apply", { op, args }); toast(`Added ${out.name}`); } catch (e) { /* toasted */ }
  }

  contextMenu(title, items, x, y) {
    const m = $("ctx"); m.innerHTML = `<div class="hd">${esc(title)}</div>`;
    for (const it of items) {
      if (it === null) { if (m.lastChild && m.lastChild.tagName !== "HR" && !m.lastChild.classList.contains("hd")) m.append(document.createElement("hr")); continue; }
      if (!it) continue;
      if (it.opacity) { m.append(this.opacityRow(it.opacity)); continue; }
      const b = document.createElement("button"); b.textContent = it[0]; if (it[2]) b.className = "danger";
      b.addEventListener("click", (e) => { e.stopPropagation(); m.hidden = true; it[1](); });
      m.append(b);
    }
    if (m.lastChild && m.lastChild.tagName === "HR") m.lastChild.remove();
    m.hidden = false;
    const r = m.getBoundingClientRect();
    m.style.left = Math.min(x, window.innerWidth - r.width - 8) + "px";
    m.style.top = Math.min(y, window.innerHeight - r.height - 8) + "px";
  }

  // "Opacity" slider row for the body menu: live, remembered per part in this browser
  opacityRow(ids) {
    const cur = Math.round(100 * (this.vp.opacity.get(ids[0]) ?? 1));
    const row = document.createElement("div"); row.className = "ctx-opacity";
    row.innerHTML = `<span>Opacity</span><input type="range" min="5" max="100" step="5" value="${cur}" aria-label="Opacity"><b>${cur}%</b>`;
    const inp = row.querySelector("input"), out = row.querySelector("b");
    row.addEventListener("click", (e) => e.stopPropagation());     // keep the menu open while sliding
    inp.addEventListener("input", () => {
      out.textContent = inp.value + "%";
      for (const id of ids) this.vp.setBodyOpacity(id, inp.value / 100);
      this.saveOpacity();
    });
    return row;
  }

  opacityKey() { return "of-opacity:" + (this.state?.file || ""); }

  saveOpacity() {
    try { localStorage.setItem(this.opacityKey(), JSON.stringify(Object.fromEntries(this.vp.opacity))); } catch (e) { /* storage unavailable */ }
  }

  loadOpacity() {
    this.vp.opacity = new Map();
    try { for (const [k, v] of Object.entries(JSON.parse(localStorage.getItem(this.opacityKey()) || "{}"))) this.vp.opacity.set(k, +v); } catch (e) { /* none saved */ }
  }

  renderParams() {
    const tb = $("params"); tb.innerHTML = "";
    for (const [k, v] of Object.entries(this.state.desc.params || {})) {
      const tr = document.createElement("tr");
      const info = (this.state.desc.param_info || []).find((x) => x.name === k);
      if (info && info.comment) tr.title = info.comment;
      tr.innerHTML = `<td>${esc(k)}</td><td><input id="p-${esc(k)}" value="${esc(v)}" aria-label="${esc(k)}" autocomplete="off" spellcheck="false"></td>` +
        `<td><button class="x" title="Delete ${esc(k)}">×</button></td>`;
      const inp = tr.querySelector("input");
      inp.addEventListener("change", async () => {
        try { await this.action("/api/param", { name: k, value: inp.value.trim() }); inp.classList.remove("bad"); }
        catch (e) { inp.classList.add("bad"); }
      });
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") inp.blur(); });
      tr.querySelector(".x").addEventListener("click", () => this.action("/api/param", { name: k, delete: true }).catch(() => {}));
      tb.append(tr);
    }
  }

  // ------------------------------------------------------------------ timeline
  renderTimeline(previewName = null, previewResult = null) {
    const ol = $("timeline"); ol.innerHTML = "";
    const res = Object.fromEntries((this.state.scene.timeline || []).map((r) => [r.name, r]));
    const feats = this.state.desc.features;
    const marker = this.state.marker;
    let after = false;
    const markerEl = () => {
      const m = document.createElement("li"); m.className = "marker"; m.title = "Timeline marker: new features go here. Click to move it to the end.";
      m.addEventListener("click", () => this.action("/api/marker", { name: null }).catch(() => {}));
      return m;
    };
    if (!feats.length) ol.innerHTML = `<li class="end">Your features appear here, in order.</li>`;
    for (const f of feats) {
      const r = res[f.name];
      const li = document.createElement("li");
      li.className = "chip " + (r ? r.status : "") + (after ? " after-marker" : "") + (this.dialog && this.dialog.edit === f.name ? " editing" : "");
      li.dataset.name = f.name;
      li.innerHTML = `<span class="op">${esc(f.op.replace("_", " "))}</span><span class="nm">${esc(f.name)}</span>`;
      const msg = r ? (r.message || r.warnings.join("; ")) : "after the marker, not built";
      li.title = `${f.name}: ${f.op}, line ${f.line}${r ? ` · ${r.status}${r.cached ? " (cached)" : ` · ${r.ms.toFixed(1)} ms`}` : ""}${msg ? "\n" + msg : ""}\nDouble-click to edit, right-click for more.`;
      li.addEventListener("click", () => {
        if (this.dialog && (f.op === "pattern_rect" || f.op === "pattern_circ") && this.dialog.takeGroup(f.name)) return;
        if (this.dialog && f.creates && this.dialog.takeBody(f.name)) return;
        const ids = this.state.scene.bodies.filter((b) => b.history.includes(f.name)).map((b) => ({ kind: "body", body: b.id, ref: b.id, key: "body:" + b.id }));
        this.selection = ids; this.vp.setSelected(ids.map((s) => s.key)); this.renderSelection();
        if (this.editor && !$("editor-pane").hidden) this.editor.showLines(f.line, f.end_line);
      });
      li.addEventListener("dblclick", () => this.editFeature(f.name));
      li.addEventListener("contextmenu", (e) => { e.preventDefault(); this.featureMenu(f, e.clientX, e.clientY); });
      ol.append(li);
      if (f.name === marker) { ol.append(markerEl()); after = true; if (previewName) ol.append(this.previewChip(previewName, previewResult)); }
    }
    if (previewName && !marker) ol.append(this.previewChip(previewName, previewResult));
    ol.scrollLeft = ol.scrollWidth;
  }

  previewChip(name, r) {
    const li = document.createElement("li");
    li.className = "chip preview " + (r ? r.status : "");
    li.innerHTML = `<span class="op">new</span><span class="nm">${esc(name)}</span>`;
    return li;
  }

  previewTimeline(name, result) { this.renderTimeline(this.dialog && this.dialog.edit ? null : name, result); }

  featureMenu(f, x, y) {
    const gui = this.state.desc.gui;
    this.contextMenu(f.name, [
      ["Edit", () => this.editFeature(f.name)],
      gui ? ["Rename…", () => this.rename(f.name)] : undefined,
      gui ? ["Roll back to here", () => this.action("/api/marker", { name: f.name }).catch(() => {})] : undefined,
      ["Show in script", () => { this.toggleEditor(true); this.editor.showLines(f.line, f.end_line); }],
      null,
      gui ? ["Delete…", () => this.deleteFeature(f.name), true] : undefined,
    ], x, y);
  }

  rename(name) {
    const chip = document.querySelector(`.chip[data-name="${CSS.escape(name)}"] .nm`);
    if (!chip) return;
    const inp = document.createElement("input"); inp.value = name; inp.style.width = "90px";
    chip.replaceWith(inp); inp.focus(); inp.select();
    const done = async (ok) => {
      inp.removeEventListener("blur", onBlur);
      if (ok && inp.value.trim() && inp.value.trim() !== name) {
        try { await this.action("/api/rename", { old: name, new: inp.value.trim() }); toast(`Renamed to ${inp.value.trim()}`); return; } catch (e) { /* toasted */ }
      }
      this.renderTimeline();
    };
    const onBlur = () => done(true);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); e.stopPropagation(); });
    inp.addEventListener("blur", onBlur);
  }

  async deleteFeature(name) {
    let chk;
    try { chk = await post("/api/delete", { name, mode: "check" }); } catch (e) { return toast(e.message, true); }
    const deps = chk.dependents;
    if (!deps.length) { await this.action("/api/delete", { name, mode: "delete" }).catch(() => {}); return toast(`Deleted ${name}`); }
    const r = $("view").getBoundingClientRect();
    this.contextMenu(`${name} is used by ${deps.join(", ")}`, [
      chk.can_remove ? ["Remove instead (keeps everything built from it)", () => this.action("/api/delete", { name, mode: "remove" }).then(() => toast(`${name} removed from here on`)).catch(() => {})] : undefined,
      [`Delete ${name} and ${deps.length} dependent${deps.length > 1 ? "s" : ""}`, () => this.action("/api/delete", { name, mode: "cascade" }).then(() => toast(`Deleted ${name} and ${deps.join(", ")}`)).catch(() => {}), true],
      ["Cancel", () => {}],
    ], r.left + r.width / 2 - 150, r.top + 80);
  }

  // ------------------------------------------------------------------ chrome
  bindChrome() {
    document.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
      this.filter = b.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    }));
    for (const k of ["anchors", "ghosts", "grid"]) {
      const b = $("t-" + k);
      b.setAttribute("aria-pressed", String(this.vp.show[k]));
      b.addEventListener("click", () => this.toggleShow(k));
    }
    $("t-section").addEventListener("click", () => { this.vp.setSection(null); this.renderSectionChip(); });
    $("fit-view").addEventListener("click", () => this.vp.viewTo(this.vp.camera.position.clone().sub(this.vp.controls.target).toArray(), true));
    $("undo").addEventListener("click", () => this.undo());
    $("redo").addEventListener("click", () => this.redo());
    $("script-btn").addEventListener("click", () => this.toggleEditor());
    $("help-btn").addEventListener("click", () => this.toggleHelp());
    $("export-btn").addEventListener("click", (e) => { e.stopPropagation(); this.exporter ? this.closeExport() : this.openExport(); });
    $("file").addEventListener("click", (e) => { e.stopPropagation(); this.fileMenu(); });
    $("params-btn").addEventListener("click", (e) => { e.stopPropagation(); this.paramsPanel ? this.closeParams() : this.openParams(); });
    $("params-btn").title = `Parameters: names for sizes, used as P.name (${keyLabel("Shift+V")})`;
    $("params-edit").addEventListener("click", () => this.openParams());
    $("export-btn").title = `Export bodies as 3MF, STL or STEP (${keyLabel("Shift+E")})`;
    $("addparam").addEventListener("submit", async (e) => {
      e.preventDefault();
      const n = $("np-name").value.trim(), v = $("np-value").value.trim();
      if (!n || !v) return toast("Give the parameter a name and a value", true);
      try { await this.action("/api/param", { name: n, value: /^-?[\d.]+$/.test(v) ? `${v} * mm` : v }); $("np-name").value = ""; $("np-value").value = ""; } catch (err) { /* toasted */ }
    });
  }

  // the Section chip shows while the view is cut open; clicking it closes the cut
  renderSectionChip() { $("t-section").hidden = !this.vp.section; }

  toggleShow(k) {
    this.vp.show[k] = !this.vp.show[k];
    $("t-" + k).setAttribute("aria-pressed", String(this.vp.show[k]));
    if (k === "grid") this.vp.gridKey = ""; else this.vp.setScene(this.vp.data);
  }

  toggleEditor(force) {
    const pane = $("editor-pane");
    const open = force === undefined ? pane.hidden : force;
    pane.hidden = !open; $("script-btn").setAttribute("aria-pressed", String(open));
    if (open && !this.editor) this.editor = new ScriptEditor(this);
    if (open) this.editor.load(this.state.text, this.state.hash);
    setTimeout(() => this.vp.resize(), 0);
  }

  async undo() { try { const o = await this.action("/api/undo", {}); toast(`Undid ${o.label}`); } catch (e) { /* toasted */ } }
  async redo() { try { const o = await this.action("/api/redo", {}); toast(`Redid ${o.label}`); } catch (e) { /* toasted */ } }

  // ------------------------------------------------------------------ shortcuts
  buildHelp() {
    const ops = Object.entries(TOOLS).filter(([, t]) => t.key);
    const rows = (list) => list.map(([k, l]) => `<tr><td><kbd>${k}</kbd></td><td>${esc(l)}</td></tr>`).join("");
    const cmd = IS_MAC ? "⌘" : "Ctrl+";
    $("help").innerHTML = `<div class="help-card" role="dialog" aria-label="Keyboard shortcuts">
      <div class="help-head"><b>Keyboard shortcuts</b><button id="help-close">Close</button></div>
      <div class="help-cols">
        <section><h2>Operations</h2><table>${rows(ops.map(([, t]) => [keyLabel(t.key) + (t.alt ? ` or ${keyLabel(t.alt)}` : ""), t.label]))}</table></section>
        <section><h2>In a sketch</h2><table>${rows([...SKETCH_DRAW.map((t) => [t.key.replace("Shift+", "⇧"), t.label]), ["X", "Construction"],
          ...SKETCH_RULES.map((r) => [r.key.replace("Shift+", "⇧"), r.label]), ["Delete", "Delete the selection"]])}</table></section>
        <section><h2>View</h2><table>${rows(VIEW_KEYS.map(([k, , l]) => [viewKeyLabel(k), l]))}</table>
          <h2>Mouse and trackpad</h2><table>${rows([["Click", "Select a face or edge (double-click: the body)"], ["Drag →", "Box: select what is inside"], ["Drag ←", "Box: select what it touches"],
            ["Right-drag", "Orbit"], ["Right-click", "Body menu"], ["Two fingers", "Pan"], ["⇧ + two fingers", "Orbit"], ["Pinch / wheel", "Zoom at the pointer"], ["Middle-drag", "Pan"]])}</table>
          <h2>Everywhere</h2><table>${rows([[cmd + "Z", "Undo"], ["⇧" + cmd + "Z", "Redo"], [cmd + "E", "Script pane"], [cmd + "S", "Save the script pane"],
            ["Enter", "OK in a tool"], ["Esc", "Cancel / clear selection"], ["Tab", "Next field (over the view: next snap point)"], ["Delete", "Delete the selected body's feature"], [keyLabel("Shift+E"), "Export"], [keyLabel("Shift+V"), "Parameters"], ["?", "This sheet"]])}</table></section>
      </div></div>`;
    $("help").addEventListener("click", (e) => { if (e.target.id === "help" || e.target.id === "help-close") this.toggleHelp(false); });
  }

  toggleHelp(force) { const h = $("help"); h.hidden = force === undefined ? !h.hidden : !force; }

  comboName(ev) {
    const c = ev.code || "";
    const k = c.startsWith("Key") ? c.slice(3) : c.startsWith("Digit") ? c.slice(5) : null;
    return k ? (ev.shiftKey ? "Shift+" + k : k) : null;
  }

  isMod(ev, which) {
    if (which === "ctrl") return ev.ctrlKey && !ev.metaKey && !ev.altKey;
    if (which === "alt") return ev.altKey && !ev.metaKey && !ev.ctrlKey;
    if (which === "altshift") return ev.altKey && ev.shiftKey && !ev.metaKey && !ev.ctrlKey;
    return false;
  }

  runView(action) {
    if (["home", "front", "back", "left", "right", "top", "bottom"].includes(action)) return this.vp.viewTo(action, action === "home");
    if (action === "fit") return $("fit-view").click();
    if (action === "script") return this.toggleEditor();
    this.toggleShow(action);
  }

  bindKeys() {
    document.addEventListener("keydown", (ev) => {
      const inCode = ev.target.closest && ev.target.closest(".cm-editor");
      const inField = ev.target.closest && ev.target.closest("input, select, textarea, .cm-editor");
      const cmd = IS_MAC ? ev.metaKey : ev.ctrlKey;
      const name = this.comboName(ev);
      if (cmd && ev.key.toLowerCase() === "s") { ev.preventDefault(); if (this.editor) this.editor.save(); return; }
      if (cmd && ev.key.toLowerCase() === "e") { ev.preventDefault(); if (!this.state?.start) this.toggleEditor(); return; }
      if (cmd && this.state?.app && !ev.shiftKey && (ev.key.toLowerCase() === "n" || ev.key.toLowerCase() === "o")) {
        ev.preventDefault(); this.fileAction(ev.key.toLowerCase() === "n" ? "new" : "open"); return;
      }
      if (this.state?.start) return;     // the start screen has no tools
      if (!inCode && name) {
        // operations: Control+letter (Mac) / Alt+letter
        if (this.isMod(ev, OPS_MOD) && !(VIEW_MOD === "altshift" && ev.shiftKey && ev.altKey)) {
          if (name === "Shift+E") { ev.preventDefault(); this.exporter ? this.closeExport() : this.openExport(); return; }
          if (name === "Shift+V") { ev.preventDefault(); this.paramsPanel ? this.closeParams() : this.openParams(); return; }
          const tool = Object.entries(TOOLS).find(([, t]) => t.key === name || t.alt === name);
          if (tool) { ev.preventDefault(); this.openTool(tool[0]); return; }
        }
        // view: Option+letter (Mac) / Alt+Shift+letter
        if (this.isMod(ev, VIEW_MOD)) {
          const bare = name.replace("Shift+", "");
          const v = VIEW_KEYS.find(([k]) => k === bare);
          if (v) { ev.preventDefault(); this.runView(v[1]); return; }
        }
      }
      if (this.dialog && !inCode && this.dialog.key(ev)) return;
      if (ev.key === "Escape") {
        if (!$("help").hidden) return this.toggleHelp(false);
        if (this.paramsPanel) { this.closeParams(); return; }
        if (this.exporter) { this.closeExport(); this.closeMenus(); return; }
        if (this.dialog) this.closeDialog(); else { this.selection = []; this.vp.setSelected([]); this.vp.showLocalGrid(null); this.renderSelection(); }
        this.closeMenus(); return;
      }
      if (inField) { if (ev.key === "Enter" && this.dialog && ev.target.closest("#dialog")) { ev.preventDefault(); this.dialog.ok(); } return; }
      if (cmd && ev.key.toLowerCase() === "z") { ev.preventDefault(); ev.shiftKey ? this.redo() : this.undo(); return; }
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (ev.key === "?") { this.toggleHelp(); return; }
      if (ev.key === "Enter" && this.dialog) { ev.preventDefault(); this.dialog.ok(); return; }
      if (ev.key === "Delete" || ev.key === "Backspace") {
        const b = this.selection.find((s) => s.kind === "body");
        if (b) { const body = this.vp.body(b.body); if (body) this.deleteFeature(body.history[0]); }
      }
    });
  }
}

// The toolbar scrolls sideways on narrow windows, which would clip a menu inside it,
// so menus are placed in window coordinates under their button.
function placeMenu(menu, anchor) {
  const r = anchor.getBoundingClientRect();
  menu.style.position = "fixed"; menu.style.top = r.bottom + 4 + "px"; menu.style.left = r.left + "px"; menu.style.right = "auto";
  const m = menu.getBoundingClientRect();
  if (m.right > window.innerWidth - 8) menu.style.left = Math.max(8, window.innerWidth - 8 - m.width) + "px";
}

function fmtStep(s) { return `${+s.toPrecision(2)} mm`; }

window.addEventListener("DOMContentLoaded", () => { window.ofToast = toast; window.ofApp = new App(); });
