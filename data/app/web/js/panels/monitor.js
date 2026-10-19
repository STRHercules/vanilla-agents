// System monitoring panel: how many helpers are working, whether the event stream is live, and what the checker is doing.
// V-044: a fourth row "CI" (GitHub Actions on this branch) appears only while s.ci exists; red when the latest run failed.
import { el, icon } from '../dom.js';
import { ciLine } from '../ci.js';

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  const head = el('div', 'mon-head');
  const title = el('div', 'mon-title', t('heading'));
  const since = el('div', 'mon-since small');
  head.append(title, since);
  const rows = {};
  for (const k of ['agents', 'feed', 'checker', 'ci']) {
    const r = el('div', 'mon-row');
    r.dataset.tip = c.tips[k];
    const pill = el('div', 'mon-pill');
    const fill = el('div', 'mon-fill');
    const val = el('div', 'mon-val');
    pill.append(fill, val);
    r.append(icon(c.icons[k], 'mon-icon'), el('div', 'mon-label', t(k)), pill);
    rows[k] = { fill, val, pill };
  }
  const box = el('div', 'mon-box');
  box.append(head, rows.agents.pill.parentNode, rows.feed.pill.parentNode, rows.checker.pill.parentNode, rows.ci.pill.parentNode);
  const ciRow = rows.ci.pill.parentNode;
  ciRow.hidden = true;
  panel.body.append(box);
  let online = true, lastChange = Date.now(), lastCursor = null, state = null;

  const set = (k, pct, text, kind) => { const r = rows[k]; r.fill.style.width = pct + '%'; r.val.textContent = text; r.pill.className = 'mon-pill mon-' + kind; };
  function draw() {
    if (!state) return;
    const total = state.agents.length, n = state.agents.filter((a) => a.state === 'working').length;
    set('agents', total ? (100 * n) / total : 0, t('load', { n, total }), n ? 'ok' : 'quiet');
    set('feed', online ? 100 : 100, online ? t('live') : t('offline'), online ? 'ok' : 'bad');
    const v = state.verify || {};
    set('checker', 100, v.paused ? t('paused') : v.running ? t('running') : t('idle'), v.paused ? 'warn' : v.running ? 'busy' : 'ok');
    const ci = state.ci;
    ciRow.hidden = !ci;
    panel.root.classList.toggle('mon-has-ci', !!ci);
    if (ci) {
      const l = ciLine(ctx, ctx.cfg.panels.quest_log.text, ci);
      set('ci', 100, t('ci_' + l.state), { passed: 'ok', failed: 'bad', running: 'busy', cancelled: 'quiet', none: 'quiet', unknown: 'warn' }[l.state]);
      ciRow.dataset.liveTip = '1';
      ciRow.tipData = { ...ctx.cfg.strings.tooltips[c.tips.ci], body: l.text + '. ' + ctx.cfg.strings.tooltips[c.tips.ci].body };
    }
    since.textContent = t('since', { seconds: Math.min(999, Math.round((Date.now() - lastChange) / 1000)) });
  }
  setInterval(draw, 1000);
  const update = (s) => { if (s.cursor !== lastCursor) { lastCursor = s.cursor; lastChange = Date.now(); } state = s; draw(); };
  ctx.monitorOnline = (v) => { online = v; draw(); };
  return update;
}
