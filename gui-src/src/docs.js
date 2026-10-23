// The Documents panel (click the file name, or ⌘⇧O): the open part (rename it, save a copy,
// show it in Finder), new parts, and the parts to open: the ones next to it, the OpenFusion
// parts folder, recent files and the examples. Parts save themselves on every change, so
// "saving" here means saving a copy under another name.
import { $, esc, post, toast } from "./api.js";
import { IS_MAC } from "./tools.js";

export class DocumentsPanel {
  constructor(app) {
    this.app = app; this.el = $("docs-panel");
    this.data = null; this.tab = null; this.filter = ""; this.error = "";
    this.el.hidden = false;
    this.el.onclick = (e) => { if (e.target === this.el) this.app.closeDocs(); };
    this.el.innerHTML = `<div class="dc-card" role="dialog" aria-label="Documents"><p class="dc-empty">Loading…</p></div>`;
    this.load();
  }

  async load() {
    try { this.data = await post("/api/file/documents", {}); } catch (e) { this.error = e.message; }
    if (!this.tab && this.data) this.tab = this.data.folders.length ? "f0" : "recent";
    this.render();
  }

  close() { this.el.hidden = true; this.el.innerHTML = ""; this.el.onclick = null; }

  lists() {
    const d = this.data, out = [];
    d.folders.forEach((f, i) => out.push({ id: "f" + i, label: f.label, hint: f.folder, rows: f.parts }));
    out.push({ id: "recent", label: "Recent", hint: "parts you opened lately", rows: d.recent });
    if (d.examples.length) out.push({ id: "examples", label: "Examples", hint: "open as a copy in Documents/OpenFusion", rows: d.examples.map((x) => ({ example: x.name, name: x.name, folder: x.title })) });
    return out;
  }

  render() {
    if (!this.data) { this.el.innerHTML = `<div class="dc-card"><p class="dc-err">${esc(this.error || "Couldn't read the documents")}</p></div>`; return; }
    const d = this.data, cur = d.current, app = d.app, mod = IS_MAC ? "⌘" : "Ctrl+";
    const lists = this.lists(), list = lists.find((l) => l.id === this.tab) || lists[0];
    const f = this.filter.trim().toLowerCase();
    const rows = list.rows.filter((r) => !f || r.name.toLowerCase().includes(f) || (r.folder || "").toLowerCase().includes(f));
    const isCur = (r) => cur && r.path === cur.path;
    const rowHtml = (r) => `<li><button type="button" class="dc-row${isCur(r) ? " cur" : ""}" ${r.example ? `data-example="${esc(r.example)}"` : `data-open="${esc(r.path)}"`} title="${esc(r.path || r.folder)}">
        <span class="nm">${esc(r.name)}</span>${isCur(r) ? `<span class="tag">open</span>` : ""}
        <span class="meta">${esc(list.id.startsWith("f") ? r.modified : r.example ? r.folder : `${r.folder} · ${r.modified}`)}</span></button></li>`;
    this.el.innerHTML = `<div class="dc-card" role="dialog" aria-label="Documents">
      <div class="pp-head"><b>Documents</b><span>Parts save themselves on every change. Save a copy to keep a version or branch off.</span>
        <button type="button" class="pp-x" aria-label="Close">×</button></div>
      ${cur ? `<section class="dc-cur">
        <div class="dc-curname"><label>Open part <input id="dc-name" value="${esc(cur.name)}" spellcheck="false" autocomplete="off" aria-label="Part name"></label><span class="ext">.ofs</span>
          <button type="button" id="dc-rename" disabled>Rename</button></div>
        <div class="dc-meta">${esc(cur.folder)} · saved ${esc(cur.modified)}${app ? ` · <button type="button" class="link" id="dc-reveal">Show in Finder</button>` : ""}</div>
        <div class="dc-line"><label>Save a copy as <input id="dc-copy" value="${esc(cur.name)} copy" spellcheck="false" autocomplete="off"></label>
          <button type="button" id="dc-saveas" class="primary">Save copy</button>${app ? `<button type="button" id="dc-saveas-choose">Choose where…</button>` : ""}</div>
        <label class="dc-check"><input type="checkbox" id="dc-switch" checked> then carry on in the copy (otherwise stay in this one)</label>
      </section>` : ""}
      <section class="dc-line dc-new"><label>New part <input id="dc-new" placeholder="name" spellcheck="false" autocomplete="off"></label>
        <button type="button" id="dc-create">Create</button>${app ? `<button type="button" id="dc-open">Open… <kbd>${mod}O</kbd></button>` : ""}
        <span class="dc-hint">in ${esc(cur ? cur.folder : d.parts_dir)}</span></section>
      <div class="dc-err" role="status">${esc(this.error)}</div>
      <div class="dc-tabs" role="tablist">${lists.map((l) => `<button type="button" role="tab" data-tab="${l.id}" aria-selected="${l.id === list.id}">${esc(l.label)} <span>${l.rows.length}</span></button>`).join("")}
        <input class="dc-filter" placeholder="Filter" value="${esc(this.filter)}" aria-label="Filter" spellcheck="false" autocomplete="off"></div>
      <div class="dc-where">${esc(list.hint)}</div>
      <ul class="dc-list">${rows.map(rowHtml).join("") || `<li class="dc-empty">${f ? "Nothing matches." : "No parts here yet."}</li>`}</ul>
    </div>`;
    this.bind();
  }

