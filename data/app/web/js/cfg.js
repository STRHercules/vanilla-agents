// Loads the settings bundle from the server and gives panels a text lookup. Nothing visual or textual is hard-coded in JS.
export async function loadConfig() {
  const r = await fetch('/api/config');
  if (!r.ok) throw new Error('could not load settings');
  return r.json();
}

export function fmt(s, vars) {
  return String(s).replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] !== undefined ? vars[k] : ''));
}

export function makeText(cfg) {
  const ui = cfg.strings.ui;
  return (key, vars) => fmt(ui[key] === undefined ? key : ui[key], vars);
}
