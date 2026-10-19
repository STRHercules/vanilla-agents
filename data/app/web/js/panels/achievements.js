// Achievements (Y): worked out in the browser from the state and the checker's run log. The list of achievements is the list of
// icons in panels/achievements.json; the numbers needed come from its options. Earned ones are kept per project in this browser,
// with the date; a new one shows the gold toast, plays the level-up sound and its title goes under the controller name.
import { clear, el, icon } from '../dom.js';
import { showToast } from '../toast.js';
import { dateText, loadRuns, projectKey, readStore, runFinished, writeStore } from '../runlog.js';

const hourOf = (ts) => new Date(ts * 1000).getHours();

// order of verdict lines (1 = verified, 0 = failed) in the chat, oldest first
const verdicts = (s) => (s.chat || []).filter((l) => l.type === 'task_verified' || l.type === 'task_failed').map((l) => ({ ok: l.type === 'task_verified', title: l.title || '' }));

function longestStreak(vs) {
  let best = 0, cur = 0;
  for (const v of vs) { cur = v.ok ? cur + 1 : 0; best = Math.max(best, cur); }
  return best;
}

// each rule: (state, runs, options) -> true when earned; "n" is the number shown in its how-to-earn sentence
const RULES = {
  first_blood: { test: (s) => verdicts(s).some((v) => v.ok) || (s.counts && s.counts.done > 0) },
  first_report: { test: (s, runs) => runs.length > 0 },
  green_streak: { n: 'streak', test: (s, runs, o) => longestStreak(verdicts(s)) >= o.streak },
  clean_hands: { test: (s) => {
    const vs = verdicts(s), failed = new Set(vs.filter((v) => !v.ok).map((v) => v.title));
    const sent = new Set((s.tasks || []).filter((t) => t.sent_back && t.sent_back.length).map((t) => t.title));
    return vs.some((v) => v.ok && v.title && !failed.has(v.title) && !sent.has(v.title));
  } },
  comeback: { test: (s) => {
    const failed = new Set();
    for (const v of verdicts(s)) { if (!v.ok) failed.add(v.title); else if (failed.has(v.title)) return true; }
    return false;
  } },
  flawless: { n: 'flawless_checks', test: (s, runs, o) => runs.some((r) => r.verdict === 'VERIFIED' && Array.isArray(r.checks) && r.checks.length >= o.flawless_checks && r.checks.every((k) => k.status === 'VERIFIED')) },
  questmaster: { n: 'questmaster', test: (s, runs, o) => Math.max(verdicts(s).filter((v) => v.ok).length, (s.counts && s.counts.done) || 0) >= o.questmaster },
  champion: { n: 'champion', test: (s, runs, o) => Math.max(verdicts(s).filter((v) => v.ok).length, (s.counts && s.counts.done) || 0) >= o.champion },
  full_house: { n: 'full_house', test: (s, runs, o) => !!s.counts && s.counts.total >= o.full_house && s.counts.done >= s.counts.total },
  party_of_five: { n: 'party', test: (s, runs, o) => (s.agents || []).filter((a) => a.state === 'working').length >= o.party },
  busy_bee: { n: 'busy_actions', test: (s, runs, o) => (s.agents || []).some((a) => (a.actions || 0) >= o.busy_actions) },
  toolsmith: { n: 'tools', test: (s, runs, o) => (s.agents || []).some((a) => Object.keys(a.tools || {}).length >= o.tools) },
  scribe: { n: 'files', test: (s, runs, o) => new Set((s.files || []).map((f) => f.file)).size >= o.files },
  whisperer: { n: 'whispers', test: (s, runs, o) => (s.messages || []).filter((m) => m.from === 'you').length >= o.whispers },
  diplomat: { n: 'answers', test: (s, runs, o) => (s.tasks || []).filter((t) => t.answer).length >= o.answers },
  night_owl: { test: (s) => (s.chat || []).some((l) => l.channel === 'agent' && hourOf(l.ts) < 5) },
  early_bird: { test: (s) => (s.chat || []).some((l) => l.channel === 'agent' && hourOf(l.ts) >= 5 && hourOf(l.ts) < 7) },
};

