// Loot rolls: a Group Loot frame for every quest that is ready to hand in and not approved yet, stacked above the action bar.
// Need = Approve (POST /api/approve), Greed = Change (a short note, POST /api/quest action change), Pass = hide for this session.
// A timer bar runs for options.loot_seconds (it stops while you write a Change note); when it runs out the roll only hides, nothing is decided. Wording: panels/auction.json text loot_*.
import { assetUrl, el, icon } from '../dom.js';

export function quality(q) {      // item quality colour by size: unknown grey, small green, medium blue, big purple
  return { small: 'green', medium: 'blue', big: 'purple' }[q.size] || 'grey';
}

export function initLoot(ctx, c) {
  const t = (k, v) => ctx.t(c.text[k], v);
  const box = el('div', 'loot-rolls');
  const more = el('div', 'loot-more small gold shadow');
  box.append(more);
  ctx.ui.prepend(box);       // first in the UI layer: any window you open draws over the rolls
  const gone = new Set();          // passed, timed out, or answered in this session
  const rolls = new Map();         // task id -> { root, fill, start, paused }
  const secs = c.options.loot_seconds;
  setInterval(() => {              // timer bars; a roll whose time ran out only hides
    const now = performance.now();
    for (const [id, r] of [...rolls]) {
      if (r.paused) continue;
      const left = 1 - (now - r.start) / (secs * 1000);
      if (left <= 0) hide(id);
      else r.fill.style.width = (left * 100).toFixed(2) + '%';
    }
  }, 200);

  function hide(id) {
    gone.add(id);
    const r = rolls.get(id);
    if (r) { r.root.remove(); rolls.delete(id); }
    if (ctx.last) update(ctx.last);
  }

  function rollBtn(kind, onClick) {
    const b = el('button', 'loot-btn loot-' + kind, null, { type: 'button', 'aria-label': t('loot_' + kind) });
    b.style.setProperty('--up', 'url("' + assetUrl(c.icons['loot_' + kind]) + '")');
    b.style.setProperty('--down', 'url("' + assetUrl(c.icons['loot_' + kind + '_down']) + '")');
    b.style.setProperty('--hl', 'url("' + assetUrl(c.icons['loot_' + kind + '_hl']) + '")');
    b.dataset.tip = c.tips['loot_' + kind];
    b.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
    return b;
  }

  function make(q) {
    const qual = quality(q);
    const root = el('div', 'loot-roll frame frame-metal');
    const slot = el('div', 'loot-slot q-' + qual);
    slot.append(icon(c.icons['item_' + (['small', 'medium', 'big'].includes(q.size) ? q.size : 'unknown')], 'loot-icon'));
    const mid = el('div', 'loot-mid');
    const name = el('div', 'loot-name shadow qt-' + qual, q.title || q.id);
    const sub = el('div', 'loot-sub small grey', t('loot_ready'));
    const timer = el('div', 'loot-timer');
    timer.dataset.tip = c.tips.loot_timer;
    const fill = el('div', 'loot-fill');
    timer.append(fill);
    mid.append(name, sub, timer);
    const btns = el('div', 'loot-btns');
    const err = el('div', 'loot-err small red');
    const noteRow = el('div', 'loot-note');
    noteRow.hidden = true;
    const input = el('input', 'esc-input loot-input', null, { type: 'text', maxlength: '600', placeholder: t('loot_note') });
    const sendB = el('button', 'btn btn-orange loot-send', t('loot_send'), { type: 'button' });
    noteRow.append(input, sendB);
    const r = { root, fill, start: performance.now(), paused: false };

    async function call(path, body) {
      err.textContent = '';
      for (const x of root.querySelectorAll('button')) x.disabled = true;
      try { const s = await ctx.net.post(path, body); hide(q.id); if (s && s.tasks) ctx.render(s); } catch (e) {
        err.textContent = t('loot_error', { error: e.message });
        for (const x of root.querySelectorAll('button')) x.disabled = false;
      }
    }
    const send = () => { const note = input.value.trim(); if (note) call('/api/quest', { task: q.id, action: 'change', note }); else input.focus(); };
    sendB.addEventListener('click', send);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });
    btns.append(
      rollBtn('need', () => call('/api/approve', { task: q.id })),
      rollBtn('greed', () => {             // writing a note stops the clock: the roll never hides while you type
        noteRow.hidden = !noteRow.hidden;
        r.paused = true;
        if (!noteRow.hidden) input.focus();
      }),
      rollBtn('pass', () => hide(q.id)));
    const top = el('div', 'loot-top');
    top.append(slot, mid, btns);
    root.append(top, noteRow, err);
    return r;
  }

  function update(s) {
    const ready = s.tasks.filter((q) => q.status === 'ready' && !q.approved && !gone.has(q.id));
    const ids = new Set(ready.map((q) => q.id));
    for (const [id, r] of [...rolls]) if (!ids.has(id)) { r.root.remove(); rolls.delete(id); }
    const shown = ready.slice(0, c.options.loot_max);
    for (const q of shown) {
      if (!rolls.has(q.id)) { const r = make(q); rolls.set(q.id, r); box.insertBefore(r.root, more); }
    }
    for (const [id, r] of [...rolls]) if (!shown.some((q) => q.id === id)) { r.root.remove(); rolls.delete(id); }
    const extra = ready.length - shown.length;
    more.textContent = extra > 0 ? t('loot_more', { count: extra }) : '';
    more.hidden = extra <= 0;
  }
  return update;
}
