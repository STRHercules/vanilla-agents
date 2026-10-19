// Zones (F3, V-042): every baked inn, grouped by zone. Left: the zones of a continent (Eastern Kingdoms, Kalimdor, your own buildings);
// right: the zone's map with every area explored (baked from your game data) and its inns as cards with a picture of the inside
// (taken the first time you open that inn). Travel sets VA_Building, which reloads the workshop into that inn. "Remember for this
// project" keeps a zone per project (each project its own themed room): opening that project later travels there by itself.
import { assetUrl, clear, el, icon } from '../dom.js';
import * as ws from '../wsettings.js';

const MAP_KEY = 'vanilla.projectZones';
const readMap = () => { try { return JSON.parse(localStorage.getItem(MAP_KEY) || '{}') || {}; } catch (e) { return {}; } };
const writeMap = (m) => { try { localStorage.setItem(MAP_KEY, JSON.stringify(m)); } catch (e) { /* storage off: lasts until reload */ } };
const norm = (p) => String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  const tabs = el('div', 'zn-tabs'), side = el('div', 'zn-side'), main = el('div', 'zn-main');
  const body = el('div', 'zn-body'); body.append(side, main);
  panel.body.append(tabs, body);
  let data = null, err = '', cont = 'ek', zone = null, project = null, lastProject, busy = '';

  async function load(force) {
    if (data && !force) return;
    try { data = await ctx.net.get('/api/places'); err = ''; } catch (e) { err = e.message; data = data || { zones: [], buildings: [] }; }
    const here = current();
    if (here && !zone) { const z = zoneOf(here); if (z) { zone = z.id; cont = z.continent || 'own'; } }
    draw();
  }
  const current = () => (data ? data.buildings.find((b) => b.key === ws.get('VA_Building')) : null);
  const zones = () => (data ? data.zones : []);
  const zoneOf = (b) => (b.zone === 'own' || b.custom ? ownZone() : zones().find((z) => z.id === b.zone)) || null;
  const ownZone = () => ({ id: 'own', name: t('own_zone'), continent: 'own', faction: 'own' });
  const innsOf = (zid) => (data ? data.buildings.filter((b) => (zid === 'own' ? b.custom || b.zone === 'own' : b.zone === zid && !b.custom)) : []);

  function travel(b) {
    if (b.key === ws.get('VA_Building')) return;
    busy = b.key; draw();
    ws.set('VA_Building', b.key);                       // the inn reloads into the new building (workshop3d/inn.js)
    if (ctx.renderMode !== 'inn') setTimeout(() => location.reload(), 400);
  }

  function drawTabs() {
    clear(tabs);
    for (const [id, label] of [['ek', t('ek')], ['kalimdor', t('kalimdor')], ['own', t('own')]]) {
      const b = el('button', 'zn-tab' + (cont === id ? ' on' : ''), label, { type: 'button' });
      b.addEventListener('click', () => { cont = id; zone = null; draw(); });
      tabs.append(b);
    }
    tabs.append(el('div', 'zn-count small grey', t('count', { zones: zones().length, inns: data ? data.buildings.length : 0 })));
  }

  function drawSide(list) {
    clear(side);
    for (const z of list) {
      const n = innsOf(z.id).length;
      const row = el('button', 'zn-zone' + (z.id === zone ? ' on' : '') + (n ? '' : ' empty'), null, { type: 'button' });
      row.append(icon(c.icons[z.faction] || c.icons.contested, 'zn-crest'), el('span', 'zn-zname', z.name), el('span', 'zn-n small', String(n)));
      row.addEventListener('click', () => { zone = z.id; draw(); });
      side.append(row);
    }
  }

  function card(b) {
    const here = b.key === ws.get('VA_Building');
    const k = el('div', 'zn-card' + (here ? ' here' : ''));
    const pic = el('div', 'zn-pic');
    if (b.photo) { pic.style.backgroundImage = 'url("' + assetUrl(b.photo) + '")'; pic.dataset.tip = c.tips.photo; }
    else { const z = zoneOf(b); if (z && z.picture) { pic.style.backgroundImage = 'url("' + assetUrl(z.picture) + '")'; pic.classList.add('map'); } pic.append(el('div', 'zn-nophoto small', t('no_photo'))); }
    if (here) pic.append(el('div', 'zn-here', t('here')));
    const go = el('button', 'btn ' + (here ? 'btn-metal' : 'btn-green') + ' zn-go', busy === b.key ? t('travelling') : t('travel'), { type: 'button' });
    go.disabled = here || !!busy; go.dataset.tip = c.tips.travel;
    go.addEventListener('click', () => travel(b));
    const sub = b.inn && b.label.startsWith(b.inn + ', ') ? b.label.slice(b.inn.length + 2) : (b.town || b.label);      // 'Goldshire (quiet night)'
    k.append(pic, el('div', 'zn-inn gold shadow', b.inn || b.label), el('div', 'zn-town small', sub), go);
    k.addEventListener('dblclick', () => travel(b));
    return k;
  }

  function drawMain(z) {
    clear(main);
    if (err) main.append(el('div', 'small red', t('error', { error: err })));
    if (!z) { main.append(el('div', 'small grey zn-empty', cont === 'own' ? t('own_empty') : t('no_inns'))); return; }
    const banner = el('div', 'zn-banner' + (z.picture ? '' : ' plain'));
    if (z.picture) banner.style.backgroundImage = 'url("' + assetUrl(z.picture) + '")';
    banner.append(el('div', 'zn-btitle', z.name), el('div', 'zn-bsub small', z.faction && c.text['faction_' + z.faction] ? t('faction_' + z.faction) : ''));
    main.append(banner);
    if (!z.picture && z.id !== 'own') main.append(el('div', 'small grey zn-hint', t('not_baked')));
    const inns = innsOf(z.id), grid = el('div', 'zn-grid');
    main.append(el('div', 'zn-h gold shadow', t('inns')));
    if (!inns.length) grid.append(el('div', 'small grey', z.id === 'own' ? t('own_empty') : t('no_inns')));
    for (const b of inns) grid.append(card(b));
    main.append(grid);
    // each project its own zone
    const row = el('label', 'zn-remember small');
    const box = el('input', null, null, { type: 'checkbox' });
    const m = readMap(), key = project && norm(project.path);
    const cur = current();
    box.disabled = !key || !cur;
    box.checked = !!(key && cur && m[key] === cur.key);
    box.addEventListener('change', () => { const mm = readMap(); if (box.checked) mm[key] = cur.key; else delete mm[key]; writeMap(mm); });
    row.dataset.tip = c.tips.remember;
    row.append(box, el('span', null, key ? t('remember', { project: project.name || project.path, inn: cur ? (cur.inn || cur.label) : '' }) : t('remember_none')));
    main.append(row);
    // pictures of every inn you have not visited yet: the inn visits each one, takes its picture and comes back (workshop3d/inn.js)
    const missing = data.buildings.filter((b) => !b.photo).map((b) => b.key);
    if (missing.length && ctx.renderMode === 'inn') {
      const tour = el('button', 'btn btn-blue zn-tour', t('tour', { n: missing.length }), { type: 'button' });
      tour.dataset.tip = c.tips.tour;
      tour.addEventListener('click', () => {
        try { sessionStorage.setItem('vanilla.photoTour', JSON.stringify({ queue: missing, back: ws.get('VA_Building'), total: missing.length })); } catch (e) { return; }
        panel.toggle(); ws.set('VA_Building', missing[0]);
        if (missing[0] === ws.get('VA_Building')) location.reload();
      });
      main.append(tour);
    }
  }

  function draw() {
    if (!data) return;
    drawTabs();
    const list = cont === 'own' ? [ownZone()] : zones().filter((z) => z.continent === cont);
    if (!zone || !list.some((z) => z.id === zone)) zone = (list.find((z) => innsOf(z.id).length) || list[0] || {}).id || null;
    drawSide(list);
    drawMain(list.find((z) => z.id === zone) || null);
  }

  return {
    update(s) {
      project = s.project || null;
      const key = project ? norm(project.path) : '';
      if (key !== lastProject) {               // a project you gave a zone: travel there when it opens
        const first = lastProject === undefined; lastProject = key;
        const want = key && readMap()[key];
        if (want && want !== ws.get('VA_Building')) ws.set('VA_Building', want);
        else if (!first && panel.isOpen && panel.isOpen()) draw();
      }
    },
    show() { load(true); },
    onOpen() { load(true); },        // panel.js calls this whenever the window opens (F3, a button, the Esc menu): fresh pictures
    open(zid) { if (zid) zone = zid; load(true); },
  };
}
