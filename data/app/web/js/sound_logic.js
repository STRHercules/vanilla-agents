// Pure: which sound events should play for one batch of server updates. No browser APIs, so Python tests can run it with node.
// table = sounds.json "triggers" (feed event type -> sound event). agents = [{id,state}]. pcts = used_pct before / after (or null).
export function soundEventsFor(table, events, prevAgents, agents, prevPct, pct, lowPct) {
  const was = new Map((prevAgents || []).map((a) => [a.id, a.state]));
  let fin = (agents || []).filter((a) => a.state === 'idle' && was.get(a.id) === 'working').length;
  const crossed = typeof pct === 'number' && pct >= lowPct && !(typeof prevPct === 'number' && prevPct >= lowPct);
  const out = [];
  for (const ev of events || []) {
    const name = table[ev.type];
    if (!name) continue;
    if (name === 'agent_finished') { if (fin > 0) { fin--; out.push(name); } }
    else if (name === 'usage_low') { if (crossed && !out.includes(name)) out.push(name); }
    else out.push(name);
  }
  return out;
}