  bind() {
    const q = (s) => this.el.querySelector(s);
    q(".pp-x").onclick = () => this.app.closeDocs();
    this.el.querySelectorAll("input").forEach((i) => i.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") this.app.closeDocs();
    }));
    this.el.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { this.tab = b.dataset.tab; this.render(); }));
    const filt = q(".dc-filter");
    filt.oninput = () => { this.filter = filt.value; const p = filt.selectionStart; this.render(); const nf = q(".dc-filter"); nf.focus(); nf.setSelectionRange(p, p); };
    this.el.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => this.run("open", { path: b.dataset.open }, true)));
    this.el.querySelectorAll("[data-example]").forEach((b) => (b.onclick = () => this.run("example", { name: b.dataset.example }, true)));
    const name = q("#dc-name");
    if (name) {
      const cur = this.data.current.name;
      const ren = q("#dc-rename");
      name.oninput = () => { ren.disabled = !name.value.trim() || name.value.trim() === cur; };
      const doRename = () => { if (!ren.disabled) this.run("rename", { name: name.value.trim() }, false, "Renamed"); };
      ren.onclick = doRename;
      name.addEventListener("keydown", (e) => { if (e.key === "Enter") doRename(); });
      const copy = q("#dc-copy"), sw = q("#dc-switch");
      const saveAs = () => this.run("save_as", { name: copy.value.trim(), open: sw.checked }, sw.checked, sw.checked ? "Saved the copy; you're working in it now" : "Saved a copy");
      q("#dc-saveas").onclick = saveAs;
      copy.addEventListener("keydown", (e) => { if (e.key === "Enter") saveAs(); });
      const choose = q("#dc-saveas-choose");
      if (choose) choose.onclick = () => this.run("save_as", { choose: true, open: sw.checked }, sw.checked, "Saved a copy");
      const rev = q("#dc-reveal");
      if (rev) rev.onclick = () => this.app.fileAction("reveal");
    }
    const nw = q("#dc-new");
    const create = () => this.run("new", { name: nw.value.trim(), here: !!this.data.current }, true, "New part");
    q("#dc-create").onclick = () => (nw.value.trim() ? create() : (this.error = "Give the new part a name", this.render(), q("#dc-new").focus()));
    nw.addEventListener("keydown", (e) => { if (e.key === "Enter" && nw.value.trim()) create(); });
    const op = q("#dc-open");
    if (op) op.onclick = () => this.run("open", {}, true);
  }

  // a file action; done: close the panel (another part opened), else reload the lists
  async run(act, body, done, message) {
    this.error = "";
    let out;
    try { out = await post("/api/file/" + act, body); } catch (e) { this.error = e.message; this.render(); return; }
    if (out.cancelled) return;
    await this.app.refresh();
    if (message) toast(message);
    if (done) this.app.closeDocs(); else this.load();
  }
}
