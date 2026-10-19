// World map (M): the project's source areas as zones on parchment (DESIGN 2). Fog = nobody has touched it; explored = an agent has;
// cleared = the whole project passed its checks after the last change here (NOT line coverage). Everything comes from GET /api/zones.
// V-045: every helper is a small portrait in the zone of the file it touched last (zones payload "heroes"), pulsing while it works; click = select it.
import { clear, el, assetUrl } from '../dom.js';
import { ago, colorVar } from './bookutil.js';
import { nameFor, setPortrait } from '../workers.js';

export function build(ctx, panel) {
  const c = panel.cfg, states = ctx.cfg.map.states;
  const head = el('div', 'book-head small grey');
  const board = el('div', 'map-board');
  const detail = el('div', 'map-detail small');
  const legend = el('div', 'map-legend small');
  board.style.backgroundImage = 'url(' + assetUrl(c.icons.parchment) + ')';
  panel.body.append(head, board, detail, legend);
  let data = null, selected = '', busy = false, lastLoad = 0;

  for (const k of Object.keys(states)) {
    const s = el('span', 'map-key');
    const sw = el('span', 'map-swatch'); sw.style.borderColor = colorVar(states[k].color); sw.style.background = states[k].fill;
    s.append(sw, el('span', null, ctx.t(states[k].word)));
    legend.append(s);
  }
  legend.append(el('span', 'map-key', '✎ ' + ctx.t(c.text.legend_edited)), el('span', 'map-key', '◆ ' + ctx.t(c.text.legend_here)));
  const heroKey = el('span', 'map-key');
  heroKey.append(el('span', 'map-hero-key'), el('span', null, ctx.t(c.text.legend_heroes)));
  legend.append(heroKey);

  function heroDot(h) {
    const a = ((ctx.last && ctx.last.agents) || []).find((x) => x.id === h.agent);
    const working = !!(a && a.state === 'working');
    const b = el('button', 'mz-hero' + (working ? ' mz-hero-working' : '') + (ctx.selected === h.agent ? ' mz-hero-sel' : ''), null, { type: 'button', 'aria-label': nameFor(h.agent) });
    const pic = el('img', null, null, { alt: '', draggable: 'false' });
    setPortrait(pic, h.agent);
    b.append(pic);
    const where = h.file ? ctx.t(c.text.hero_file, { file: h.file.split(/[\\/]/).pop(), when: ago(ctx, h.ts) }) : ctx.t(c.text.hero_seen, { when: ago(ctx, h.ts) });
    b.dataset.liveTip = '1';
    b.tipData = { title: nameFor(h.agent), titleColor: 'gold', body: where + '\n' + ctx.t(working ? c.text.hero_working : c.text.hero_idle) };
    b.addEventListener('click', (e) => { e.stopPropagation(); ctx.select(h.agent); draw(); });
    return b;
  }

  async function load() {
    if (busy) return;
    busy = true;
    try { data = await ctx.net.get('/api/zones'); } catch (e) { data = { zones: [], problem: 'not_git', grid: { cols: 1, rows: 1 } }; }
    busy = false; lastLoad = Date.now();
    draw();
  }

  function tipOf(z) {
    const st = states[z.state];
    const lines = [];
    if (z.eli5) lines.push(z.eli5);
    lines.push(z.lines != null ? ctx.t(c.text.files_lines, { files: z.files, lines: z.lines }) : ctx.t(c.text.files, { files: z.files }));
    lines.push(z.last_touch ? ctx.t(c.text.touched, { when: ago(ctx, z.last_touch) }) : ctx.t(c.text.never));
    if (z.here.length) lines.push(ctx.t(c.text.here, { role: z.here.join(', ') }));
    const use = z.state === 'CLEARED' ? ctx.t(c.text.use_cleared, { commit: z.commit })
      : (z.state === 'EXPLORED' ? ctx.t(c.text.use_explored) : ctx.t(c.text.use_undiscovered)) + (z.edited_since_check ? '\n' + ctx.t(c.text.edited) : '');
    return { title: z.name, titleColor: 'gold', body: lines.join('\n'), last: { text: ctx.t(c.text.state_line, { result: ctx.t(st.word) }), color: st.color }, use };
  }

  function showDetail() {
    clear(detail);
    const z = data && data.zones.find((x) => x.id === selected);
    if (!z) { detail.append(el('div', 'grey', ctx.t(c.text.detail_empty))); return; }
    const t = tipOf(z);
    const last = el('div', null, t.last.text); last.style.color = colorVar(t.last.color);
    detail.append(el('div', 'tip-title', t.title), el('div', 'tip-body', t.body), last, el('div', 'tip-use', t.use));
  }

  function draw() {
    clear(board);
    const d = data;
    const counts = { EXPLORED: 0, CLEARED: 0 };
    if (!d || d.problem || !d.zones.length) {
      head.textContent = '';
      board.append(el('div', 'empty', ctx.t(d && d.problem === 'not_git' ? c.text.problem_not_git : c.text.empty)));
      showDetail();
      return;
    }
    board.style.gridTemplateColumns = 'repeat(' + d.grid.cols + ', 1fr)';
    board.style.gridTemplateRows = 'repeat(' + d.grid.rows + ', 1fr)';
    const w = (z) => (z.lines != null ? z.lines : z.files);
    const max = Math.max(1, ...d.zones.map(w));
    for (const z of d.zones) {
      const st = states[z.state];
      if (z.state !== 'UNDISCOVERED') counts.EXPLORED++;
      if (z.state === 'CLEARED') counts.CLEARED++;
      const cell = el('div', 'mz mz-' + z.state.toLowerCase() + (z.id === selected ? ' mz-sel' : ''));
      cell.style.gridColumn = z.col + 1; cell.style.gridRow = z.row + 1;
      cell.style.borderColor = colorVar(st.color);
      const tint = el('div', 'mz-tint'); tint.style.background = st.fill;
      cell.append(tint);
      if (z.state === 'UNDISCOVERED') {
        const fog = el('div', 'mz-fog'); fog.style.backgroundImage = 'url(' + assetUrl(c.icons.fog) + ')';
        cell.append(fog);
      }
      const name = el('div', 'mz-name shadow', z.name); name.style.color = colorVar(st.color);
      const sub = el('div', 'mz-sub small shadow', z.lines != null ? z.files + ' / ' + z.lines : String(z.files));
      const bar = el('div', 'mz-size'); bar.style.width = Math.max(6, Math.round(100 * w(z) / max)) + '%'; bar.style.background = colorVar(st.color);
      cell.append(name, sub, bar);
      if (z.state === 'CLEARED') cell.append(el('div', 'mz-mark green shadow', '✔'));
      if (z.edited_since_check) cell.append(el('div', 'mz-edit gold shadow', '✎'));
      const heroes = (d.heroes || []).filter((h) => h.zone === z.id);
      if (z.here.length && !heroes.length) cell.append(el('div', 'mz-here gold shadow', '◆'));
      if (heroes.length) {
        const row = el('div', 'mz-heroes' + (heroes.length > 4 ? ' mz-heroes-many' : ''));
        heroes.forEach((h) => row.append(heroDot(h)));
        cell.append(row);
      }
      cell.dataset.liveTip = '1';
      cell.tipData = tipOf(z);
      cell.addEventListener('click', () => { selected = z.id; draw(); });
      board.append(cell);
    }
    head.textContent = ctx.t(c.text.count, { zones: d.zones.length, explored: counts.EXPLORED, cleared: counts.CLEARED });
    showDetail();
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen()) load(); };
  draw();
  return function update() {     // the server pushes state over SSE; the open map refreshes at most every refresh_seconds
    if (panel.isOpen() && Date.now() - lastLoad > ctx.cfg.map.refresh_seconds * 1000) load();
  };
}
