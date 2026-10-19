// Character sheet (C): one agent. Portrait from its own hero model, level and experience (actions), pass rate,
// tools used, and the task timeline (past steps -> current -> next quest) with Force Play (= Verify now) and step buttons.
import { bar, clear, el } from '../dom.js';
import { historyOf, nameFor, setPortrait } from '../workers.js';

const short = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

export function build(ctx, panel) {
  const c = panel.cfg;
  const t = (k, v) => ctx.t(c.text[k], v);
  const box = el('div', 'char');
  panel.body.append(box);
  let last = null;
  window.addEventListener('workers-ready', () => last && update(last, true));

  const step = (dir) => {
    const ids = (last ? last.agents : []).map((a) => a.id);
    if (!ids.length) return;
    const cur = Math.max(0, ids.indexOf(ctx.selected || ids[0]));
    ctx.select(ids[(cur + dir + ids.length) % ids.length]);
  };

  function timeline(s, a) {
    const tl = el('div', 'timeline');
    tl.dataset.tip = c.tips.timeline;
    const track = el('div', 'tl-track');
    const past = historyOf(a.id).slice(0, -1).slice(-2);
    track.append(el('div', 'tl-arrow tl-left'), el('div', 'tl-line'), el('div', 'tl-arrow tl-right'));
    const pts = el('div', 'tl-points');
    past.forEach((p) => { const d = el('div', 'tl-dot'); d.dataset.liveTip = '1'; d.tipData = { title: t('past'), body: p }; pts.append(d); });
    const curDot = el('div', 'tl-dot tl-now');
    pts.append(el('div', 'tl-tick'), curDot, el('div', 'tl-tick'));
    track.append(pts);
    const task = s.tasks.find((x) => x.id === a.task);
    const next = s.tasks.find((x) => x.status === 'new' && x.id !== a.task);
    const labels = el('div', 'tl-labels');
    labels.append(el('div', 'tl-lab small', t('past')), el('div', 'tl-lab tl-lab-now small gold', t('current')), el('div', 'tl-lab small', next ? short(next.title, 22) : t('next_none')));
    const objs = task ? task.objectives : [];
    const pct = objs.length ? (100 * objs.filter((o) => o.verdict === 'VERIFIED').length) / objs.length : a.state === 'working' ? 50 : 0;
    const prog = bar('--xp');
    prog.set(pct, task ? short(task.title, 30) : short(a.activity || t('idle'), 30));
    const ctrl = el('div', 'tl-ctrl');
    const force = el('button', 'btn btn-metal', t('force'), { type: 'button' });
    force.dataset.tip = c.tips.force;
    force.addEventListener('click', () => ctx.actions && ctx.actions.verify());
    const mk = (cls, tip, dir) => { const b = el('button', 'round-btn ' + cls, null, { type: 'button' }); b.dataset.tip = tip; b.addEventListener('click', () => step(dir)); return b; };
    ctrl.append(force, el('div', 'tl-steplabel small', t('back')), mk('rb-back', c.tips.back, -1), mk('rb-fwd', c.tips.forward, 1));
    tl.append(track, labels, prog.root, ctrl);
    return tl;
  }

  let sig = '';
  function update(s, force) {
    last = s;
    const a = s.agents.find((x) => x.id === ctx.selected) || s.agents[0];
    const k = JSON.stringify([a, a && s.tasks.find((x) => x.id === a.task), a && historyOf(a.id).length, s.tasks.filter((x) => x.status === 'new').map((x) => x.id), nameFor(a ? a.id : '')]);
    if (k === sig && !force) return;      // nothing this sheet shows has changed
    sig = k;
    clear(box);
    if (!a) {
      box.append(el('div', 'empty grey', t('none')));
      return;
    }
    const top = el('div', 'char-top');
    const ring = el('div', 'ring ring-big');
    ring.dataset.tip = c.tips.portrait;
    const pic = el('img', 'ring-pic', null, { alt: '', draggable: 'false' });
    pic.src = '/assets/' + c.icons.portrait;
    setPortrait(pic, a.id);
    const level = 1 + Math.min(59, Math.floor(a.actions / 10));
    ring.append(pic, el('div', 'ring-frame'), el('div', 'ring-badge', t('level', { n: level })));
    const who = el('div', 'char-who');
    who.append(el('div', 'char-name gold shadow', nameFor(a.id)), el('div', 'small grey', t('role', { role: a.role || '?' })));
    who.append(el('div', 'small ' + (a.state === 'working' ? 'white' : 'grey'), a.state === 'working' ? short(t('doing', { activity: a.activity || '...' }), 46) : t('idle')));
    const xp = bar('--xp', c.tips.xp);
    xp.set((a.actions % 10) * 10, t('xp', { done: a.actions % 10, total: 10 }));
    const done = a.passed + a.failed;
    const rate = bar('--health', c.tips.rate);
    rate.set(done ? (100 * a.passed) / done : 0, done ? t('rate', { passed: a.passed, total: done }) : t('rate_none'));
    who.append(xp.root, rate.root);
    top.append(ring, who);
    const work = el('div', 'char-line', t('work_done', { count: a.actions }));
    const tools = el('div', 'char-tools');
    tools.dataset.tip = c.tips.tools;
    tools.append(el('div', 'gold', t('tools')));
    const names = Object.keys(a.tools);
    if (!names.length) tools.append(el('div', 'small grey', t('tools_none')));
    names.slice(0, 5).forEach((n) => { const r = el('div', 'tool small'); r.append(el('span', 'tool-check', '✓'), el('span', null, n + ' x' + a.tools[n])); tools.append(r); });
    box.append(top, work, tools, timeline(s, a));
  }
  return update;
}
