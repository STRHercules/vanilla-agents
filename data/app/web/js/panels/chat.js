// Chat window: the audit log in plain words, one coloured channel per speaker kind (system, agents, checker), with a channel / helper
// filter and a search box (V-034). New lines are also read to screen readers through the live region (a11y.js, V-033).
// Two tabs along the top (V-036): General (that log) and Community, where the people in your party (Party, G) talk to each other.
// Community text is for people only: it comes from s.party.chat (POST /api/party say), never from the feed, and never goes to Claude.
import { clear, el } from '../dom.js';
import { nameFor } from '../workers.js';
import { announce, initA11y, newLines } from '../a11y.js';

function clock(ts) {
  const d = new Date(ts * 1000);
  return [d.getHours(), d.getMinutes()].map((n) => String(n).padStart(2, '0')).join(':');
}

const FILTERS = { all: null, agents: ['agent'], system: ['system'], checker: ['verifier'] };

export function build(ctx, panel) {
  const c = panel.cfg;
  initA11y(ctx);
  const bar = el('div', 'chat-bar');
  // ---- tabs (WoW chat tabs): General = the log, Community = the people in your party ----
  let tab = 'general';
  try { tab = localStorage.getItem('chatTab') === 'community' ? 'community' : 'general'; } catch (e) { /* storage off */ }
  const tabs = el('div', 'chat-tabs', null, { role: 'tablist', 'aria-label': ctx.t(c.text.tabs) });
  const mkTab = (k) => {
    const b = el('button', 'chat-tab', null, { type: 'button', role: 'tab', id: 'chat-tab-' + k, 'aria-controls': 'chat-pane-' + k });
    b.append(el('span', 'chat-tab-label', ctx.t(c.text['tab_' + k])));
    b.dataset.tip = c.tips['tab_' + k];
    b.addEventListener('click', () => setTab(k));
    b.addEventListener('keydown', (e) => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); setTab(k === 'general' ? 'community' : 'general'); (k === 'general' ? tabC : tabG).focus(); } });
    tabs.append(b);
    return b;
  };
  const tabG = mkTab('general'), tabC = mkTab('community');
  const badge = el('span', 'chat-badge');
  badge.hidden = true;
  tabC.append(badge);
  const sel = el('select', 'chat-filter', null, { 'aria-label': ctx.t(c.text.filter) });
  sel.dataset.tip = c.tips.filter;
  for (const k of Object.keys(FILTERS)) sel.append(el('option', null, ctx.t(c.text['filter_' + k]), { value: k }));
  const helpers = el('optgroup', null, null, { label: '──────' });      // one entry per helper ("Only Gilthaniel"), rebuilt when helpers come and go
  sel.append(helpers);
  let want = 'all';
  try { want = localStorage.getItem('chatFilter') || 'all'; } catch (e) { /* storage off */ }
  sel.value = FILTERS[want] !== undefined ? want : 'all';
  const find = el('input', 'chat-search', null, { type: 'search', maxlength: '60', placeholder: ctx.t(c.text.search), 'aria-label': ctx.t(c.text.search), spellcheck: 'false' });
  find.dataset.tip = c.tips.search;
  bar.append(tabs, find, el('span', 'chat-filter-label small', ctx.t(c.text.filter)), sel);
  const log = el('div', 'chat-log', null, { id: 'chat-pane-general', role: 'tabpanel', 'aria-labelledby': 'chat-tab-general' });
  log.dataset.tip = c.tips.window;
  const clog = el('div', 'chat-log chat-clog', null, { id: 'chat-pane-community', role: 'tabpanel', 'aria-labelledby': 'chat-tab-community' });
  const community = {
    active: () => tab === 'community',
    joined: () => !!(state && state.party && state.party.joined),
    say: (text) => ctx.net.post('/api/party', { action: 'say', text }),
  };
  const cmd = commandLine(ctx, panel, () => state, community);
  panel.body.append(bar, log, clog, cmd.wrap);
  let state = null, online = true, helperSig = '', unread = 0;
  const seen = {};
  sel.addEventListener('change', () => { want = sel.value; try { localStorage.setItem('chatFilter', sel.value); } catch (e) { /* storage off */ } draw(); });
  find.addEventListener('input', () => { draw(); drawCommunity(); });
  find.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); if (find.value) { find.value = ''; draw(); drawCommunity(); } else find.blur(); } });

  function setTab(k) {
    tab = k;
    try { localStorage.setItem('chatTab', k); } catch (e) { /* storage off */ }
    for (const [b, name] of [[tabG, 'general'], [tabC, 'community']]) {
      b.classList.toggle('chat-tab-on', name === k);
      b.setAttribute('aria-selected', name === k ? 'true' : 'false');
      b.tabIndex = name === k ? 0 : -1;
    }
    log.hidden = k !== 'general';
    clog.hidden = k !== 'community';
    sel.hidden = k !== 'general';                 // the channel / helper filter belongs to the General log
    panel.body.classList.toggle('chat-on-community', k === 'community');
    if (k === 'community') setUnread(0);
    cmd.mode();
    sig = ''; csig = '';
    draw(); drawCommunity();
    const pane = k === 'general' ? log : clog;
    if (!find.value.trim()) pane.scrollTop = pane.scrollHeight;     // a tab opens on its newest lines (it may have filled while hidden)
  }
  function setUnread(n) {
    unread = n;
    badge.hidden = !n;
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.setAttribute('aria-label', ctx.t(c.text.community_unread, { count: n }));
  }

  function syncHelpers() {
    const ids = (state?.agents || []).map((a) => a.id);
    const k = ids.map((id) => id + '=' + nameFor(id)).join(',');
    if (k === helperSig) return;
    helperSig = k;
    clear(helpers);
    for (const id of ids) helpers.append(el('option', null, ctx.t(c.text.filter_helper, { name: nameFor(id) || id }), { value: 'h:' + id }));
    helpers.hidden = !ids.length;
    sel.value = [...sel.options].some((o) => o.value === want) ? want : 'all';     // a remembered helper filter comes back with its helper
  }

  // the line text with every match of the search words marked (case-insensitive)
  function marked(row, text, q) {
    if (!q) { row.append(document.createTextNode(text)); return; }
    const low = text.toLowerCase();
    let at = 0;
    for (let i = low.indexOf(q); i >= 0; i = low.indexOf(q, at)) {
      if (i > at) row.append(document.createTextNode(text.slice(at, i)));
      row.append(el('mark', 'chat-mark', text.slice(i, i + q.length)));
      at = i + q.length;
    }
    if (at < text.length) row.append(document.createTextNode(text.slice(at)));
  }

  let sig = '';
  function draw() {
    const lines0 = state ? state.chat : [];
    const q = tab === 'general' ? find.value.trim().toLowerCase() : '';
    const k = [lines0.length, lines0.length && lines0[lines0.length - 1].ts, lines0.length && lines0[lines0.length - 1].text, online, sel.value, q, helperSig].join('|');
    if (k === sig) return;       // same log as last time: keep the DOM (and its scroll) as it is
    sig = k;
    const stick = q || log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    clear(log);
    const v = sel.value, only = FILTERS[v] || null, who = v.startsWith('h:') ? v.slice(2) : '';
    const lines = lines0.filter((l) => (!only || only.includes(l.channel)) && (!who || l.agent === who || l.text.includes(who)))
      .map((l) => [l, named(l.text)]).filter(([, text]) => !q || text.toLowerCase().includes(q));
    if (state && !lines.length) log.append(el('div', 'chat-line chat-empty', ctx.t(q || who ? c.text.no_match : c.text.empty)));
    lines.forEach(([l, text]) => {
      const row = el('div', 'chat-line chat-' + l.channel);
      row.append(el('span', 'chat-time', '[' + clock(l.ts) + '] '));
      marked(row, text, q);
      log.append(row);
    });
    if (!online) log.append(el('div', 'chat-line chat-offline', ctx.t(c.text.offline)));
    if (stick) log.scrollTop = log.scrollHeight;
  }

  // agent ids in the log text show as the worker's name (same name as the raid frame)
  const named = (t) => (state?.agents || []).reduce((x, a) => { const n = nameFor(a.id); return n && n !== a.id ? x.split(a.id).join(n) : x; }, t);

  // ---- Community: the people in your party (s.party.chat: {id, ts, member, name, text, sys?}) ----
  const sysText = (m) => ctx.t(c.text['community_' + m.sys] || c.text.community_join, { name: m.name });
  let csig = '', lastId = -1;
  function drawCommunity() {
    const p = (state && state.party) || {};
    const msgs = p.joined ? (p.chat || []) : [];
    const q = tab === 'community' ? find.value.trim().toLowerCase() : '';
    const me = p.state && p.state.me, host = !!p.host;
    const k = [p.joined, msgs.length, msgs.length && msgs[msgs.length - 1].id, msgs.length && msgs[0].id, q, me, host, tab].join('|');
    if (k === csig) return;
    csig = k;
    const stick = q || clog.scrollTop + clog.clientHeight >= clog.scrollHeight - 8;
    clear(clog);
    if (!p.joined) { clog.append(el('div', 'chat-line chat-empty', ctx.t(c.text.community_none))); return; }
    const rows = msgs.map((m) => [m, m.sys ? sysText(m) : m.name + ': ' + m.text]).filter(([, text]) => !q || text.toLowerCase().includes(q));
    if (!rows.length) clog.append(el('div', 'chat-line chat-empty', ctx.t(q ? c.text.no_match : c.text.community_empty)));
    for (const [m, text] of rows) {
      const row = el('div', 'chat-line chat-community' + (m.sys ? ' chat-csys' : '') + (m.member === me ? ' chat-cown' : ''));
      row.append(el('span', 'chat-time', '[' + clock(m.ts) + '] '));
      if (m.sys) marked(row, text, q);
      else {
        const name = el('span', 'chat-cname' + (m.member === me ? ' chat-me' : ''));
        marked(name, m.name, q);
        row.append(name, document.createTextNode(': '));
        const body = el('span', 'chat-ctext');
        marked(body, m.text, q);                    // textContent only (never HTML): a friend's "<b>" stays text
        row.append(body);
        if (host) {                                 // the host may take a message away for everyone (the hub checks it is the host)
          const x = el('button', 'chat-cdel', '×', { type: 'button', 'aria-label': ctx.t(c.text.community_delete) });
          x.dataset.tip = c.tips.community_delete;
          x.addEventListener('click', async () => { try { const s = await ctx.net.post('/api/party', { action: 'unsay', id: m.id }); if (s && s.agents) ctx.render(s); } catch (e) { cmd.hint(ctx.t(c.text.cmd_error, { error: e.message })); } });
          row.append(x);
        }
      }
      clog.append(row);
    }
    if (stick) clog.scrollTop = clog.scrollHeight;
  }
  // new community lines: unread count on the tab, a soft sound, and the screen reader's live region (never our own lines)
  function noticeCommunity(p) {
    const msgs = p && p.joined ? (p.chat || []) : [];
    const top = msgs.length ? msgs[msgs.length - 1].id : 0;
    if (lastId < 0 || !p || !p.joined) { lastId = top; if (!p || !p.joined) setUnread(0); return; }     // first snapshot / not in a party: no noise
    if (top < lastId) { lastId = top; return; }        // another party (ids start again)
    const me = p.state && p.state.me;
    const fresh = msgs.filter((m) => m.id > lastId && m.member !== me);
    lastId = top;
    if (!fresh.length) return;
    if (tab !== 'community' || !panel.isOpen()) setUnread(unread + fresh.length);
    for (const m of fresh.slice(-3)) announce(m.sys ? sysText(m) : ctx.t(c.text.community_announce, { name: m.name, text: m.text }));
    if (c.options.community_sound && fresh.some((m) => !m.sys) && ctx.sound && ctx.sound.ui) ctx.sound.ui('tell', c.options.community_volume_pct / 100);
  }

  const update = (s) => {
    state = s;
    syncHelpers();
    for (const l of newLines(s.chat || [], seen)) announce(named(l.text));      // screen readers hear every new line, whatever the filter
    noticeCommunity(s.party);
    cmd.mode();
    draw();
    drawCommunity();
  };
  setTab(tab);
  update.setOnline = (v) => { if (!v && online) announce(ctx.t(c.text.offline)); online = v; draw(); };
  return update;
}

