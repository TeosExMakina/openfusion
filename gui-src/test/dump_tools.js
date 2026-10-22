// Prints every tool's operation(s) and field keys as JSON, for the Python test that checks
// the GUI against the engine's real parameter names.
import { TOOLS } from "../src/tools.js";
const out = {};
for (const [id, t] of Object.entries(TOOLS)) {
  out[id] = { ops: (t.ops || [t.op]).filter(Boolean), fields: (t.fields || []).map((f) => ({ key: f.key, kind: f.kind, required: !!f.required })) };
}
console.log(JSON.stringify(out));
