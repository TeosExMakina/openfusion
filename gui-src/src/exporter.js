// Export panel: pick bodies (in the list or by clicking them in the view), a format,
// one file or one per body, and mesh quality. Nothing is exported until Export.
import { $, esc, get, post, toast } from "./api.js";

const PREFS_KEY = "openfusion.export";
const FORMATS = {
  "3mf": { label: "3MF", desc: "For slicers. Each body stays a separate object, in millimeters." },
  stl: { label: "STL", desc: "Plain mesh. One STL holds one mesh, so several bodies arrive in the slicer as one object." },
  step: { label: "STEP", desc: "Exact geometry (no mesh) for other CAD programs." },
};
const QUALITY = { draft: ["Draft", "0.05 mm"], standard: ["Standard", "0.01 mm"], fine: ["Fine", "0.002 mm"] };

function readPrefs() {
  try { return { fmt: "3mf", each: false, quality: "standard", ...JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") }; }
  catch (e) { return { fmt: "3mf", each: false, quality: "standard" }; }
}
function writePrefs(p) { try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); } catch (e) { /* storage unavailable */ } }

export function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
const fmtLen = (v) => String(+v.toFixed(2));
const fmtVol = (mm3) => (mm3 >= 100 ? `${(mm3 / 1000).toFixed(2)} cm³` : `${mm3.toFixed(1)} mm³`);

export function exportName(stem, fmt, ids, each) {
  if (each && ids.length > 1) return `${stem}-${fmt}.zip`;
  if (ids.length === 1) return `${stem}-${ids[0]}.${fmt}`;
  return `${stem}.${fmt}`;
}

export function exportUrl(fmt, ids, each, quality) {
  return `/api/export?fmt=${fmt}&bodies=${ids.map(encodeURIComponent).join(",")}&each=${each ? 1 : 0}&quality=${quality}`;
}

// Fetch the file and hand it to the browser as a download (errors come back as JSON).
export async function download(fmt, ids, each, quality) {
  const r = await fetch(exportUrl(fmt, ids, each, quality), { cache: "no-store" });
  if (!r.ok) { let msg = r.statusText; try { msg = (await r.json()).error; } catch (e) { /* not JSON */ } throw new Error(msg); }
  const blob = await r.blob();
  const name = r.headers.get("X-Export-Name") || exportName("part", fmt, ids, each);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  return { name, size: blob.size };
}

// In the desktop app the file goes where the user picks in a Save dialog; in a browser it downloads.
export async function saveExport(fmt, ids, each, quality) {
  if (window.ofApp && window.ofApp.state && window.ofApp.state.app) {
    const out = await post("/api/file/export", { fmt, bodies: ids, each, quality });
    if (out.cancelled) return null;
    toast(`Saved ${out.name} (${fmtBytes(out.size)})`);
    return out;
  }
  const out = await download(fmt, ids, each, quality);
  toast(`Downloaded ${out.name} (${fmtBytes(out.size)})`);
  return out;
}

export async function quickExport(fmt, ids) {
  const p = readPrefs();
  try { await saveExport(fmt, ids, false, p.quality); } catch (e) { toast(e.message, true); }
}

export class ExportPanel {
  constructor(app, ids) {
    this.app = app; this.el = $("export-panel");
    this.prefs = readPrefs();
    this.info = null; this.loading = false; this.busy = false;
    this.chosen = new Set(ids || []);
    this.preset = !!(ids && ids.length);
    this.prevSel = app.selection;
    this.el.hidden = false;
    this.render();
    this.load();
  }

  close() {
    this.el.hidden = true; this.el.innerHTML = "";
    this.app.vp.setHover(null);
    this.app.vp.setSelected(this.app.selection.map((s) => s.key));
  }