// The chat line: Enter starts typing; "/w <helper> <text>" whispers one helper (name or id, any case), "/p" or "/party" talks to every
// helper, "/c" or "/community" talks to the people in your party (V-036, never Claude). On the Community tab plain text goes there too.
function commandLine(ctx, panel, getState, community) {
  const c = panel.cfg;
  const wrap = el('div', 'chat-cmd');
  const box = el('input', 'chat-input', null, { type: 'text', maxlength: '506', placeholder: ctx.t(c.text.cmd_placeholder), 'aria-label': ctx.t(c.text.cmd_placeholder) });
  box.dataset.tip = c.tips.input;
  const hint = el('div', 'chat-hint small');
  wrap.append(box, hint);
  let timer = 0;
  const say = (text) => { hint.textContent = text; clearTimeout(timer); if (text) timer = setTimeout(() => { hint.textContent = ''; }, 5000); };
  const send = async (to, text) => {
    if (!text) { say(ctx.t(c.text.cmd_empty)); return; }
    try { const s = await ctx.net.post('/api/whisper', { to, text }); if (s && s.agents) ctx.render(s); box.value = ''; say(''); } catch (e) { say(ctx.t(c.text.cmd_error, { error: e.message })); }
  };
  const sayCommunity = async (text) => {
    if (!community.joined()) { say(ctx.t(c.text.community_need_party)); return; }
    if (!text) { say(ctx.t(c.text.cmd_empty)); return; }
    try { const s = await community.say(text.slice(0, 500)); if (s && s.agents) ctx.render(s); box.value = ''; say(''); } catch (e) { say(ctx.t(c.text.cmd_error, { error: e.message })); }
  };
  let modeSig = '';
  const mode = () => {                 // the placeholder says where plain text goes
    const k = community.active() ? (community.joined() ? 'community_placeholder' : 'community_need_party') : 'cmd_placeholder';
    if (k === modeSig) return;
    modeSig = k;
    box.placeholder = ctx.t(c.text[k]);
    box.setAttribute('aria-label', box.placeholder);
    wrap.classList.toggle('chat-cmd-community', k === 'community_placeholder');
  };
  box.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { box.blur(); return; }
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const v = box.value.trim();
    if (!v) { box.blur(); return; }
    const m = v.match(/^\/(\w+)\s*([\s\S]*)$/);
    const cmd = m ? m[1].toLowerCase() : '';
    if (cmd === 'c' || cmd === 'community') { sayCommunity(m[2].trim()); return; }
    if (!m && community.active()) { sayCommunity(v); return; }
    if (cmd === 'p' || cmd === 'party') { send('everyone', m[2].trim()); return; }
    const EMOTE_ALIAS = { lol: 'laugh', clap: 'applaud', hi: 'wave', hello: 'wave', bye: 'wave', nod: 'yes', ty: 'bow', thank: 'bow', yell: 'shout', angry: 'rude', mourn: 'kneel', pray: 'kneel', read: 'read', think: 'channel', stealth: 'sneak' };
    const emo = EMOTE_ALIAS[cmd] || cmd;       // the game's emotes play on your own character in the inn: /dance, /wave, /sit, /sleep, /kneel ...
    if (ctx.heroEmote && (ctx.emoteNames || []).includes(emo) && !m[2].trim()) { if (ctx.heroEmote(emo)) { box.value = ''; say(''); box.blur(); } return; }
    if (cmd !== 'w' && cmd !== 'whisper') { say(ctx.t(c.text.cmd_unknown)); return; }
    const rest = m[2], low = rest.toLowerCase();
    let best = null, len = 0;
    for (const a of (getState() || { agents: [] }).agents) {
      for (const n of [nameFor(a.id), a.id]) {
        const k = String(n).toLowerCase();
        if ((low === k || low.startsWith(k + ' ')) && k.length > len) { best = a.id; len = k.length; }
      }
    }
    if (!best) { say(ctx.t(c.text.cmd_no_helper, { name: rest.split(/\s+/)[0] || '' })); return; }
    send(best, rest.slice(len).trim());
  });
  // Enter (when not already typing somewhere) opens the chat and puts the cursor in this line; runs before the hotkeys
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.ctrlKey || e.altKey || e.metaKey || ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(e.target.tagName)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    panel.root.hidden = false;
    box.focus();
  });
  return { wrap, mode, hint: say };
}
