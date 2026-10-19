// Bounty Board (H): an Auction House list of quests. Bounties = new quests no helper has taken (quality colour by size, risk column),
// with a "Post a bounty" box (POST /api/quest_new). Taken = quests a helper is on, and who. The loot rolls (lootroll.js) borrow this panel's wording.
// V-044 Imported = proposals from GitHub / Jira / Linear (s.bounties): a source filter, Open link, and the owner's "Make it a quest"
// (POST /api/bounty/quest -> a normal unapproved quest) or Hide. Helpers never see these until they are quests.
import { assetUrl, clear, el, icon } from '../dom.js';
import { nameFor } from '../workers.js';
import { initLoot, quality } from './lootroll.js';

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  let tab = 'open', last = null;

  const cols = el('div', 'ah-cols');
  const list = el('div', 'ah-list');
  const note = el('div', 'small grey ah-note');

  const post = el('div', 'ah-post');
  const title = el('input', 'esc-input ah-in', null, { type: 'text', maxlength: '200', placeholder: t('post_name') });
  const why = el('input', 'esc-input ah-in ah-why', null, { type: 'text', maxlength: '600', placeholder: t('post_why') });
  const send = el('button', 'btn btn-green', t('post'), { type: 'button' });
  send.dataset.tip = c.tips.post;
  send.addEventListener('click', () => submit());
  const msg = el('div', 'small ah-msg');
  const row = el('div', 'ah-post-row');
  row.append(title, why, send);
  post.append(el('div', 'gold shadow ah-post-h', t('post_title')), row, msg);
  for (const i of [title, why]) i.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });

  const tabs = el('div', 'ah-tabs');
  const tabBtn = {};
  for (const k of ['open', 'taken', 'imported']) {
    const b = el('button', 'tal-tab', '', { type: 'button' });
    b.dataset.tip = c.tips.tab;
    b.addEventListener('click', () => { tab = k; if (last) draw(last); });
    tabBtn[k] = b;
    tabs.append(b);
  }
  // V-044: source filter (All / GitHub / Jira / Linear) and a way to the Integrations page, shown on the Imported tab
  const SOURCES = ['github', 'jira', 'linear'];
  let source = 'all';
  const filt = el('div', 'ah-filter');
  const filtBtn = {};
  for (const k of ['all', ...SOURCES]) {
    const b = el('button', 'ah-chip', '', { type: 'button' });
    if (k !== 'all') b.append(icon(c.icons['src_' + k], 'ah-chip-icon'));
    b.append(el('span', 'ah-chip-text'));
    b.dataset.tip = c.tips.source;
    b.addEventListener('click', () => { source = k; if (last) draw(last); });
    filtBtn[k] = b;
    filt.append(b);
  }
  const setBtn = el('button', 'btn btn-metal ah-settings', t('settings'), { type: 'button' });
  setBtn.dataset.tip = c.tips.settings;
  setBtn.addEventListener('click', () => { panel.toggle(); const g = ctx.panels.game_settings; if (g && g.api && g.api.page) g.api.page(ctx.t('integ.title')); });
  filt.append(setBtn);
  const imsg = el('div', 'small ah-msg ah-imsg');
  panel.body.append(filt, cols, list, note, imsg, post, tabs);

  async function submit() {
    const name = title.value.trim();
    if (!name) { msg.className = 'small red ah-msg'; msg.textContent = t('need_title'); return; }
    send.disabled = true;
    msg.className = 'small gold ah-msg'; msg.textContent = t('posting');
    try {
      const r = await ctx.net.post('/api/quest_new', { title: name, why: why.value.trim() });
      title.value = ''; why.value = '';
      msg.className = 'small green ah-msg'; msg.textContent = t('posted', { title: name });
      if (r && r.tasks) ctx.render(r);
    } catch (e) { msg.className = 'small red ah-msg'; msg.textContent = t('post_error', { error: e.message }); }
    send.disabled = false;
  }

  const sizeWord = (q) => t(['small', 'medium', 'big'].includes(q.size) ? 'size_' + q.size : 'size_unknown');
  const riskWord = (q) => t(q.risk === 'high' ? 'risk_high' : q.risk === 'low' ? 'risk_low' : 'risk_unknown');

  function header(names) {
    clear(cols);
    names.forEach((k) => cols.append(el('div', 'ah-col ah-col-' + k + ' gold shadow', t('col_' + k))));
  }

  function item(q, extra) {
    const qual = quality(q);
    const r = el('div', 'ah-row');
    const slot = el('div', 'ah-slot q-' + qual);
    slot.append(icon(c.icons['item_' + (['small', 'medium', 'big'].includes(q.size) ? q.size : 'unknown')], 'ah-icon'));
    const nm = el('div', 'ah-name');
    nm.style.backgroundImage = 'url("' + assetUrl(c.icons.item_frame) + '")';
    nm.append(el('div', 'ah-title shadow qt-' + qual, q.title || q.id));
    const src = sourceOf(q);
    if (src) { const badge = icon(c.icons['src_' + src], 'ah-badge'); slot.append(badge); }
    if (q.why) nm.append(el('div', 'ah-sub small grey', q.why));
    const item = el('div', 'ah-col ah-col-item');
    item.append(slot, nm);
    r.append(item, el('div', 'ah-col ah-col-size small', sizeWord(q)), el('div', 'ah-col ah-col-risk small ' + (q.risk === 'high' ? 'red' : q.risk === 'low' ? 'green' : 'grey'), riskWord(q)));
    extra.forEach((x) => r.append(x));
    r.dataset.liveTip = '1';
    const tip = ctx.cfg.strings.tooltips[c.tips.row];
    r.tipData = { title: q.title || q.id, body: [sizeWord(q), riskWord(q)].join(' · ') + '. ' + tip.body };
    return r;
  }

  // a quest made from an import keeps its source badge: its why starts with "GitHub #12:", "Jira ABC-1:" or "Linear ENG-2:"
  const sourceOf = (q) => { const m = /^(GitHub|Jira|Linear) /.exec(q.why || ''); return m ? m[1].toLowerCase() : null; };

  async function act(path, b, okText) {
    imsg.className = 'small gold ah-msg ah-imsg'; imsg.textContent = path === '/api/bounty/quest' ? t('making') : '';
    try {
      const r = await ctx.net.post(path, { id: b.id });
      imsg.className = 'small green ah-msg ah-imsg'; imsg.textContent = okText || '';
      if (r && r.tasks) ctx.render(r);
    } catch (e) { imsg.className = 'small red ah-msg ah-imsg'; imsg.textContent = t('make_error', { error: e.message }); }
  }

  function importedRow(b) {
    const r = el('div', 'ah-row ah-imp');
    const slot = el('div', 'ah-slot ah-src-' + b.source);
    slot.append(icon(c.icons['src_' + b.source], 'ah-icon'));
    const nm = el('div', 'ah-name');
    nm.style.backgroundImage = 'url("' + assetUrl(c.icons.item_frame) + '")';
    nm.append(el('div', 'ah-title shadow white', b.title));
    const sub = [b.key, b.kind === 'pr' ? t('kind_pr') : '', b.excerpt].filter(Boolean).join(' \u00b7 ');
    nm.append(el('div', 'ah-sub small grey', sub));
    const itemCol = el('div', 'ah-col ah-col-item');
    itemCol.append(slot, nm);
    const btns = el('div', 'ah-col ah-acts');
    if (b.url) {
      const open = el('button', 'btn btn-metal ah-btn', t('open_link'), { type: 'button' });
      open.dataset.tip = c.tips.link;
      open.addEventListener('click', () => window.open(b.url, '_blank', 'noopener,noreferrer'));
      btns.append(open);
    }
    const make = el('button', 'btn btn-green ah-btn', t('make_quest'), { type: 'button' });
    make.dataset.tip = c.tips.make;
    make.addEventListener('click', () => { make.disabled = true; act('/api/bounty/quest', b, t('made', { title: b.title })); });
    const hide = el('button', 'btn btn-red ah-btn ah-hide', t('hide'), { type: 'button' });
    hide.dataset.tip = c.tips.hide;
    hide.addEventListener('click', () => act('/api/bounty/hide', b));
    btns.append(make, hide);
    r.append(itemCol, btns);
    r.dataset.liveTip = '1';
    const tip = ctx.cfg.strings.tooltips[c.tips.imported];
    r.tipData = { title: b.title, body: (b.excerpt ? b.excerpt + ' ' : '') + tip.body };
    return r;
  }

  function drawImported(s) {
    const all = s.bounties || [];
    const count = (k) => (k === 'all' ? all.length : all.filter((b) => b.source === k).length);
    for (const k of ['all', ...SOURCES]) {
      filtBtn[k].querySelector('.ah-chip-text').textContent = t('src_' + k) + ' ' + count(k);
      filtBtn[k].classList.toggle('ah-chip-on', k === source);
    }
    const rows = source === 'all' ? all : all.filter((b) => b.source === source);
    clear(cols);
    rows.slice(0, c.options.max_rows).forEach((b) => list.append(importedRow(b)));
    note.textContent = rows.length ? t('count_imported', { count: rows.length }) : t('empty_imported');
  }

  function draw(s) {
    clear(list);
    filt.hidden = tab !== 'imported';
    panel.root.classList.toggle('ah-imported', tab === 'imported');
    tabBtn.imported.textContent = t('tab_imported') + ((s.bounties || []).length ? ' (' + s.bounties.length + ')' : '');
    tabBtn.imported.classList.toggle('tal-tab-on', tab === 'imported');
    if (tab === 'imported') {
      tabBtn.open.textContent = t('tab_open'); tabBtn.taken.textContent = t('tab_taken');
      for (const k of ['open', 'taken']) tabBtn[k].classList.remove('tal-tab-on');
      post.hidden = true; panel.root.classList.remove('ah-taken');
      return drawImported(s);
    }
    imsg.textContent = '';
    const open = s.tasks.filter((q) => q.status === 'new' && !q.agent);
    const taken = s.tasks.filter((q) => q.agent && !['complete', 'failed'].includes(q.status));
    tabBtn.open.textContent = t('tab_open');
    tabBtn.taken.textContent = t('tab_taken');
    for (const k of ['open', 'taken']) tabBtn[k].classList.toggle('tal-tab-on', k === tab);
    post.hidden = tab !== 'open';
    const rows = tab === 'open' ? open : taken;
    header(tab === 'open' ? ['item', 'size', 'risk'] : ['item', 'size', 'risk', 'who', 'status']);
    panel.root.classList.toggle('ah-taken', tab === 'taken');
    rows.slice(0, c.options.max_rows).forEach((q) => {
      const extra = [];
      if (tab === 'taken') {
        const st = ctx.cfg.metaphors.task_states[q.status];
        extra.push(el('div', 'ah-col ah-col-who small white', nameFor(q.agent)), el('div', 'ah-col ah-col-status small ' + (st ? st.color : 'grey'), st ? ctx.t(st.word) : q.status));
      }
      list.append(item(q, extra));
    });
    note.textContent = !rows.length ? t(tab === 'open' ? 'empty_open' : 'empty_taken') : tab === 'open' ? t('count', { count: open.length }) : '';
  }

  const loot = initLoot(ctx, c);
  window.addEventListener('workers-ready', () => last && panel.isOpen() && draw(last));
  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen() && last) draw(last); };
  return {
    update(s) { last = s; if (panel.isOpen()) draw(s); loot(s); },
  };
}