  async load() {
    this.loading = true; this.render();
    const q = this.prefs.quality;
    try {
      const info = await get("/api/export_info?quality=" + q);
      if (q !== this.prefs.quality) return;   // a newer request is on its way
      const first = !this.info;
      this.info = info;
      const usable = new Set(info.bodies.filter((b) => !b.problem).map((b) => b.id));
      if (first && !this.preset) for (const b of info.bodies) if (b.role === "solid" && usable.has(b.id)) this.chosen.add(b.id);
      for (const id of [...this.chosen]) if (!usable.has(id)) this.chosen.delete(id);
    } catch (e) {
      this.error = e.message;
    }
    this.loading = false; this.render();
  }

  bodies() { return this.info ? this.info.bodies : []; }
  ids() { return this.bodies().filter((b) => this.chosen.has(b.id)).map((b) => b.id); }

  toggle(id, force) {
    const b = this.bodies().find((x) => x.id === id);
    if (!b) return false;
    if (b.problem) { toast(`${id} is ${b.problem}`, true); return true; }
    const on = force === undefined ? !this.chosen.has(id) : force;
    on ? this.chosen.add(id) : this.chosen.delete(id);
    this.render();
    return true;
  }

  // a click in the 3D view while the panel is open adds or removes that body
  viewClick(ev) {
    const hit = this.app.vp.pick(ev, "body");
    if (hit) this.toggle(hit.body);
  }

  set(k, v) {
    this.prefs[k] = v; writePrefs(this.prefs);
    if (k === "quality") this.load(); else this.render();
  }

  summary(ids) {
    const sel = this.bodies().filter((b) => ids.includes(b.id));
    const tris = sel.reduce((n, b) => n + b.triangles, 0);
    const { fmt, each } = this.prefs;
    if (fmt === "step") return `${sel.length} ${sel.length === 1 ? "body" : "bodies"}, exact geometry`;
    if (fmt === "stl") {
      const bytes = each || sel.length === 1 ? sel.reduce((n, b) => n + b.stl_bytes, 0) : 84 + 50 * tris;
      return `${tris.toLocaleString()} triangles · about ${fmtBytes(bytes)}`;
    }
    return `${tris.toLocaleString()} triangles`;
  }

  notes(ids) {
    const out = [], sel = this.bodies().filter((b) => ids.includes(b.id));
    const { fmt, each } = this.prefs;
    if (fmt === "stl" && !each && ids.length > 1) out.push("These bodies will be merged into one mesh. Choose One per body or 3MF to keep them apart in the slicer.");
    const refs = sel.filter((b) => b.role === "reference").map((b) => b.id);
    if (refs.length) out.push(`${refs.join(", ")} ${refs.length === 1 ? "is a reference body" : "are reference bodies"}; exported because you picked ${refs.length === 1 ? "it" : "them"}.`);
    for (const b of sel) for (const n of b.notes) out.push(`${b.id}: ${n}.`);
    if (this.info && this.info.rolled_back) out.push("The timeline is rolled back in the view; exports always use the whole timeline.");
    return out;
  }

