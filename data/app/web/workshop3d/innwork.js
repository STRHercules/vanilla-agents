// V-043: different ways helpers do their tasks in the inn. What a step is comes from the command of a Bash step, matched against
// workshop.json inn.tasks (test = tests and builds -> the anvil, ship = commit / push / publish / zip -> a sack to the crates).
// Same rule as classify() in tests/test_inn_tasks.py: shipping is tested first, patterns are case-insensitive.
export function makeTaskKind(tasks) {
  const comp = (list) => (Array.isArray(list) ? list : []).map((s) => { try { return new RegExp(s, 'i'); } catch (e) { return null; } }).filter(Boolean);
  const rules = { ship: comp(tasks && tasks.ship), test: comp(tasks && tasks.test) };
  return (activity) => {
    const m = /^Bash\s+([\s\S]*)$/.exec(activity || ''); if (!m) return null;
    for (const k of ['ship', 'test']) if (rules[k].some((r) => r.test(m[1]))) return k;
    return null;
  };
}

// a doodad-name pattern from the config (null when missing or broken: that kind of place is simply not used)
export function namePattern(s) { try { return s ? new RegExp(s, 'i') : null; } catch (e) { return null; } }

const PASS = /^(verified|passed|pass|ok)$/i;
// the checker's work on this helper's quests: running = a check started and not finished yet (quest still in progress or handed in);
// passed = how many checks of its quests have passed so far (a rise = cheer)
export function questChecks(tasks, agentId) {
  let running = false, passed = 0;
  for (const t of tasks || []) {
    if (!t || t.agent !== agentId) continue;
    for (const o of t.objectives || []) {
      if (o.verdict == null) { if (t.status === 'ready' || t.status === 'progress') running = true; }
      else if (PASS.test(String(o.verdict))) passed++;
    }
  }
  return { running, passed };
}
