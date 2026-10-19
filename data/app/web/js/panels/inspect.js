// Inspect (N): the project's changed files worn as gear. Left the item slots (quality colour = size of the change, letter = kind), right the selected file's diff.
// api.open(agentId) opens it filtered to the files that helper touched (s.files); api.open() shows everything.
// V-045 api.openBranch({branch, base, agent}) shows what a competing helper's branch changed since it left base (GET /api/diff?branch=&base=).
import { clear, el, icon, assetUrl } from '../dom.js';
import { nameFor } from '../workers.js';

const KIND = { M: ['status_m', 'modified'], A: ['status_a', 'added'], D: ['status_d', 'deleted'], R: ['status_r', 'renamed'], '?': ['status_u', 'untracked'] };

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v), o = c.options;
  panel.root.classList.add('ins');
  const left = el('div', 'ins-left'), right = el('div', 'ins-right');
  const head = el('div', 'ins-head small'), list = el('div', 'ins-list');
  const dhead = el('div', 'ins-dhead small'), diff = el('div', 'ins-diff');
  diff.dataset.tip = c.tips.diff;
  left.append(head, list);
  right.append(dhead, diff);
  panel.body.append(left, right);
  panel.root.style.setProperty('--ins-bar', 'url("' + assetUrl(c.icons.bar) + '")');
  panel.root.style.setProperty('--ins-bar-border', 'url("' + assetUrl(c.icons.bar_border) + '")');

  let files = null, error = '', agent = null, selected = null, state = null, cursor = null, lastLoad = 0, timer = null, diffKey = '', branch = null;
  const q = (extra) => (branch ? '?branch=' + encodeURIComponent(branch.branch) + (branch.base ? '&base=' + encodeURIComponent(branch.base) : '') + (extra ? '&' + extra : '') : (extra ? '?' + extra : ''));

  const kindOf = (f) => { const s = (f.status || '?').replace(/\s/g, ''); return KIND[s[0]] ? s[0] : (s.includes('?') ? '?' : 'M'); };
  const size = (f) => (parseInt(f.added, 10) || 0) + (parseInt(f.removed, 10) || 0);
  const quality = (n) => n >= o.huge ? 'q-epic' : n >= o.large ? 'q-rare' : n >= o.medium ? 'q-uncommon' : 'q-poor';
  const norm = (p) => String(p || '').replace(/\\/g, '/');
  // the feed hides the user name in paths ('[user]'): match it as any one folder name
  const samePath = (fromFeed, real) => new RegExp('^' + norm(fromFeed).replace(/\/+$/, '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\[user\\\]/g, '[^/]+') + '/?$', 'i').test(norm(real));
  function touchedBy(id) {
    const mine = ((state && state.files) || []).filter((x) => x.agent === id).map((x) => norm(x.file));
    return (f) => { const r = norm(f.file); return mine.some((m) => m === r || m.endsWith('/' + r) || r.endsWith('/' + m)); };
  }

  async function load() {
    lastLoad = Date.now();
    try { const r = await ctx.net.get('/api/diff' + q()); files = r.files || []; error = r.error || ''; } catch (e) { files = []; error = e.message; }
    drawList();
    const f = shown().find((x) => x.file === selected);
    if (!f) { selected = null; drawDiff(null); } else if (keyOf(f) !== diffKey) loadDiff(f);
  }
  const keyOf = (f) => f.file + '|' + f.added + '|' + f.removed + '|' + f.status;
  const shown = () => (files || []).filter(agent && !branch ? touchedBy(agent) : () => true).slice(0, o.max_files);

  function drawList() {
    clear(head); clear(list);
    if (branch) head.append(el('span', 'gold ins-filter ins-branch', t('branch_head', { name: nameFor(branch.agent) || branch.agent, branch: branch.branch, base: branch.base || 'HEAD' })));
    else if (agent) {
      const fl = el('span', 'gold ins-filter', t('filtered', { text: nameFor(agent) || agent }));
      fl.dataset.tip = c.tips.filter;
      const all = el('button', 'btn btn-metal ins-all', t('show_all'), { type: 'button' });
      all.dataset.tip = c.tips.show_all;
      all.addEventListener('click', () => { agent = null; drawList(); if (!selected) drawDiff(null); });
      head.append(fl, all);
      const a = state && [...(state.agents || []), ...(state.departed || [])].find((x) => x.id === agent);      // V-038: which checkout it works in
      if (a && a.branch) { const p = el('span', 'ins-place small', t(a.worktree ? 'place_wt' : 'place', { branch: a.branch, worktree: a.worktree || '' })); p.dataset.tip = c.tips.place; head.append(p);
        if (a.root && state.project && !samePath(a.root, state.project.path)) head.append(el('span', 'ins-place small orange', t('other_checkout'))); }
    }
    if (error) { list.append(el('div', 'ins-error red', t('error', { error }))); return; }
    if (files === null) { list.append(el('div', 'grey small ins-empty', t('loading'))); return; }
    const fs = shown();
    if (!agent || branch) head.append(el('span', 'grey', t('count', { count: fs.length })));
    if (!fs.length) { list.append(el('div', 'grey small ins-empty', agent ? t('empty_agent', { text: nameFor(agent) || agent }) : t('empty'))); return; }
    for (const f of fs) {
      const k = kindOf(f), n = size(f), q = f.added === undefined ? 'q-poor' : quality(n);
      const row = el('button', 'ins-row ' + q + (f.file === selected ? ' sel' : ''), null, { type: 'button' });
      row.dataset.tip = c.tips.slot;
      const slot = el('div', 'ins-slot');
      slot.append(icon(c.icons[KIND[k][1]], 'ins-icon'), el('span', 'ins-letter', k));
      const parts = norm(f.file).split('/'), base = parts.pop();
      const txt = el('div', 'ins-txt');
      txt.append(el('div', 'ins-name', base), el('div', 'ins-dir small grey', (parts.join('/') ? parts.join('/') + '/  ' : '') + t(KIND[k][0])));
      const bar = el('div', 'ins-bar'), fill = el('div', 'ins-fill');
      fill.style.width = (f.added === undefined ? 100 : Math.max(4, Math.min(100, 100 * Math.log10(1 + n) / Math.log10(1 + o.huge)))) + '%';
      bar.append(fill);
      txt.append(bar);
      const num = el('div', 'ins-num small');
      if (f.added !== undefined) num.append(el('div', 'ins-plus', t('added', { n: f.added })), el('div', 'ins-minus', t('removed', { n: f.removed })));
      row.append(slot, txt, num);
      row.addEventListener('click', () => { selected = f.file; drawList(); loadDiff(f); });
      list.append(row);
    }
  }

  async function loadDiff(f) {
    diffKey = keyOf(f);
    clear(dhead); clear(diff);
    dhead.append(el('span', 'gold', norm(f.file)));
    diff.append(el('div', 'grey small ins-empty', t('loading')));
    let r;
    try { r = await ctx.net.get('/api/diff' + q('file=' + encodeURIComponent(f.file))); } catch (e) { r = { error: e.message }; }
    if (selected !== f.file) return;
    drawDiff(r, f);
  }

  function drawDiff(r, f) {
    clear(diff);
    if (!r) { clear(dhead); if (!error && shown().length) diff.append(el('div', 'grey ins-empty ins-pick', t('pick'))); return; }
    if (r.error) { diff.append(el('div', 'ins-error red', t('error', { error: r.error }))); return; }
    if (r.truncated) dhead.append(el('span', 'small grey ins-trunc', t('truncated')));
    const lines = (r.diff || '').split('\n');
    if (lines.length && lines[lines.length - 1] === '') lines.pop();
    if (!lines.length) { diff.append(el('div', 'grey ins-empty', t('no_diff'))); return; }
    const hunked = lines.some((l) => l.startsWith('@@'));
    let inHunk = !hunked, a = 1, b = 1;
    const frag = document.createDocumentFragment();
    const add = (cls, la, lb, text) => {
      const d = el('div', 'ins-ln ' + cls);
      d.append(el('span', 'ins-no', la), el('span', 'ins-no', lb), el('span', 'ins-code', text));
      frag.append(d);
    };
    for (const l of lines) {
      const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(l);
      if (m) { inHunk = true; a = +m[1]; b = +m[2]; add('ins-hunk', '', '', l); continue; }
      if (!inHunk) continue;        // git's header lines (diff --git, index, ---/+++)
      if (l.startsWith('+')) add('ins-add', '', b++, l);
      else if (l.startsWith('-')) add('ins-del', a++, '', l);
      else if (l.startsWith('\\')) add('ins-meta', '', '', l);
      else add('ins-ctx', a++, b++, l);
    }
    diff.append(frag);
    diff.scrollTop = 0;
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen()) load(); };

  function open(agentId) {
    agent = agentId || null;
    branch = null;
    if (!panel.isOpen()) toggle();
    drawList();
    load();
    return panel;
  }

  function update(s) {
    state = s;
    if (!panel.isOpen() || s.cursor === cursor) return;
    cursor = s.cursor;
    clearTimeout(timer);       // at most one git call every 3 s while things are happening
    timer = setTimeout(load, Math.max(0, 3000 - (Date.now() - lastLoad)));
  }

  function openBranch(b) {
    branch = b && b.branch ? { branch: b.branch, base: b.base || '', agent: b.agent || '' } : null;
    agent = branch ? branch.agent : null;
    selected = null; files = null; diffKey = '';
    if (!panel.isOpen()) toggle();
    if (ctx.panels.inspect && ctx.panels.inspect.front) ctx.panels.inspect.front();
    drawList(); drawDiff(null);
    load();
    return panel;
  }

  drawDiff(null);
  return { update, open, openBranch };
}
