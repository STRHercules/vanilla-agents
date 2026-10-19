// Spellbook (P): the project's checks as spells. Click casts Verify now; a dark clock sweeps over the icon while the check runs.
import { clear, el, icon } from '../dom.js';
import { showToast } from '../toast.js';
import { ago, colorVar, qualityOf, took } from './bookutil.js';

export function build(ctx, panel) {
  const c = panel.cfg, tips = ctx.cfg.strings.tooltips, marks = ctx.cfg.metaphors.spell_marks;
  const head = el('div', 'book-head small grey');
  const grid = el('div', 'spell-grid');
  grid.style.gridTemplateColumns = 'repeat(' + c.options.columns + ', 1fr)';
  const note = el('div', 'book-note small');
  panel.body.append(head, grid, note);
  let checks = [], problem = false, state = null, cells = {}, timer = null, base = 0, at = 0;

  const markOf = (verdict) => {
    if (!verdict) return 'NEVER';
    const q = qualityOf(ctx, verdict);
    return marks[q] ? q : 'BLOCKED';
  };

  async function load() {
    try {
      const d = await ctx.net.get('/api/checks');
      checks = d.checks; problem = !!d.problem;
    } catch (e) { checks = []; problem = true; }
    draw();
  }

  function spellTip(k, running) {
    const d = tips['spell_' + k.id] || tips[c.tips.default];
    const m = marks[markOf(k.last && k.last.verdict)];
    let text = ctx.t(m.word);
    if (k.last && k.last.ts) text += ' - ' + ago(ctx, k.last.ts) + (k.last.seconds ? ', ' + ctx.t(c.text.took, { seconds: took(ctx, k.last.seconds) }) : '');
    return { title: d.title, body: d.body, use: d.use, last: { text: running ? ctx.t(c.text.running) : ctx.t(c.text.last, { result: text }), color: running ? 'gold' : m.color } };
  }

  function draw() {
    clear(grid); cells = {};
    const running = state && state.verify && state.verify.running ? state.verify.check : '';
    head.textContent = checks.length ? ctx.t(c.text.count, { count: checks.length }) : '';
    for (let i = 0; i < c.options.slots; i++) {
      const k = checks[i];
      const cell = el('div', 'spell');
      const slot = el('button', 'slot spell-slot', null, { type: 'button' });
      if (k) {
        const sp = c.spells.find((x) => x.id === k.id);
        const isRun = running === k.id;
        slot.append(icon(sp ? sp.icon : c.icons.default, 'slot-icon'));
        const cd = el('div', 'cooldown'); const left = el('div', 'cooldown-left small shadow');
        cd.hidden = left.hidden = !isRun;
        slot.append(cd, left);
        const m = marks[markOf(k.last && k.last.verdict)];
        const last = el('div', 'spell-last small');
        last.append(el('span', 'spell-mark', isRun ? '⌛' : m.mark), el('span', null, isRun ? ctx.t(c.text.running) : k.last ? ctx.t(m.word) + (k.last.seconds ? ' - ' + ctx.t(c.text.took, { seconds: took(ctx, k.last.seconds) }) : '') : ctx.t(c.text.never)));
        last.style.color = isRun ? colorVar('gold') : colorVar(m.color);
        const info = el('div', 'spell-info');
        const name = el('div', 'spell-name shadow', k.label);
        info.append(name, last);
        if (!k.registered) info.append(el('div', 'small grey', ctx.t(c.text.not_registered)));
        cell.append(slot, info);
        cell.classList.toggle('spell-off', !k.registered);
        cell.dataset.liveTip = '1';
        cell.tipData = spellTip(k, isRun);
        cell.addEventListener('click', () => cast(k));
        cells[k.id] = { cd, left };
      } else {
        cell.append(slot);
        cell.classList.add('spell-empty');
      }
      grid.append(cell);
    }
    note.textContent = problem ? ctx.t(c.text.problem) : !checks.length ? ctx.t(c.text.empty) : state && state.verify && state.verify.paused ? ctx.t(c.text.paused) : '';
    note.style.color = colorVar('grey');
    tick();
  }

  function cast(k) {
    const v = (state && state.verify) || {};
    if (!k.registered) return showToast(ctx.t(c.text.not_registered), 'grey');
    if (v.paused) return showToast(ctx.t(c.text.paused), 'grey');
    if (v.running) return;
    ctx.actions.verify(k.id);
  }

  // the dark clock: elapsed part clear, remaining part dark; centre shows the time left (a guess from the expected time, like the cast bar)
  function tick() {
    const v = state && state.verify;
    if (!v || !v.running || !cells[v.check]) return;
    const exp = Math.max(1, v.expected), e = base + (Date.now() - at) / 1000, p = Math.min(0.95, e / exp);
    cells[v.check].cd.style.setProperty('--p', (p * 360).toFixed(1) + 'deg');
    cells[v.check].left.textContent = took(ctx, Math.max(0, exp - e));
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen()) load(); };

  let loaded = false, wasPaused = false;
  return function update(s) {
    const was = !!(state && state.verify && state.verify.running), now = !!(s.verify && s.verify.running), paused = !!(s.verify && s.verify.paused);
    state = s;
    if (now) { base = s.verify.elapsed; at = Date.now(); if (!timer) timer = setInterval(tick, 250); }
    else if (timer) { clearInterval(timer); timer = null; }
    if (!loaded) { loaded = true; load(); }
    else if (was && !now) load();          // a run just finished: its result is now in the run log
    else if (was !== now || paused !== wasPaused) draw();
    wasPaused = paused;
  };
}
