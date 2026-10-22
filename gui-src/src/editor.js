// Script pane: CodeMirror with Python highlighting. Saves go through the server
// with the hash of the text they started from, so a GUI edit is never overwritten.
import { EditorView, basicSetup } from "codemirror";
import { python } from "@codemirror/lang-python";
import { EditorSelection } from "@codemirror/state";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { $ } from "./api.js";

const style = HighlightStyle.define([
  { tag: t.comment, color: "var(--ink-2)", fontStyle: "italic" },
  { tag: [t.string, t.special(t.string)], color: "var(--ok)" },
  { tag: [t.number, t.bool, t.null], color: "var(--accent)" },
  { tag: [t.keyword, t.operatorKeyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword], color: "var(--sel)", fontWeight: "500" },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "var(--ink)", fontWeight: "500" },
  { tag: [t.propertyName, t.attributeName], color: "var(--ink)" },
  { tag: t.variableName, color: "var(--ink)" },
  { tag: t.operator, color: "var(--ink-2)" },
]);

const theme = EditorView.theme({
  "&": { color: "var(--ink)", backgroundColor: "var(--panel)" },
  ".cm-content": { caretColor: "var(--ink)" },
  ".cm-gutters": { backgroundColor: "var(--panel)", color: "var(--ink-2)", border: "none" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--accent) 8%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--ink)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "color-mix(in srgb, var(--sel) 30%, transparent) !important" },
  ".cm-cursor": { borderLeftColor: "var(--ink)" },
});

export class ScriptEditor {
  constructor(app) {
    this.app = app; this.base = null; this.dirty = false; this.loaded = "";
    this.view = new EditorView({
      parent: $("editor"),
      extensions: [basicSetup, python(), theme, syntaxHighlighting(style), EditorView.lineWrapping,
        EditorView.updateListener.of((u) => { if (u.docChanged && !this.setting) this.markDirty(true); })],
    });
    $("editor-save").addEventListener("click", () => this.save());
  }

  markDirty(d) {
    this.dirty = d;
    $("editor-state").textContent = d ? "Unsaved changes (Cmd+S)" : "In sync with the file";
  }

  load(text, hash) {
    if (this.dirty) { if (text !== this.loaded) $("editor-state").textContent = "The file changed; save to overwrite or reload"; return; }
    this.base = hash;
    if (text === this.view.state.doc.toString()) { this.loaded = text; return; }
    this.setting = true;
    const sel = this.view.state.selection;
    this.view.dispatch({ changes: { from: 0, to: this.view.state.doc.length, insert: text },
      selection: sel.main.head <= text.length ? sel : EditorSelection.cursor(0) });
    this.setting = false; this.loaded = text;
    this.markDirty(false);
  }

  reload() { this.dirty = false; this.load(this.app.state.text, this.app.state.hash); }

  async save() {
    if (!this.dirty) return;
    try {
      await this.app.action("/api/script", { text: this.view.state.doc.toString(), base: this.base });
      this.dirty = false; this.markDirty(false);
    } catch (e) { $("editor-state").textContent = e.message; }
  }

  showLines(a, b) {
    const doc = this.view.state.doc;
    if (a < 1 || a > doc.lines) return;
    const from = doc.line(a).from, to = doc.line(Math.min(b || a, doc.lines)).to;
    this.view.dispatch({ selection: EditorSelection.range(from, to), effects: EditorView.scrollIntoView(from, { y: "center" }) });
  }
}
