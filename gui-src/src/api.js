// Small client for the openfusion gui server.
export class ApiError extends Error {
  constructor(msg, data) { super(msg); this.data = data || {}; }
}

// The server always answers with JSON, errors included. A request that gets no answer at all
// (the server stopped, or crashed) says so plainly instead of the browser's "Load failed".
async function call(path, init) {
  let r;
  try { r = await fetch(path, init); } catch (e) {
    throw new ApiError("No answer from the OpenFusion server (is it still running?). Your file is saved; reopen it to carry on.", { offline: true });
  }
  let d;
  try { d = await r.json(); } catch (e) { d = { error: `The server sent an unreadable answer (${r.status} ${r.statusText})` }; }
  if (!r.ok) throw new ApiError(d.error || r.statusText, d);
  return d;
}

export async function get(path) {
  return call(path, { cache: "no-store" });
}

export async function post(path, body) {
  return call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
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
