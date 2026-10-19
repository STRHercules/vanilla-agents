// Raid frames: one row per helper: portrait, name, buff/debuff icons, green bar = quests passed, blue bar = how busy it is.
// Click selects it, like clicking its hero in the inn. Helpers on the same quest sit together under a small gold header with the quest title.
import { bar, el, icon } from '../dom.js';
import { drawAuras } from '../auras.js';
import { nameFor, setPortrait } from '../workers.js';
import { resurrect, canResurrect, checkRevived, justRevived, isGhost } from '../resurrect.js';

const LEFT_SHOWN = 2, LEFT_HOURS = 2;       // V-039: the last helpers that went home stay as grey frames for a while (right-click: Resurrect)

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  const grid = el('div', 'raid-grid');
  grid.style.gridTemplateColumns = 'repeat(' + c.options.columns + ', 1fr)';
  const note = el('div', 'small grey empty');
  panel.body.append(grid, note);
  const frames = new Map();
  const heads = new Map();       // quest id -> its group header
  const lefts = new Map();       // departed helper id -> its grey frame
  const played = new Set();
  const placeOf = (a) => (a && a.branch ? t(a.worktree ? 'place_wt' : 'place', { branch: a.branch, worktree: a.worktree || '' }) : '');

  // ---- V-039: right-click menu on a ghost or a helper that went home: Talk / Resurrect ----
  let menu = null;
  const closeMenu = () => { if (menu) { menu.remove(); menu = null; } };
  window.addEventListener('mousedown', (e) => { if (menu && !menu.contains(e.target)) closeMenu(); });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });
  function openMenu(id, x, y) {
    closeMenu();
    const s = last || {}, a = [...(s.agents || []), ...(s.departed || [])].find((v) => v.id === id);
    menu = el('div', 'raid-menu', null, { role: 'menu' });
    menu.append(el('div', 'raid-menu-title gold', nameFor(id)));
    const item = (label, tip, fn, off) => {
      const b = el('button', 'raid-menu-item', label, { type: 'button', role: 'menuitem' });
      b.dataset.tip = tip; b.disabled = !!off;
      b.addEventListener('click', () => { closeMenu(); fn(); });
      menu.append(b);
    };
    item(t('menu_talk'), c.tips.menu_talk, () => ctx.talk && ctx.talk(id));
    item(t('resurrect'), c.tips.resurrect, async () => {
      const g = ctx.panels && ctx.panels.gossip && ctx.panels.gossip.api;
      if (g && g.resurrect) return g.resurrect(id);       // the talk window shows the result (and the command to copy when it fails)
      try { await resurrect(ctx, id); } catch (e) { note.textContent = e.message; }
    }, !canResurrect(a));
    document.body.append(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.min(x, innerWidth - r.width - 4) + 'px';
    menu.style.top = Math.min(y, innerHeight - r.height - 4) + 'px';
    const f = menu.querySelector('button:not([disabled])');
    if (f) f.focus();
  }

  function groupHead(s, g) {
    let h = heads.get(g.head);
    if (!h) { h = el('div', 'raid-group small gold shadow'); h.dataset.tip = c.tips.group; heads.set(g.head, h); }
    const q = s.tasks.find((x) => x.id === g.head);
    h.textContent = t('group', { title: q ? q.title : g.head, count: g.count });
    return h;
  }

  function make(id) {
    const root = el('div', 'raid-frame');
    root.dataset.liveTip = '1';
    const face = el('div', 'raid-face');
    const pic = el('img', 'raid-pic', null, { alt: '', draggable: 'false' });
    face.append(pic);
    const col = el('div', 'raid-col');
    const head = el('div', 'raid-head');
    const name = el('div', 'raid-name shadow');
    const auras = el('div', 'aura-row');
    head.append(name, auras);
    const b = bar('--health');
    const busy = bar('--mana');
    col.append(head, b.root, busy.root);
    root.append(face, col);
    root.addEventListener('click', () => ctx.select(id));
    root.addEventListener('dblclick', () => ctx.talk && ctx.talk(id));        // talk window (the inn opens it on a hero click)
    root.addEventListener('contextmenu', (e) => {       // V-039: ghosts and helpers that went home: Talk / Resurrect
      const s = last || {}, a = [...(s.agents || []), ...(s.departed || [])].find((v) => v.id === id);
      if (!a || !(a.left !== undefined || isGhost(a))) return;
      e.preventDefault();
      openMenu(id, e.clientX, e.clientY);
    });
    return { root, name, b, busy, auras, pic, shown: null };
  }

  let last = null;
  window.addEventListener('workers-ready', () => last && update(last));
  function update(s) {
    last = s;
    checkRevived(ctx, s);
    const { raid, raid_more: more } = s.auras;
    for (const id of [...frames.keys()]) if (!raid.some((r) => r.id === id)) { frames.get(id).root.remove(); frames.delete(id); }
    const taskOf = (id) => { const a = s.agents.find((x) => x.id === id); return a && a.task; };
    const groups = new Map();
    for (const r of raid) { const k = taskOf(r.id); if (k) groups.set(k, [...(groups.get(k) || []), r]); }
    const order = [], seen = new Set();
    for (const r of raid) {
      const k = taskOf(r.id), g = k && groups.get(k);
      if (!g || g.length < 2) order.push(r);
      else if (!seen.has(k)) { seen.add(k); order.push({ head: k, count: g.length }, ...g); }
    }
    for (const [k, h] of [...heads]) if (!seen.has(k)) { h.remove(); heads.delete(k); }
    for (const r of order) {
      if (r.head) { grid.append(groupHead(s, r)); continue; }
      let f = frames.get(r.id);
      if (!f) { f = make(r.id); frames.set(r.id, f); }
      grid.append(f.root);       // keeps the order of the snapshot
      if (f.shown !== r.id) { setPortrait(f.pic, r.id); f.shown = r.id; }
      f.name.textContent = nameFor(r.id) + (r.role ? ' (' + r.role + ')' : '');
      f.name.className = 'raid-name shadow ' + (r.state === 'working' ? 'gold' : 'grey');
      f.root.classList.toggle('raid-selected', ctx.selected === r.id);
      f.b.set(r.pct, r.total ? t('quests', r) : t('no_quests'));
      const a = s.agents.find((x) => x.id === r.id);
      const acts = a ? a.actions : 0;
      if (a && typeof a.mana === 'number') f.busy.set(a.mana, t('mana', { pct: a.mana }));     // memory left in its conversation (from its transcript)
      else f.busy.set(r.state === 'working' ? 100 : 25, acts + ' / ' + (Math.floor(acts / 10) + 1) * 10);
      const clash = (s.clashes || []).find((x) => x.agents.includes(r.id));
      f.root.classList.toggle('raid-clash', !!clash);
      f.root.classList.toggle('raid-needs', !!(a && a.needs));
      f.root.classList.toggle('raid-stunned', !!(a && a.stunned));
      f.root.classList.toggle('raid-ghost', isGhost(a));
      const vs = a && a.contest ? s.tasks.find((x) => x.id === a.contest) : null;       // V-045: sent a fix to an open competition: crossed swords
      f.root.classList.toggle('raid-contest', !!vs);
      if (vs && !f.mark && ctx.cfg.panels.contest) { f.mark = icon(ctx.cfg.panels.contest.icons.swords, 'raid-contest-mark'); f.root.append(f.mark); }
      if (!vs && f.mark) { f.mark.remove(); f.mark = null; }
      if (justRevived(ctx, r.id, played)) { f.root.classList.remove('raid-revive'); void f.root.offsetWidth; f.root.classList.add('raid-revive'); setTimeout(() => f.root.classList.remove('raid-revive'), 3000); }
      drawAuras(ctx, f.auras, r.auras);
      const tip = ctx.cfg.strings.tooltips[c.tips.frame];
      const clashLine = clash ? t('clash', { names: clash.agents.filter((x) => x !== r.id).map(nameFor).join(', '), file: clash.file.split(/[\\/]/).pop() }) + ' ' : '';
      const why = clashLine + (a && a.needs ? t('needs_' + (['permission', 'question', 'input'].includes(a.needs) ? a.needs : 'input')) + (a.needs_note ? ' (' + a.needs_note + ')' : '') + ' ' : a && a.stunned ? t('stunned', { reason: a.stunned }) + ' ' : '');
      const where = (placeOf(a) ? placeOf(a) + '. ' : '') + (vs ? t('contest', { title: vs.title }) + ' ' : '');
      f.root.tipData = { title: nameFor(r.id), body: why + where + t(r.state === 'working' ? 'working' : 'idle') + ' - ' + (r.total ? t('quests', r) : t('no_quests')) + '. ' + tip.body,
        use: isGhost(a) ? t('menu_hint') : tip.use };
    }
    // V-039: helpers that went home lately, as grey frames under the others
    const gone = (s.departed || []).filter((d) => Date.now() / 1000 - d.left < LEFT_HOURS * 3600).slice(-LEFT_SHOWN).reverse();
    for (const id of [...lefts.keys()]) if (!gone.some((d) => d.id === id)) { lefts.get(id).root.remove(); lefts.delete(id); }
    for (const d of gone) {
      let f = lefts.get(d.id);
      if (!f) { f = make(d.id); f.root.classList.add('raid-left'); f.b.set(0, ''); f.busy.set(0, ''); setPortrait(f.pic, d.id); lefts.set(d.id, f); }
      grid.append(f.root);
      f.name.textContent = nameFor(d.id) + ' - ' + t('left');
      f.name.className = 'raid-name shadow grey';
      const tip = ctx.cfg.strings.tooltips[c.tips.left];
      f.root.tipData = { title: nameFor(d.id), body: (placeOf(d) ? placeOf(d) + '. ' : '') + tip.body, use: canResurrect(d) ? tip.use : '' };
    }
    note.textContent = !raid.length ? t('empty') : more ? t('more', { count: more }) : '';
  }
  return update;
}