  render() {
    const el = this.el, p = this.prefs;
    const ids = this.ids(), all = this.bodies();
    const solids = all.filter((b) => b.role === "solid" && !b.problem).map((b) => b.id);
    this.app.vp.setSelected(ids.map((id) => "body:" + id));
    const rows = all.map((b) => {
      const on = this.chosen.has(b.id), dis = !!b.problem;
      const meta = dis ? `<span class="xp-bad">${esc(b.problem)}</span>`
        : `${b.size.map(fmtLen).join(" × ")} mm · ${fmtVol(b.volume)}${p.fmt === "step" ? "" : ` · ${b.triangles.toLocaleString()} tri`}` +
          (b.notes.length ? ` · <span class="xp-warn" title="${esc(b.notes.join("; "))}">⚠ ${esc(b.notes[0])}</span>` : "");
      return `<li class="${on ? "on" : ""}${dis ? " dis" : ""}" data-id="${esc(b.id)}">
        <label><input type="checkbox" ${on ? "checked" : ""} ${dis ? "disabled" : ""}>
        <span class="xp-nm">${esc(b.id)}</span><span class="role ${b.role}">${b.role}${b.mirrored ? " · mirrored" : ""}</span></label>
        <div class="xp-meta">${meta}</div></li>`;
    }).join("");
    const seg = (key, opts, cur, disabled = false) => `<div class="seg" role="radiogroup">${Object.entries(opts).map(([k, l]) =>
      `<button type="button" data-k="${key}" data-v="${k}" aria-pressed="${String(cur === k)}" ${disabled ? "disabled" : ""}>${esc(l)}</button>`).join("")}</div>`;
    const name = this.info ? exportName(this.info.file, p.fmt, ids, p.each) : "";
    const notes = this.notes(ids);
    el.innerHTML = `
      <div class="xp-head"><b>Export</b><button class="xp-x" aria-label="Close">×</button></div>
      <p class="xp-hint">${this.info ? `${ids.length} of ${all.length} ${all.length === 1 ? "body" : "bodies"} · click bodies in the view to add or remove them` : ""}</p>
      <div class="xp-quick"><button type="button" data-pick="solids">All solids</button><button type="button" data-pick="none">None</button></div>
      <ul class="xp-list">${this.error ? `<li class="dis"><span class="xp-bad">${esc(this.error)}</span></li>` : !this.info ? `<li class="dis">Measuring bodies…</li>` : rows || `<li class="dis">No bodies to export.</li>`}</ul>
      <div class="xp-row"><span class="xp-lbl">Format</span>${seg("fmt", Object.fromEntries(Object.entries(FORMATS).map(([k, f]) => [k, f.label])), p.fmt)}</div>
      <p class="xp-desc">${esc(FORMATS[p.fmt].desc)}</p>
      <div class="xp-row"><span class="xp-lbl">Files</span>${seg("each", { one: "One file", each: "One per body" }, p.each ? "each" : "one", ids.length < 2)}</div>
      ${p.fmt === "step" ? "" : `<div class="xp-row"><span class="xp-lbl">Quality</span>${seg("quality", Object.fromEntries(Object.entries(QUALITY).map(([k, v]) => [k, v[0]])), p.quality)}</div>
      <p class="xp-desc">Surfaces stay within ${QUALITY[p.quality][1]} of the exact shape.${this.loading && this.info ? " Measuring…" : ""}</p>`}
      ${notes.length ? `<ul class="xp-notes">${notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      <div class="xp-foot"><div class="xp-file"><code>${esc(name)}</code><span>${this.info && ids.length ? esc(this.summary(ids)) : ""}</span></div>
        <button class="primary" id="xp-go" ${!ids.length || this.busy ? "disabled" : ""}>${this.busy ? "Exporting…" : "Export"}</button></div>`;
    el.querySelector(".xp-x").onclick = () => this.app.closeExport();
    el.querySelectorAll(".xp-list li[data-id]").forEach((li) => {
      const id = li.dataset.id;
      li.querySelector("input").addEventListener("change", (e) => this.toggle(id, e.target.checked));
      li.addEventListener("mouseenter", () => this.app.vp.setHover("body:" + id));
      li.addEventListener("mouseleave", () => this.app.vp.setHover(null));
    });
    el.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => {
      this.chosen = new Set(b.dataset.pick === "solids" ? solids : []); this.render();
    }));
    el.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.k, v = b.dataset.v;
      this.set(k, k === "each" ? v === "each" : v);
    }));
    $("xp-go").onclick = () => this.go();
  }

  async go() {
    const ids = this.ids();
    if (!ids.length || this.busy) return;
    this.busy = true; this.render();
    try {
      const out = await saveExport(this.prefs.fmt, ids, this.prefs.each && ids.length > 1, this.prefs.quality);
      this.busy = false;
      if (out) this.app.closeExport(); else this.render();
    } catch (e) {
      this.busy = false; this.render();
      toast(e.message, true);
    }
  }
}
