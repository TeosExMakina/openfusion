// The Parameters table (like Fusion's Change Parameters): every entry of the script's
// params({...}) block with its expression, current value, comment and where it is used.
// Each edit is one script edit (undoable); use a parameter in any field as P.name.
import { $, esc, toast } from "./api.js";

const fmt = (v) => (typeof v === "number" ? String(Math.round(v * 10000) / 10000) : v == null ? "—" : String(v));

export class ParamsPanel {
  constructor(app) {
    this.app = app; this.el = $("params-panel");
    this.error = null; this.focus = null;
    this.el.hidden = false;
    this.el.addEventListener("click", (e) => { if (e.target === this.el) this.app.closeParams(); });
    this.render();
    setTimeout(() => { const f = this.el.querySelector(".pp-add [data-col=name]"); if (f && !this.rows().length) f.focus(); }, 0);
  }

  rows() { return (this.app.state && this.app.state.desc && this.app.state.desc.param_info) || []; }

  close() { this.el.hidden = true; this.el.innerHTML = ""; }

  render() {
    const rows = this.rows();
    const values = (this.app.state && this.app.state.scene && this.app.state.scene.params) || {};
    // a refresh that changes nothing here leaves the table alone (and whatever is being typed in it)
    const key = JSON.stringify([rows, values, this.error]);
    if (key === this.lastKey && this.el.firstChild && !this.focus) return;
    this.lastKey = key;
    const body = rows.map((r) => `<tr data-row="${esc(r.name)}">
        <td><input data-col="name" value="${esc(r.name)}" aria-label="Name" spellcheck="false" autocomplete="off"></td>
        <td><input data-col="expr" value="${esc(r.expr)}" aria-label="Expression for ${esc(r.name)}" spellcheck="false" autocomplete="off"></td>
        <td class="pp-val" title="Value in mm (or degrees)">${esc(fmt(values[r.name]))}</td>
        <td><input data-col="comment" value="${esc(r.comment)}" aria-label="Comment for ${esc(r.name)}" placeholder="—" autocomplete="off"></td>
        <td class="pp-used">${r.used_by.length ? r.used_by.map((u) => `<button type="button" class="chiplet" data-feature="${esc(u)}" title="Edit ${esc(u)}">${esc(u)}</button>`).join("") : '<span class="pp-none">not used</span>'}</td>
        <td><button type="button" class="x" data-del="${esc(r.name)}" ${r.used_by.length ? `disabled title="Used by ${esc(r.used_by.join(", "))}"` : `title="Delete ${esc(r.name)}"`} aria-label="Delete ${esc(r.name)}">×</button></td>
      </tr>`).join("");
    this.el.innerHTML = `<div class="pp-card" role="dialog" aria-label="Parameters">
      <div class="pp-head"><b>Parameters</b><span>Use one in any field as <code>P.name</code>, e.g. <code>P.wall * 2</code>. Values are in mm (angles in degrees).</span>
        <button type="button" class="pp-x" aria-label="Close">×</button></div>
      <table class="pp-table">
        <thead><tr><th>Name</th><th>Expression</th><th>Value</th><th>Comment</th><th>Used by</th><th></th></tr></thead>
        <tbody>${body || `<tr class="pp-empty"><td colspan="6">No parameters yet. Add one below; it goes into the script's params({...}) block.</td></tr>`}</tbody>
        <tfoot><tr class="pp-add">
          <td><input data-col="name" placeholder="name" aria-label="New parameter name" spellcheck="false" autocomplete="off"></td>
          <td><input data-col="expr" placeholder="e.g. 2 * mm" aria-label="New parameter expression" spellcheck="false" autocomplete="off"></td>
          <td></td>
          <td><input data-col="comment" placeholder="comment (optional)" aria-label="New parameter comment" autocomplete="off"></td>
          <td colspan="2"><button type="button" class="primary" id="pp-add">Add</button></td>
        </tr></tfoot>
      </table>
      <div class="pp-err" role="status">${this.error ? esc(this.error) : ""}</div>
    </div>`;
    this.el.querySelector(".pp-x").onclick = () => this.app.closeParams();
    this.el.querySelectorAll("tbody tr[data-row] input").forEach((inp) => {
      inp.addEventListener("change", () => this.commit(inp.closest("tr").dataset.row, inp.dataset.col, inp.value));
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); inp.blur(); } });
    });
    this.el.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => this.del(b.dataset.del)));
    this.el.querySelectorAll("[data-feature]").forEach((b) => (b.onclick = () => { this.app.closeParams(); this.app.editFeature(b.dataset.feature); }));
    const add = () => this.add();
    $("pp-add").onclick = add;
    this.el.querySelectorAll(".pp-add input").forEach((inp) => inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }));
    if (this.focus) {
      const sel = this.focus.row ? `tr[data-row="${CSS.escape(this.focus.row)}"] [data-col="${this.focus.col}"]` : `.pp-add [data-col="${this.focus.col}"]`;
      const el = this.el.querySelector(sel);
      if (el) el.focus();
      this.focus = null;
    }
  }

  async send(body, focus) {
    try {
      await this.app.action("/api/param", body);
      this.error = null;
      this.focus = focus || null;
      return true;
    } catch (e) {
      this.error = e.message; this.render(); return false;
    }
  }

  async commit(name, col, value) {
    value = value.trim();
    const row = this.rows().find((r) => r.name === name);
    if (!row) return;
    if (col === "name" && value !== name) await this.send({ name, rename: value }, { row: value, col: "expr" });
    else if (col === "expr" && value !== row.expr) {
      if (!value) { this.error = "An expression is needed (delete the row with ×)"; this.render(); return; }
      await this.send({ name, value: normalize(value) }, { row: name, col: "comment" });
    } else if (col === "comment" && value !== row.comment) await this.send({ name, comment: value }, null);
  }

  async add() {
    const get = (c) => this.el.querySelector(`.pp-add [data-col="${c}"]`).value.trim();
    const name = get("name"), expr = get("expr"), comment = get("comment");
    if (!name || !expr) { this.error = "Give the new parameter a name and an expression"; this.render(); return; }
    if (!(await this.send(comment ? { name, value: normalize(expr), comment } : { name, value: normalize(expr) }, null))) return;
    this.focus = { row: null, col: "name" };
    this.render();
    toast(`Added ${name}: use it as P.${name}`);
  }

  async del(name) { await this.send({ name, delete: true }, null); }
}

// a bare number is millimetres
export function normalize(v) { return /^-?\d+(\.\d+)?$/.test(v) ? `${v} * mm` : v; }