export function build(ctx, panel) {
  const c = panel.cfg, o = c.options, T = (k, v) => ctx.t(c.text[k], v);
  const ids = Object.keys(c.icons).filter((id) => RULES[id]);
  const head = el('div', 'book-head small grey');
  const grid = el('div', 'ach-grid');
  panel.body.append(head, grid);

  // the newest title under the controller name
  const title = el('div', 'unit-title shadow');
  title.dataset.tip = c.tips.player_title;
  title.hidden = true;
  const uf = ctx.panels.unit_frames, nameEl = uf && uf.root.querySelector('.unit-name');
  if (nameEl) nameEl.after(title);

  let state = null, prev = null, runs = [], key = '', earned = {}, ready = false, drawn = '';
  const n = (id) => (RULES[id].n ? o[RULES[id].n] : '');
  const name = (id) => ctx.t('ach.name_' + id);

  function celebrate(fresh) {
    fresh.slice(0, o.max_toasts).forEach((id, i) => setTimeout(() => {
      showToast(T('toast', { title: name(id) }), 'gold');
      const snd = ctx.sound, ev = snd && snd.settings && snd.settings.events && snd.settings.events.task_verified;
      if (ev && ev.enabled && snd.files.some((f) => f.id === 'level_up')) snd.test('task_verified', { sound: 'level_up' });   // the level-up ding, at the quest sound's volume
    }, i * 1800));
  }

  function evaluate() {
    if (!state || !ready) return;
    const fresh = [];
    for (const id of ids) {
      if (earned[id]) continue;
      let ok = false;
      try { ok = RULES[id].test(state, runs, o); } catch (e) { ok = false; }
      if (ok) { earned[id] = Date.now() / 1000 + fresh.length * 0.001; fresh.push(id); }      // same-moment unlocks keep list order
    }
    if (fresh.length) { writeStore('ach:' + key, earned); celebrate(fresh); }
    draw();
  }

  function draw() {
    const newest = ids.filter((id) => earned[id]).sort((a, b) => earned[b] - earned[a])[0];
    title.hidden = !newest;
    if (newest) title.textContent = ctx.t('ach.title_line', { title: ctx.t('ach.honor_' + newest) });
    const k = JSON.stringify(earned);
    if (k === drawn) return;
    drawn = k;
    head.textContent = T('count', { count: ids.filter((id) => earned[id]).length, total: ids.length });
    clear(grid);
    for (const id of ids) {
      const got = !!earned[id];
      const cell = el('div', 'ach-cell' + (got ? ' earned' : ' locked'));
      const slot = el('div', 'ach-icon');
      slot.append(icon(c.icons[id], 'slot-icon'));
      const col = el('div', 'ach-col');
      col.append(el('div', 'ach-name shadow', name(id)), el('div', 'ach-how small', ctx.t('ach.how_' + id, { n: n(id) })),
        el('div', 'ach-when small', got ? T('earned', { when: dateText(earned[id]) }) : T('locked')));
      cell.append(slot, col);
      cell.dataset.liveTip = '1';
      cell.tipData = { title: name(id), titleColor: got ? 'gold' : 'grey', body: ctx.t('ach.how_' + id, { n: n(id) }) + '\n' + ctx.t('ach.honor_line', { title: ctx.t('ach.honor_' + id) }),
        last: { text: got ? T('earned', { when: dateText(earned[id]) }) : T('locked'), color: got ? 'green' : 'grey' } };
      grid.append(cell);
    }
  }

  async function refresh(force) {
    runs = await loadRuns(ctx, force);
    ready = true;
    evaluate();
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen()) draw(); };

  return function update(s) {
    const pk = projectKey(s);
    state = s;
    if (pk !== key) { key = pk; earned = readStore('ach:' + pk, {}); drawn = ''; ready = false; refresh(); }
    else if (runFinished(prev, s)) refresh(true);
    prev = s;
    evaluate();
  };
}
