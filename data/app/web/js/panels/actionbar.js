// Action bar (V-052): six buttons in the rack, like the game's action bar - each with its key (1-6) in the corner and its
// name under it. Verify now, Pause/Resume, Needs you (the next helper with a gold "!"), Quest log, Changes (Inspect) and the
// Hearthstone (pause + ask every helper to stop; two presses). The server does the work; this only sends the click and shows
// the server's plain answer. The letter hotkeys (V, Z, L, N) keep working through hotkeys.js; the number keys live here.
import { el, icon } from '../dom.js';
import { showToast } from '../toast.js';

export function build(ctx, panel) {
  const c = panel.cfg;
  const bar = el('div', 'actionbar');
  bar.style.setProperty('--ab-slots', String(c.slots.length));
  const parts = {};
  const byKey = {};
  c.slots.forEach((s) => {
    const b = el('button', 'slot slot-btn ab-slot', null, { type: 'button', 'aria-label': ctx.t(s.label) });
    const img = icon(s.icon, 'slot-icon');
    b.append(img);
    if (s.key) {
      b.append(el('span', 'ab-key', s.key));
      byKey[s.key] = s.id;
    }
    const count = el('span', 'ab-count');
    count.hidden = true;
    b.append(count);
    const label = el('span', 'ab-name small shadow', ctx.t(s.label));
    const wrap = el('div', 'slot-wrap ab-wrap');
    wrap.dataset.tip = s.tip;
    wrap.append(b, label);
    bar.append(wrap);
    parts[s.id] = { b, img, label, wrap, count, slot: s };
  });
  const call = async (path, body) => {
    try { ctx.render(await ctx.net.post(path, body)); } catch (e) { showToast(e.message, 'grey'); }
  };
  const toggle = (name) => { if (ctx.panels[name]) ctx.panels[name].toggle(); };
  let armed = 0, nextNeed = 0;
  const act = {
    verify: (check) => call('/api/verify', typeof check === 'string' && check ? { check } : {}),
    pause: () => call('/api/pause', { paused: !(ctx.last && ctx.last.verify && ctx.last.verify.paused) }),
    // the helpers with a gold "!", one after the other: pick it and open its talk window
    needs: () => {
      const waiting = ((ctx.last && ctx.last.agents) || []).filter((a) => a.needs);
      if (!waiting.length) { showToast(ctx.t(c.text.nobody), 'grey'); return; }
      const a = waiting[nextNeed % waiting.length];
      nextNeed += 1;
      ctx.select(a.id);
      if (ctx.talk) ctx.talk(a.id);
    },
    quests: () => toggle('quest_log'),
    changes: () => toggle('inspect'),
    hearth: () => {      // two presses: the first only says what will happen
      if (Date.now() - armed > 4000) { armed = Date.now(); showToast(ctx.t(c.text.hearth_again), 'grey'); return; }
      armed = 0; call('/api/hearth', {}).then(() => showToast(ctx.t('action.hearthed'), 'gold'));
    },
  };
  ctx.actions = Object.assign(ctx.actions || {}, act);
  for (const [id, p] of Object.entries(parts)) if (act[id]) p.b.addEventListener('click', () => act[id]());
  // number keys, like the game's action bar (never while typing, or with a button / list focused)
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.altKey || e.metaKey || e.repeat) return;
    const t = e.target;
    if (t && (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(t.tagName) || t.isContentEditable)) return;
    const id = byKey[e.key];
    if (!id || !act[id]) return;
    e.preventDefault();
    const p = parts[id];
    p.b.classList.add('ab-down');
    setTimeout(() => p.b.classList.remove('ab-down'), 120);
    act[id]();
  });
  panel.body.append(bar);
  // centred on the screen, unless that would cover the chat or the backpack at the bottom: then centred in the free gap
  // between them (the default 1280-wide window has room for all six buttons there)
  const place = () => {
    const vw = window.innerWidth, w = panel.root.offsetWidth || c.size.w, top = window.innerHeight - (c.anchor.bottom || 0) - c.size.h;
    let lo = w / 2 + 8, hi = vw - w / 2 - 8;
    for (const [name, side] of [['chat', 'left'], ['backpack', 'right']]) {
      const p = ctx.panels[name];
      if (!p || !p.isOpen() || !p.root) continue;
      const r = p.root.getBoundingClientRect();
      if (r.bottom <= top || r.top >= window.innerHeight) continue;
      if (side === 'left') lo = Math.max(lo, r.right + 8 + w / 2); else hi = Math.min(hi, r.left - 8 - w / 2);
    }
    const x = lo <= hi ? Math.min(Math.max(vw / 2, lo), hi) : vw / 2;
    panel.root.style.left = Math.round(x) + 'px';
  };
  window.addEventListener('resize', place);
  requestAnimationFrame(place);
  return function update(s) {
    place();
    const p = parts.pause;
    const paused = !!(s.verify && s.verify.paused);
    if (p) {
      p.label.textContent = ctx.t(paused ? c.text.resume : p.slot.label);
      p.img.src = '/assets/' + (paused ? c.icons.resume : p.slot.icon);
      p.wrap.dataset.tip = paused ? c.tips.resume : p.slot.tip;
      p.b.classList.toggle('ab-on', paused);
    }
    if (parts.verify) parts.verify.b.classList.toggle('slot-off', paused || !!(s.verify && s.verify.running));
    const n = parts.needs;
    if (n) {
      const k = (s.agents || []).filter((a) => a.needs).length;
      n.count.hidden = !k;
      n.count.textContent = k ? String(k) : '';
      n.b.classList.toggle('ab-glow', k > 0);
      n.b.classList.toggle('slot-off', !k);
      if (!k) nextNeed = 0;
    }
    if (parts.quests) parts.quests.b.classList.toggle('ab-on', !!(ctx.panels.quest_log && ctx.panels.quest_log.isOpen()));
    if (parts.changes) parts.changes.b.classList.toggle('ab-on', !!(ctx.panels.inspect && ctx.panels.inspect.isOpen()));
  };
}
