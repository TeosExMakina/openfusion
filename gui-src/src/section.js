// Section view: cut the model open with a plane to see inside (walls of a shell, a thread's
// profile, a hole's depth). It only changes the view: nothing is written to the script.
import { $, esc, toast } from "./api.js";

const fmt = (v) => String(Math.round(v * 100) / 100);
const WORLD = {      // the normal points at the half that is cut away
  XY: { n: [0, 0, 1], label: "world XY (from above)" },
  XZ: { n: [0, -1, 0], label: "world XZ (from the front)" },
  YZ: { n: [1, 0, 0], label: "world YZ (from the right)" },
};

export class SectionView {
  constructor(app) {
    this.app = app; this.vp = app.vp; this.edit = null; this.kept = false;
    this.prev = this.vp.section;
    this.panel = $("dialog"); this.panel.hidden = false;
    const face = app.selection.find((s) => s.kind === "face" && s.entry && s.entry.kind === "plane");
    if (face) this.fromFace(face.entry, face.body, face.label, face.point);
    else if (this.prev) { this.base = { o: this.prev.base.o, n: this.prev.base.n, label: this.prev.base.label }; this.offset = this.prev.offset; this.flip = this.prev.flip; }
    else this.world("XZ");
    this.renderPanel();
    this.update();
    this.app.setHint("Click a flat face to cut parallel to it, or pick a world plane. Drag the slider to move the cut.");
  }

  boxKind() { return null; }
  boxPick() {}
  takeBody() { return false; }
  takeGroup() { return false; }

  centre() {
    const b = this.vp.bbox;
    if (!b || b.isEmpty()) return [0, 0, 0];
    return [(b.min.x + b.max.x) / 2, (b.min.y + b.max.y) / 2, (b.min.z + b.max.z) / 2];
  }

  // start through the middle of the model, whatever the plane
  throughCentre() {
    const c = this.centre(), { o, n } = this.base;
    this.offset = (c[0] - o[0]) * n[0] + (c[1] - o[1]) * n[1] + (c[2] - o[2]) * n[2];
    this.flip = false;
  }

  world(name) {
    const w = WORLD[name];
    this.base = { o: this.centre(), n: w.n.slice(), label: w.label };
    this.offset = 0; this.flip = false;
  }

  fromFace(entry, body, label, point) {
    const snaps = this.vp.snapCandidates({ face: entry, body, label, point });
    const fr = (snaps.find((c) => c.kind === "center") || snaps[0]).frame;
    this.base = { o: fr.o.slice(), n: fr.z.slice(), label: `${body}.face("${label}")` };
    this.throughCentre();
  }

  update() {
    const { o, n } = this.base, s = this.flip ? -1 : 1;
    const p = [0, 1, 2].map((i) => o[i] + n[i] * this.offset);
    this.vp.setSection({ o: p, n: n.map((v) => v * s), base: this.base, offset: this.offset, flip: this.flip });
    const inp = this.panel.querySelector("#sec-offset"), rng = this.panel.querySelector("#sec-range");
    if (inp && document.activeElement !== inp) inp.value = fmt(this.offset);
    if (rng) rng.value = String(this.offset);
  }

  hover(ev) {
    const hit = this.vp.pick(ev, "face");
    const ok = hit && hit.face && hit.face.kind === "plane";
    this.vp.setHover(ok ? "face:" + hit.ref : null);
    this.app.showTip(ok ? "Cut parallel to this face" : hit ? "Pick a flat face" : null, ev);
    return true;
  }

  click(ev) {
    const hit = this.vp.pick(ev, "face");
    if (!hit) return;
    if (!hit.face || hit.face.kind !== "plane") { toast("Pick a flat face (or a world plane in the panel)", true); return; }
    this.fromFace(hit.face, hit.body, hit.label, hit.point);
    this.renderPanel(); this.update();
  }

  key(ev) {
    if (ev.target && ev.target.closest && ev.target.closest("#dialog input")) return false;
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return false;
    if (ev.key === "f" || ev.key === "F") { this.flip = !this.flip; this.update(); return true; }
    return false;
  }

  renderPanel() {
    const L = Math.max(this.vp.modelSize || 50, 10), step = Math.max(L / 400, 0.01);
    this.panel.innerHTML = `
      <div class="dlg-head"><b>Section</b><span>view only</span></div>
      <div class="dlg-help">Cuts the view open to show the inside. Nothing is written to the script.</div>
      <div class="fields">
        <div class="field"><label>Plane</label><span class="sec-plane">${esc(this.base.label)}</span></div>
        <div class="field"><label>World</label><div class="row">${Object.keys(WORLD).map((k) => `<button type="button" data-world="${k}">${k}</button>`).join("")}</div></div>
        <div class="field"><label for="sec-offset">Offset</label><div class="row"><input id="sec-offset" type="text" inputmode="decimal" spellcheck="false">
          <button type="button" class="flip" id="sec-flip" title="Show the other half (F)">⇄</button></div></div>
        <div class="field"><label for="sec-range">Move</label><input id="sec-range" type="range" min="${-L}" max="${L}" step="${step}"></div>
      </div>
      <div class="dlg-foot"><span class="sk-keys">Click a flat face to cut along it</span>
        <button type="button" id="sec-off">Turn off</button><button type="button" class="primary" id="sec-ok">Keep</button></div>`;
    this.panel.querySelectorAll("[data-world]").forEach((b) => b.addEventListener("click", () => { this.world(b.dataset.world); this.renderPanel(); this.update(); }));
    const inp = this.panel.querySelector("#sec-offset");
    inp.addEventListener("input", () => { const v = Number(inp.value); if (Number.isFinite(v) && inp.value.trim() !== "") { this.offset = v; this.update(); } });
    this.panel.querySelector("#sec-range").addEventListener("input", (e) => { this.offset = Number(e.target.value); this.update(); });
    this.panel.querySelector("#sec-flip").addEventListener("click", () => { this.flip = !this.flip; this.update(); });
    this.panel.querySelector("#sec-ok").addEventListener("click", () => this.ok());
    this.panel.querySelector("#sec-off").addEventListener("click", () => { this.prev = null; this.app.closeDialog(); });
  }

  ok() { this.kept = true; this.app.closeDialog(true); }

  close() {
    if (!this.kept) this.vp.setSection(this.prev || null);
    this.vp.setHover(null);
    this.panel.hidden = true; this.panel.innerHTML = "";
    this.app.renderSectionChip();
  }
}
