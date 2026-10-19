// Shared by the Mailbox, Achievements and Damage meter: the checker's run log (one fetch for all panels at a time),
// a per-project key, and browser storage that never throws (private windows, blocked storage: values last until the page closes).
import { nameFor } from './workers.js';

let pending = null, cache = [], stamp = 0;

export function loadRuns(ctx, force) {
  if (!force && Date.now() - stamp < 3000) return Promise.resolve(cache);
  if (!pending) pending = ctx.net.get('/api/runs').then((d) => (cache = (d && d.runs) || [])).catch(() => cache)
    .finally(() => { stamp = Date.now(); pending = null; });
  return pending;
}

// a run just finished when verify.running goes from true to false
export function runFinished(prev, s) {
  return !!(prev && prev.verify && prev.verify.running) && !(s.verify && s.verify.running);
}

export const projectKey = (s) => (s && s.project && (s.project.path || s.project.name)) || 'default';

export function readStore(key, fallback) {
  try { const v = JSON.parse(localStorage.getItem(key) || 'null'); return v && typeof v === 'object' ? v : fallback; } catch (e) { return fallback; }
}

export function writeStore(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage off */ }
}

export const dayOf = (ts) => { const d = new Date(ts * 1000); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
export const dateText = (ts) => new Date(ts * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
export const clockText = (ts) => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

// agent ids in a chat sentence -> hero names (same as the chat window)
export const named = (s, t) => ((s && s.agents) || []).reduce((x, a) => { const n = nameFor(a.id); return n && n !== a.id ? x.split(a.id).join(n) : x; }, String(t || ''));
