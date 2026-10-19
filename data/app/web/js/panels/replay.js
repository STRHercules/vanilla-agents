// Replay (R): a time machine docked above the action bar. The slider runs over the feed's events; picking one shows the whole window as it was right after it
// (GET /api/history?until=<id>). While looking at the past, ctx.replaying is set and live snapshots wait in ctx.live until Live is pressed.
import { el, icon, assetUrl } from '../dom.js';

const SPEEDS = [1, 4, 16];

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  panel.root.classList.add('rp');
  panel.root.style.setProperty('--rp-track', 'url("' + assetUrl(c.icons.track) + '")');
  panel.root.style.setProperty('--rp-track-border', 'url("' + assetUrl(c.icons.track_border) + '")');
  const top = el('div', 'rp-top'), bottom = el('div', 'rp-bottom');
  const watch = icon(c.icons.watch, 'rp-watch');
  watch.dataset.tip = c.tips.when;
  const slider = el('input', 'rp-slider', null, { type: 'range', min: '0', max: '0', step: '1', value: '0' });
  slider.dataset.tip = c.tips.slider;
  top.append(watch, slider);
  const play = el('button', 'btn btn-metal rp-btn rp-play', t('play'), { type: 'button' });
  play.dataset.tip = c.tips.play;
  const speed = el('button', 'btn btn-metal rp-btn rp-speed', t('speed', { n: SPEEDS[0] }), { type: 'button' });
  speed.dataset.tip = c.tips.speed;
  const info = el('div', 'rp-info');
  const at = el('div', 'rp-at small'), when = el('div', 'rp-when small gold');
  when.dataset.tip = c.tips.when;
  info.append(at, when);
  const live = el('button', 'btn btn-green rp-btn rp-live', t('live'), { type: 'button' });
  live.dataset.tip = c.tips.live;
  bottom.append(play, speed, info, live);
  panel.body.append(top, bottom);

  let range = { first: 0, last: 0 }, pos = 0, sp = 0, timer = null, busy = false, want = null, err = '';

  const clock = (ts) => {
    if (!ts) return '';
    const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, '0');
    const day = d.toDateString() === new Date().toDateString() ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ';
    return day + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  };

  function draw(snap) {
    slider.min = String(range.first || 0);
    slider.max = String(range.last || 0);
    slider.value = String(ctx.replaying ? pos : range.last);
    slider.disabled = !range.last;
    panel.root.classList.toggle('rp-past', !!ctx.replaying);
    play.textContent = timer ? t('pause') : t('play');
    live.textContent = ctx.replaying ? t('live') : t('live_now');
    live.disabled = !ctx.replaying;
    at.className = 'rp-at small ' + (err ? 'red' : 'grey');
    if (err) at.textContent = t('error', { error: err });
    else if (!range.last) at.textContent = t('empty');
    else at.textContent = t('at', { n: ctx.replaying ? pos : range.last, total: range.last });
    const s = snap || ctx.last, chat = (s && s.chat) || [];
    when.textContent = clock(chat.length ? chat[chat.length - 1].ts : 0);
    const pct = range.last > range.first ? (100 * ((ctx.replaying ? pos : range.last) - range.first)) / (range.last - range.first) : 100;
    slider.style.setProperty('--rp-pct', pct + '%');
  }

  async function fetchRange() {
    try { const r = await ctx.net.get('/api/history'); range = r.range; err = ''; } catch (e) { err = e.message; }
    draw();
  }

  // show the window as it was after event `id` (requests never pile up: the newest wish wins once the current one returns)
  async function go(id) {
    want = id;
    ctx.replaying = true;
    if (busy) return;
    busy = true;
    while (want !== null) {
      const target = want;
      want = null;
      let r;
      try { r = await ctx.net.get('/api/history?until=' + target); err = ''; } catch (e) { err = e.message; break; }
      if (!ctx.replaying) break;            // Live was pressed meanwhile
      range = r.range;
      pos = r.range.at;
      const now = ctx.live || ctx.last || {};
      const snap = Object.assign({}, r, {
        project: now.project || null, problem: '', cursor: now.cursor,
        verify: Object.assign({}, now.verify || {}, { running: false, paused: false }),
        auras: now.auras || { raid: [], player: [] },
      });
      delete snap.range;
      ctx.render(snap);
      draw(snap);
    }
    busy = false;
    draw();
  }

  function stop() { if (timer) clearInterval(timer); timer = null; }
  function tick() {
    if (busy) return;
    const next = Math.min(range.last, pos + SPEEDS[sp]);
    if (next >= range.last) stop();
    go(next);
  }

  function backToLive() {
    stop();
    want = null;
    ctx.replaying = false;
    if (ctx.live) ctx.render(ctx.live);
    draw();
  }

  slider.addEventListener('input', () => { stop(); pos = +slider.value; draw(); go(pos); });
  play.addEventListener('click', () => {
    if (timer) { stop(); draw(); return; }
    if (!range.last) return;
    if (!ctx.replaying || pos >= range.last) pos = range.first - 1;     // play from the very first event
    timer = setInterval(tick, c.options.tick_ms);
    draw();
    tick();
  });
  speed.addEventListener('click', () => { sp = (sp + 1) % SPEEDS.length; speed.textContent = t('speed', { n: SPEEDS[sp] }); });
  live.addEventListener('click', backToLive);

  const toggle = panel.toggle;
  panel.toggle = () => {
    toggle();
    document.body.classList.toggle('replay-open', panel.isOpen());
    if (panel.isOpen()) fetchRange(); else if (ctx.replaying) backToLive();      // closing the time machine returns to now
  };

  return function update(s) {
    if (ctx.replaying || !panel.isOpen()) return;
    if (s.cursor > range.last) range = { first: range.first || s.cursor, last: s.cursor };
    draw(s);
  };
}
