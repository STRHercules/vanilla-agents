// V-039/V-053: Resurrect (talk window and raid frame right-click): the server opens a new terminal running `claude --resume <session>`
// or `codex resume <session>` in the folder that session worked in (POST /api/resurrect, the owner's own click; never something a helper
// can do). When the session shows up again (same helper id, or a new helper in the same folder within 10 minutes) the inn and the raid
// frame play a short revive effect.

const WAIT_MS = 10 * 60 * 1000;

export async function resurrect(ctx, id) {
  const r = await ctx.net.post('/api/resurrect', { agent: id });
  if (r && r.ok) {
    const s = ctx.last || {};
    const a = [...(s.agents || []), ...(s.departed || [])].find((x) => x.id === id) || {};
    ctx.reviving = ctx.reviving || new Map();
    ctx.reviving.set(id, { at: Date.now(), cwd: a.cwd || '', known: new Set((s.agents || []).map((x) => x.id)) });
  }
  return r;
}

const quietFor = (a) => (a && a.seen ? Date.now() / 1000 - (typeof a.seen === 'number' ? a.seen : Date.parse(a.seen) / 1000) : 1e9);

// Call with every snapshot (any panel may; it is idempotent). Marks helpers that came back in ctx.revivedAt (id -> ms).
export function checkRevived(ctx, s) {
  ctx.revivedAt = ctx.revivedAt || new Map();
  if (!ctx.reviving || !ctx.reviving.size) return ctx.revivedAt;
  for (const [id, w] of [...ctx.reviving]) {
    if (Date.now() - w.at > WAIT_MS) { ctx.reviving.delete(id); continue; }
    const same = (s.agents || []).find((a) => a.id === id && quietFor(a) < 120);
    const moved = !same && w.cwd && (s.agents || []).find((a) => !w.known.has(a.id) && a.cwd === w.cwd && quietFor(a) < 120);
    const back = same || moved;
    if (back) { ctx.reviving.delete(id); ctx.revivedAt.set(back.id, Date.now()); }
  }
  return ctx.revivedAt;
}

// true once per `seen` set for a helper revived in the last few seconds (each panel / the inn keeps its own set)
export function justRevived(ctx, id, seen, maxAge = 8000) {
  const at = ctx.revivedAt && ctx.revivedAt.get(id);
  if (!at || Date.now() - at > maxAge || seen.has(id + '@' + at)) return false;
  seen.add(id + '@' + at);
  return true;
}

// helpers whose AI session can be resumed: quiet for 15 minutes (a ghost) or gone home, with a known session id
export const isGhost = (a) => !!a && a.state !== 'working' && quietFor(a) > 15 * 60;
export const canResurrect = (a) => !!a && !!a.session && (a.left !== undefined || isGhost(a));
