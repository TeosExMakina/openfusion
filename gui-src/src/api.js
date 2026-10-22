// Small client for the openfusion gui server.
export class ApiError extends Error {
  constructor(msg, data) { super(msg); this.data = data || {}; }
}

export async function get(path) {
  const r = await fetch(path, { cache: "no-store" });
  const d = await r.json();
  if (!r.ok) throw new ApiError(d.error || r.statusText, d);
  return d;
}

export async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const d = await r.json();
  if (!r.ok) throw new ApiError(d.error || r.statusText, d);
  return d;
}

export const $ = (id) => document.getElementById(id);
export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let toastTimer = null;
export function toast(msg, isErr = false, ms = 1800) {
  const t = $("toast");
  t.textContent = msg; t.className = "toast" + (isErr ? " err" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), isErr ? Math.max(ms, 4000) : ms);
}

// Python string literal for a name like +Z or post.side
export const q = (s) => JSON.stringify(String(s));
