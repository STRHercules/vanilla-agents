// Talents (T): a read-only TalentFrame for the selected helper (or the first one). Three trees:
// Tools (one talent per tool it used, rank = uses, 5 at most), Trust (tiers of its quests: never / ask / auto / you said yes),
// Results (quests passed and failed, checks passed, quests waiting for you). Respec is Claude's own permissions, not this window.
import { assetUrl, clear, el, icon } from '../dom.js';
import { nameFor, setPortrait } from '../workers.js';

const TREES = ['tools', 'trust', 'results'];
const MAX_RANK = 5;
const COLS = 4;

function toolIcon(name) {      // tool name -> icon key in panels/talents.json
  const n = String(name).toLowerCase();
  if (n === 'read' || n === 'notebookread') return 'tool_read';
  if (n.includes('edit')) return 'tool_edit';
  if (n === 'write') return 'tool_write';
  if (n === 'bash' || n.includes('shell')) return 'tool_bash';
  if (n === 'grep' || n === 'glob' || n === 'ls') return 'tool_search';
  if (n.startsWith('web')) return 'tool_web';
  if (n === 'task' || n === 'agent') return 'tool_task';
  return 'tool_other';
}

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v), tips = ctx.cfg.strings.tooltips;
  let tree = 'tools', last = null;

  const head = el('div', 'tal-head');
  const face = el('div', 'tal-face');
  const pic = el('img', 'tal-pic', null, { alt: '', draggable: 'false' });
  face.append(pic);
  const who = el('div', 'tal-who');
  const name = el('div', 'gold shadow tal-name');
  const points = el('div', 'small tal-points');
  who.append(name, points);
  head.append(face, who);

  const art = el('div', 'tal-tree');
  for (const p of ['tl', 'tr', 'bl', 'br']) art.append(el('img', 'tal-bg tal-bg-' + p, null, { alt: '', draggable: 'false' }));
  const grid = el('div', 'tal-grid');
  const empty = el('div', 'tal-empty shadow');
  const label = el('div', 'tal-label shadow');
  art.append(grid, empty, label);

  const tabs = el('div', 'tal-tabs');
  const tabBtns = {};
  for (const k of TREES) {
    const b = el('button', 'tal-tab', '', { type: 'button' });
    b.dataset.tip = c.tips.tab;
    b.addEventListener('click', () => { tree = k; if (last) update(last); });
    tabBtns[k] = b;
    tabs.append(b);
  }
  const foot = el('div', 'tal-foot small');
  foot.textContent = t('footer');
  foot.dataset.tip = c.tips.footer;
  const none = el('div', 'empty grey');
  panel.body.append(head, art, tabs, foot, none);

  // one talent: square slot + icon, rank badge; rank 0 is greyed like an unlearned talent
  function talent(o) {
    const cell = el('div', 'tal-cell');
    cell.style.gridColumn = String(o.col + 1);
    cell.style.gridRow = String(o.row + 1);
    const slot = el('div', 'tal-slot' + (o.rank ? '' : ' tal-off') + (o.max && o.rank >= o.max ? ' tal-max' : ''));
    slot.append(icon(c.icons[o.icon], 'tal-icon'));
    const badge = el('div', 'tal-rank');
    badge.style.backgroundImage = 'url("' + assetUrl(c.icons.rank) + '")';
    badge.append(el('span', '', o.badge));
    slot.append(badge);
    cell.append(slot);
    const tip = tips[o.tip] || { title: '', body: '' };
    cell.dataset.liveTip = '1';
    cell.tipData = { title: o.title, body: o.line + ' ' + tip.body, use: tip.use };
    return cell;
  }

  function mine(s, id) { return s.tasks.filter((q) => q.agent === id); }

  function trees(s, a) {
    const qs = mine(s, a.id);
    const tools = Object.entries(a.tools || {}).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0])).slice(0, COLS * 6);
    const out = {};
    out.tools = tools.map(([n, uses], i) => {
      const r = Math.min(uses, MAX_RANK);
      return { col: i % COLS, row: Math.floor(i / COLS), icon: toolIcon(n), rank: r, max: MAX_RANK, badge: t('rank_short', { n: r }), title: n,
        line: t('rank', { n: r }) + '. ' + t('uses', { count: uses }), tip: c.tips.tool };
    });
    const tier = { never: 0, ask: 0, auto: 0, yours: 0 };
    for (const q of qs) {
      if (q.tier && tier[q.tier.tier] !== undefined) tier[q.tier.tier] += 1;
      else if (q.approved) tier.yours += 1;
    }
    const cnt = (k, n, col, row) => ({ col, row, icon: k, rank: n, badge: t('count', { count: n }), title: t(k), line: t('quests', { count: n }), tip: c.tips[k] });
    out.trust = [cnt('auto', tier.auto, 0, 0), cnt('ask', tier.ask, 1, 0), cnt('never', tier.never, 3, 0), cnt('yours', tier.yours, 1, 2)];
    const checks = qs.flatMap((q) => q.objectives || []).filter((o) => o.verdict);
    const ok = checks.filter((o) => o.verdict === 'VERIFIED').length;
    const ready = qs.filter((q) => q.status === 'ready' && !q.approved).length;
    out.results = [cnt('passed', a.passed || 0, 0, 0), cnt('failed', a.failed || 0, 3, 0),
      { ...cnt('checks', ok, 1, 1), line: t('checks_of', { passed: ok, total: checks.length }) }, cnt('ready', ready, 1, 3)];
    return out;
  }

  function update(s) {
    last = s;
    if (!panel.isOpen()) return;
    const a = s.agents.find((x) => x.id === ctx.selected) || s.agents[0];
    const has = !!a;
    head.hidden = art.hidden = tabs.hidden = !has;
    none.hidden = has;
    if (!has) { none.textContent = t('none'); return; }
    const all = trees(s, a);
    const spent = (k) => all[k].reduce((n, o) => n + o.rank, 0);
    if (pic.dataset.who !== a.id) { setPortrait(pic, a.id); pic.dataset.who = a.id; }
    name.textContent = t('who', { title: nameFor(a.id), role: a.role || '?' });
    points.textContent = t('points', { n: TREES.reduce((n, k) => n + spent(k), 0) });
    const bgs = art.querySelectorAll('.tal-bg');
    TREES.forEach((k) => {
      if (k === tree) ['tl', 'tr', 'bl', 'br'].forEach((p, j) => { const src = assetUrl(c.icons['bg_' + k + '_' + p]); if (bgs[j].getAttribute('src') !== src) bgs[j].src = src; });
      tabBtns[k].textContent = t('tab_' + k);
      tabBtns[k].classList.toggle('tal-tab-on', k === tree);
    });
    clear(grid);
    all[tree].forEach((o) => grid.append(talent(o)));
    empty.textContent = tree === 'tools' && !all.tools.length ? t('tools_none') : '';
    label.textContent = t('tree_points', { title: t('tab_' + tree), n: spent(tree) });
  }

  window.addEventListener('workers-ready', () => last && update(last));
  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen() && last) update(last); };
  return update;
}
