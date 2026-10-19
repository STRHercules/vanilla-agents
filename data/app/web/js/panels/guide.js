// Feature guide (V-037): a parchment book of every feature and how to use it. Chapters and topics come from app/config/guide.json,
// every sentence from strings/ui.json (guide.*). Left: search box + chapter/topic list; right: the topic, its keys as key caps,
// an "Open it" button for the window it is about and "Show in Options" for the setting it explains.
// Opened from Help (F1), the Game Menu (Guide) and the ? in the Options window: api.open(chapterOrTopicId).
import { clear, el, icon } from '../dom.js';
import { keyCap, keyLabel } from './help.js';

const NO_OPEN = new Set(['guide', 'cast_bar']);      // the cast bar opens by itself while a check runs

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  const chapters = (ctx.cfg.guide && ctx.cfg.guide.chapters) || [];
  const topics = chapters.flatMap((ch) => ch.topics.map((tp) => ({ ...tp, chapter: ch })));
  const textOf = (tp) => [ctx.t(tp.title), ...tp.body.map((k) => ctx.t(k)), ...(tp.keys || []).map((k) => keyLabel(ctx, k))].join(' ').toLowerCase();
  let current = topics[0] || null, query = '';

  const view = el('div', 'gd');
  const top = el('div', 'gd-top');
  const search = el('input', 'gd-search', null, { type: 'search', placeholder: t('search'), spellcheck: 'false', 'aria-label': t('search') });
  search.dataset.tip = c.tips.search;
  top.append(icon(c.icons.book, 'gd-book'), search);
  const nav = el('div', 'gd-nav'), page = el('div', 'gd-page');
  nav.setAttribute('role', 'navigation');
  const panes = el('div', 'gd-panes');
  panes.append(nav, page);
  view.append(top, panes);
  panel.body.append(view);

  const words = () => query.split(/\s+/).filter(Boolean);
  const matches = (tp) => words().every((w) => textOf(tp).includes(w));
  // the text with every search word marked (case-insensitive), as nodes
  function marked(text) {
    const ws = words();
    if (!ws.length) return [document.createTextNode(text)];
    const rx = new RegExp('(' + ws.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi');
    return text.split(rx).filter((s) => s !== '').map((s) => (ws.includes(s.toLowerCase()) ? el('mark', 'gd-mark', s) : document.createTextNode(s)));
  }

  function renderNav() {
    clear(nav);
    const list = query ? topics.filter(matches) : topics;
    if (query) nav.append(el('div', 'gd-count small', list.length ? t('results', { count: list.length }) : t('no_match')));
    for (const ch of chapters) {
      const mine = list.filter((tp) => tp.chapter === ch);
      if (!mine.length) continue;
      const head = el('button', 'gd-chapter', ctx.t(ch.title), { type: 'button' });
      head.addEventListener('click', () => show(mine[0]));
      nav.append(head);
      for (const tp of mine) {
        const b = el('button', 'gd-topic' + (tp === current ? ' on' : ''), null, { type: 'button' });
        b.append(...marked(ctx.t(tp.title)));
        if (tp === current) b.setAttribute('aria-current', 'true');
        b.addEventListener('click', () => show(tp));
        nav.append(b);
      }
    }
  }

  function renderPage() {
    clear(page);
    if (!current) return;
    const tp = current;
    page.append(el('div', 'gd-crumb small', ctx.t(tp.chapter.title)));
    const h = el('h2', 'gd-title');
    h.append(...marked(ctx.t(tp.title)));
    page.append(h);
    for (const k of tp.body) { const p = el('p', 'gd-p'); p.append(...marked(ctx.t(k))); page.append(p); }
    if (tp.keys && tp.keys.length) {
      page.append(el('div', 'gd-sub', t('keys')));
      const keys = el('div', 'gd-keys');
      for (const k of tp.keys) {
        const key = ctx.cfg.hotkeys[k];
        if (!key) continue;
        const row = el('div', 'gd-key');
        const label = el('span', 'gd-key-label');
        label.append(...marked(keyLabel(ctx, k)));
        row.append(el('kbd', 'gd-kbd', keyCap(key)), label);
        keys.append(row);
      }
      page.append(keys);
    }
    const acts = el('div', 'gd-acts');
    if (tp.panel && !NO_OPEN.has(tp.panel) && ctx.panels[tp.panel]) {
      const b = el('button', 'btn btn-green gd-btn', t('open'), { type: 'button' });
      b.dataset.tip = c.tips.open;
      b.addEventListener('click', () => openPanel(tp.panel));
      acts.append(b);
    }
    if (tp.setting && ctx.panels.game_settings) {
      const b = el('button', 'btn btn-blue gd-btn', t('setting'), { type: 'button' });
      b.dataset.tip = c.tips.setting;
      b.addEventListener('click', () => { if (panel.isOpen()) panel.toggle(); const g = ctx.panels.game_settings; g.api.show(tp.setting); if (!g.isOpen()) g.toggle(); });
      acts.append(b);
    }
    if (acts.childNodes.length) page.append(acts);
    note.textContent = '';
    page.append(note);
    page.scrollTop = 0;
  }
  const note = el('div', 'gd-note small');

  function openPanel(name) {
    const p = ctx.panels[name];
    if (name === 'gossip') {
      const agents = (ctx.last && ctx.last.agents) || [];
      const id = ctx.selected || (agents[0] && agents[0].id);
      if (!id || !ctx.talk) { note.textContent = t('need_helper'); return; }
      ctx.talk(id);
      p.front();       // the talk window shows itself without coming to the front
      return glow(p);
    }
    if (name === 'game_settings' || name === 'sound_menu') p.api.show();
    if (name === 'esc_menu' && p.api.reset) p.api.reset();
    if (!p.isOpen()) p.toggle(); else p.front();
    glow(p);
  }
  function glow(p) {      // a short gold glow says where it is
    p.root.classList.remove('gd-flash'); void p.root.offsetWidth; p.root.classList.add('gd-flash');
    setTimeout(() => p.root.classList.remove('gd-flash'), 1600);
  }

  function show(tp, where = 'nearest') {
    current = tp;
    renderNav(); renderPage();
    const on = nav.querySelector('.gd-topic.on');
    if (on) on.scrollIntoView({ block: where });
  }

  search.addEventListener('input', () => {
    query = search.value.trim().toLowerCase();
    const list = query ? topics.filter(matches) : topics;
    if (query && list.length && !list.includes(current)) current = list[0];
    renderNav(); renderPage();
  });
  search.addEventListener('keydown', (e) => { if (e.key === 'Escape' && search.value) { e.stopPropagation(); e.preventDefault(); search.value = ''; query = ''; renderNav(); renderPage(); } });

  renderNav(); renderPage();
  return {
    update() {},
    // open('settings') shows that chapter's first topic, open('pause') that topic; no id keeps the page you were on
    open(id) {
      const tp = id && (topics.find((x) => x.id === id) || topics.find((x) => x.chapter.id === id));
      if (tp) { query = ''; search.value = ''; current = tp; }
      if (!panel.isOpen()) panel.toggle(); else panel.front();
      show(current, 'center');
    },
  };
}
