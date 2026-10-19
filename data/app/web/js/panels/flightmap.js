// Flight Master (F): a TaxiFrame map; every known project is a flight point, the open one says "You are here".
// Hover draws the flight line from here; click asks "Fly to ...?"; Fly opens that project (POST /api/project) and closes the map.
import { assetUrl, clear, el, icon } from '../dom.js';

// spots on the land of the map picture (fractions of its width/height), roughly where the game's flight masters stand
const SPOTS = [[0.53, 0.47], [0.44, 0.58], [0.62, 0.30], [0.75, 0.11], [0.55, 0.13], [0.40, 0.19], [0.49, 0.31], [0.62, 0.59], [0.49, 0.69], [0.60, 0.73], [0.45, 0.81], [0.53, 0.88]];

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  const top = el('div', 'fly-top small');
  const map = el('div', 'fly-map');
  map.style.backgroundImage = 'url("' + assetUrl(c.icons.map) + '")';
  const line = el('div', 'fly-line');
  line.style.backgroundImage = 'url("' + assetUrl(c.icons.line) + '")';
  map.append(line);
  const ask = el('div', 'fly-ask');
  const zonesBtn = el('button', 'btn btn-blue fly-zones', t('zones'), { type: 'button' });     // V-042: travel to another town's inn
  zonesBtn.addEventListener('click', () => { panel.toggle(); const z = ctx.panels.zones; if (z && !z.isOpen()) z.toggle(); });
  panel.body.append(top, map, ask, zonesBtn);
  let data = { projects: [], current: '' }, err = '', busy = '', project = null, pick = null;

  const norm = (p) => String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
  const isHere = (p) => norm(p.path) === norm(data.current) || (ctx.last && ctx.last.project && norm(ctx.last.project.path) === norm(p.path));

  async function load() {
    try { data = await ctx.net.get('/api/projects'); err = ''; } catch (e) { err = t('error_list', { error: e.message }); }
    draw();
  }

  function spot(i) {
    const s = SPOTS[i % SPOTS.length], lap = Math.floor(i / SPOTS.length);
    return [s[0] + lap * 0.035, s[1] + lap * 0.03];
  }

  function showLine(from, to) {
    if (!from || !to) { line.hidden = true; return; }
    const w = map.clientWidth, h = map.clientHeight;
    const x1 = from[0] * w, y1 = from[1] * h, x2 = to[0] * w, y2 = to[1] * h;
    line.style.left = x1 + 'px';
    line.style.top = y1 + 'px';
    line.style.width = Math.hypot(x2 - x1, y2 - y1) + 'px';
    line.style.transform = 'translateY(-50%) rotate(' + Math.atan2(y2 - y1, x2 - x1) + 'rad)';
    line.hidden = false;
  }

  async function fly(p) {
    busy = t('flying', { title: p.name });
    draw();
    try {
      await ctx.net.post('/api/project', { path: p.path });
      busy = ''; pick = null;
      await load();
      if (panel.isOpen()) panel.toggle();
    } catch (e) { busy = ''; err = t('error', { error: e.message }); draw(); }
  }

  function draw() {
    for (const n of [...map.querySelectorAll('.fly-point')]) n.remove();
    clear(ask);
    const list = data.projects || [];
    const hereIdx = list.findIndex(isHere);
    const here = hereIdx >= 0 ? spot(hereIdx) : null;
    top.textContent = list.length ? t('count', { count: list.length }) + ' - ' + t('pick') : '';
    top.className = 'fly-top small ' + (list.length ? 'gold' : 'grey');
    line.hidden = true;
    list.forEach((p, i) => {
      const [x, y] = spot(i), me = i === hereIdx;
      const pt = el('button', 'fly-point' + (me ? ' fly-here' : '') + (pick === p.path ? ' fly-picked' : ''), null, { type: 'button' });
      pt.style.left = (x * 100) + '%';
      pt.style.top = (y * 100) + '%';
      pt.append(icon(c.icons.highlight, 'fly-glow'), icon(me ? c.icons.here : c.icons.point, 'fly-pin'));
      pt.append(el('span', 'fly-name shadow', p.name));
      if (me) pt.append(el('span', 'fly-you shadow', t('here')));
      pt.dataset.liveTip = '1';
      const tip = ctx.cfg.strings.tooltips[me ? c.tips.here : c.tips.point];
      pt.tipData = { title: p.name, body: tip.body + ' ' + p.path, use: me ? undefined : tip.use };
      pt.addEventListener('mouseenter', () => { if (!me) showLine(here, [x, y]); });
      pt.addEventListener('mouseleave', () => { const k = list.findIndex((q) => q.path === pick); if (k >= 0) showLine(here, spot(k)); else line.hidden = true; });
      if (!me) pt.addEventListener('click', () => { pick = p.path; err = ''; draw(); });
      map.append(pt);
    });
    const k = list.findIndex((q) => q.path === pick), chosen = k >= 0 ? list[k] : null;
    if (chosen) showLine(here, spot(k));
    if (!list.length && !err) ask.append(el('div', 'grey fly-msg', t('none')));
    if (busy) ask.append(el('div', 'gold fly-msg', busy));
    else if (chosen) {
      const row = el('div', 'fly-confirm');
      const yes = el('button', 'btn btn-green', t('fly'), { type: 'button' });
      yes.addEventListener('click', () => fly(chosen));
      const no = el('button', 'btn btn-metal', t('cancel'), { type: 'button' });
      no.addEventListener('click', () => { pick = null; draw(); });
      row.append(el('div', 'gold shadow fly-q', t('ask', { title: chosen.name })), yes, no);
      ask.append(row);
    }
    if (err) ask.append(el('div', 'red small fly-msg', err));
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen()) { pick = null; err = ''; load(); } };

  return function update(s) {
    const p = s.project ? s.project.path : '';
    if (p !== project) { project = p; if (panel.isOpen()) load(); }
  };
}
