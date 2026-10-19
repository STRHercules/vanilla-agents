// Damage meter (U): a Recount/Details-style window. One bar per helper in its class colour for actions per minute (sampled in this
// browser over a sliding window), total actions or quests passed; plus a combat log of the checker's lines and the helpers' tool lines.
// V-030: the Cost tab shows each helper's session cost as Claude Code estimates it (statusline bridge), with dollars per minute of session time.
// Tokens tab: output tokens per minute over the window, from the tokens the transcript watcher counts (agent.tok = [[time, total so far]]).
import { clear, el } from '../dom.js';
import { nameFor, workerFor } from '../workers.js';
import { clockText, named } from '../runlog.js';

// RAID_CLASS_COLORS from the game's FrameXML
const CLASS_COLORS = { warrior: '#C79C6E', paladin: '#F58CBA', hunter: '#ABD473', rogue: '#FFF569', priest: '#FFFFFF', shaman: '#0070DE', mage: '#69CCF0', warlock: '#9482C9', druid: '#FF7D0A' };
const TABS = ['rate', 'total', 'passed', 'cost', 'tokens', 'log'];

export function build(ctx, panel) {
  const c = panel.cfg, o = c.options, T = (k, v) => ctx.t(c.text[k], v);
  const tabs = el('div', 'meter-tabs');
  const bars = el('div', 'meter-bars');
  const log = el('div', 'meter-log');
  panel.body.classList.add('meter-body');
  panel.body.append(tabs, bars, log);
  let tab = 'rate';
  try { if (TABS.includes(localStorage.getItem('meterTab'))) tab = localStorage.getItem('meterTab'); } catch (e) { /* storage off */ }
  const tabEls = {};
  for (const t of TABS) {
    const b = el('button', 'meter-tab', T('tab_' + t), { type: 'button' });
    b.dataset.tip = c.tips['tab_' + t];
    b.addEventListener('click', () => { tab = t; try { localStorage.setItem('meterTab', t); } catch (e) { /* storage off */ } drawn = ''; draw(); });
    tabEls[t] = b;
    tabs.append(b);
  }

  const samples = new Map();      // agent id -> [[seconds, actions]] inside the window
  let state = null, drawn = '';

  function sample(s) {
    const now = Date.now() / 1000;
    for (const a of s.agents || []) {
      const l = samples.get(a.id) || [];
      if (!l.length || l[l.length - 1][1] !== a.actions || now - l[l.length - 1][0] > 5) l.push([now, a.actions || 0]);
      while (l.length > 2 && now - l[1][0] > o.window_seconds) l.shift();
      samples.set(a.id, l);
    }
  }

  function rate(id) {
    const l = samples.get(id);
    if (!l || l.length < 2) return 0;
    const [t0, a0] = l[0], [t1, a1] = l[l.length - 1];
    return Math.max(0, a1 - a0) * 60 / Math.max(15, t1 - t0);
  }

  function tokRate(a) {
    const l = (a.tok || []).filter((x) => x[0] >= ((a.tok || []).slice(-1)[0] || [0])[0] - o.window_seconds);
    if (!l.length) return null;
    if (l.length < 2) return 0;
    return Math.max(0, l[l.length - 1][1] - l[0][1]) * 60 / Math.max(60, l[l.length - 1][0] - l[0][0]);
  }

  const colorOf = (id) => { const w = workerFor(id); return (w && CLASS_COLORS[String(w.class || '').toLowerCase()]) || '#9d9d9d'; };

  function drawBars() {
    const rows = (state.agents || []).map((a) => ({ a, rate: rate(a.id), total: a.actions || 0, passed: a.passed || 0, checked: (a.passed || 0) + (a.failed || 0),
      tokens: tokRate(a), tokTotal: a.tokens_out || 0,
      cost: typeof a.cost_usd === 'number' ? a.cost_usd : null, perMin: typeof a.cost_usd === 'number' && a.duration_ms > 60000 ? a.cost_usd / (a.duration_ms / 60000) : null }))
      .filter((r) => (tab !== 'cost' || r.cost !== null) && (tab !== 'tokens' || r.tokens !== null));
    const val = (r) => (tab === 'rate' ? r.rate : tab === 'total' ? r.total : tab === 'cost' ? r.cost : tab === 'tokens' ? r.tokens : r.passed);
    rows.sort((x, y) => val(y) - val(x) || y.total - x.total);
    const shown = rows.slice(0, o.max_bars);
    const k = JSON.stringify([tab, shown.map((r) => [r.a.id, Math.round(val(r) * 100), r.total, r.checked, nameFor(r.a.id), colorOf(r.a.id)])]);
    if (k === drawn) return;
    drawn = k;
    clear(bars);
    if (!shown.length) { bars.append(el('div', 'meter-empty grey', T((tab === 'cost' || tab === 'tokens') && (state.agents || []).length ? tab + '_empty' : 'empty'))); return; }
    const top = Math.max(...shown.map(val), 1e-9);
    shown.forEach((r, i) => {
      const v = val(r), shownV = tab === 'rate' ? (Math.round(v * 10) / 10).toFixed(1) : tab === 'tokens' ? String(Math.round(v)) : String(v);
      const row = el('div', 'meter-row');
      const fill = el('div', 'meter-fill');
      fill.style.width = (v > 0 ? Math.max(2, (100 * v) / top) : 0) + '%';
      fill.style.backgroundColor = colorOf(r.a.id);
      const label = el('div', 'meter-label shadow', (i + 1) + '. ' + nameFor(r.a.id));
      const usd = (x) => (x === null ? '?' : x.toFixed(x < 10 ? 2 : 0));
      const value = el('div', 'meter-value shadow', tab === 'rate' ? T('rate', { n: shownV }) : tab === 'total' ? T('total', { n: shownV }) : tab === 'tokens' ? T('tokens', { n: shownV })
        : tab === 'cost' ? T(r.perMin === null ? 'cost' : 'cost_rate', { usd: usd(r.cost), rate: usd(r.perMin) }) : T('passed', { passed: r.passed, checks: r.checked }));
      row.append(fill, label, value);
      row.dataset.liveTip = '1';
      row.tipData = { title: nameFor(r.a.id) + (r.a.role ? ' (' + r.a.role + ')' : ''), body: tab === 'tokens' ? T('tokens_body', { n: shownV, count: r.tokTotal }) : tab === 'cost'
        ? T('cost_body', { usd: usd(r.cost), rate: usd(r.perMin), added: r.a.lines_added || 0, removed: r.a.lines_removed || 0, name: r.a.model || '?' })
        : T('bar_body', { n: (Math.round(r.rate * 10) / 10).toFixed(1), count: r.total, passed: r.passed, total: r.checked }) };
      bars.append(row);
    });
  }

  function drawLog() {
    const lines = (state.chat || []).filter((l) => l.channel === 'verifier' || l.channel === 'agent').slice(-o.max_log);
    const k = JSON.stringify(['log', lines.length, lines.length && lines[lines.length - 1].id]);
    if (k === drawn) return;
    drawn = k;
    const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
    clear(log);
    if (!lines.length) { log.append(el('div', 'meter-empty grey', T('log_empty'))); return; }
    for (const l of lines) {
      const row = el('div', 'meter-line meter-' + l.channel);
      row.append(el('span', 'meter-ts', '[' + clockText(l.ts) + '] '), document.createTextNode(named(state, l.text)));
      log.append(row);
    }
    if (atEnd || !log.dataset.seen) { log.scrollTop = log.scrollHeight; log.dataset.seen = '1'; }
  }

  function draw() {
    if (!state) return;
    for (const t of TABS) tabEls[t].classList.toggle('active', t === tab);
    bars.hidden = tab === 'log';
    log.hidden = tab !== 'log';
    if (panel.isOpen()) (tab === 'log' ? drawLog : drawBars)();
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); drawn = ''; draw(); };
  window.addEventListener('workers-ready', () => { drawn = ''; draw(); });

  return function update(s) {
    state = s;
    sample(s);
    draw();
  };
}
