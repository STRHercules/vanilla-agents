// Options > Phone (V-040) and Options > Notifications (V-041): two pages of Vanilla Agents' own settings in the game options window.
// Both are saved by the server in YOUR settings file (never the repository). Words: strings/ui.json phonelink.* / notify.*, hover texts
// strings/tooltips.json phonelink_* / notify_*.
import { clear, el } from './dom.js';
import { windowKey } from './key.js';
import { draw as drawQr } from './qr.js';

const CHANNELS = ['discord', 'slack', 'ntfy', 'webhook'];

// ---- Phone ----
export function phonePage(ctx) {
  const T = (k, v) => ctx.t('phonelink.' + k, v);
  const box = el('div', 'vas');
  let st = null, msg = '', bad = false;

  async function load() {
    try { st = await ctx.net.get('/api/phone'); } catch (e) { msg = e.message; bad = true; }
    render();
  }
  async function toggle(on) {
    msg = ''; bad = false;
    try { st = await ctx.net.post('/api/phone', { enabled: on }); } catch (e) { msg = T('error', { error: e.message }); bad = true; }
    render();
  }
  function render() {
    clear(box);
    box.append(el('div', 'vas-p', T('desc')));
    const row = el('label', 'ws-row ws-check vas-toggle');
    const cb = el('input', 'ws-box', null, { type: 'checkbox' });
    cb.checked = !!(st && st.enabled);
    cb.addEventListener('change', () => toggle(cb.checked));
    row.append(cb, el('span', 'ws-label', T('toggle')));
    row.dataset.tip = 'phonelink_toggle';
    box.append(row, el('div', 'vas-warn red', T('warning')));
    if (st && st.enabled && st.error) box.append(el('div', 'vas-p red', st.error));
    const key = windowKey();
    if (st && st.listening && st.url) {
      if (!key) box.append(el('div', 'vas-p red', T('no_key')));
      else {
        const link = st.url + '#k=' + key;
        const wrap = el('div', 'vas-phone');
        const cv = el('canvas', 'vas-qr', null, { role: 'img', 'aria-label': T('qr_label') });
        cv.dataset.tip = 'phonelink_qr';
        try { drawQr(cv, link, 4, 4); } catch (e) { cv.hidden = true; }
        const side = el('div', 'vas-side');
        const code = el('code', 'vas-link', link);
        code.dataset.tip = 'phonelink_link';
        const copy = el('button', 'btn btn-metal', T('copy'), { type: 'button' });
        copy.addEventListener('click', async () => {
          try { await navigator.clipboard.writeText(link); copy.textContent = T('copied'); } catch (e) { const r = document.createRange(); r.selectNodeContents(code); getSelection().removeAllRanges(); getSelection().addRange(r); }
          setTimeout(() => { copy.textContent = T('copy'); }, 2000);
        });
        side.append(el('div', 'gold', T('scan')), code, copy, el('div', 'small grey', T('fresh')));
        wrap.append(cv, side);
        box.append(wrap);
      }
    } else if (st && !st.enabled) box.append(el('div', 'vas-p grey', T('off')));
    if (msg) box.append(el('div', 'vas-p ' + (bad ? 'red' : 'green'), msg));
  }
  load();
  return box;
}

// ---- Notifications ----
export function notifyPage(ctx) {
  const T = (k, v) => ctx.t('notifyhub.' + k, v);
  const box = el('div', 'vas');
  let data = null, msg = '', bad = false;
  const inputs = {};       // channel -> { enabled, url|topic, server, events: {name: checkbox} }

  async function load() {
    try { data = await ctx.net.get('/api/notify'); } catch (e) { msg = e.message; bad = true; }
    render();
  }
  const read = (ch) => {
    const i = inputs[ch], out = { enabled: i.enabled.checked, events: Object.entries(i.events).filter(([, b]) => b.checked).map(([k]) => k) };
    if (ch === 'ntfy') { out.topic = i.topic.value.trim(); out.server = i.server.value.trim() || 'https://ntfy.sh'; } else out.url = i.url.value.trim();
    return out;
  };
  async function save() {
    msg = ''; bad = false;
    try { data = await ctx.net.post('/api/notify', { channels: Object.fromEntries(CHANNELS.map((ch) => [ch, read(ch)])) }); msg = T('saved'); } catch (e) { msg = T('error', { error: e.message }); bad = true; }
    render();
  }
  async function test(ch, out) {
    out.className = 'vas-result small grey'; out.textContent = T('testing');
    try {
      const r = await ctx.net.post('/api/notify/test', { channel: ch, settings: read(ch) });
      out.className = 'vas-result small ' + (r.ok ? 'green' : 'red'); out.textContent = r.ok ? T('test_ok') : T('test_fail', { error: r.error });
    } catch (e) { out.className = 'vas-result small red'; out.textContent = T('test_fail', { error: e.message }); }
  }
  function field(label, value, placeholder, tip) {
    const row = el('div', 'ws-row');
    const inp = el('input', 'esc-input vas-input', null, { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: placeholder || '' });
    inp.value = value || '';
    row.append(el('span', 'ws-label vas-flabel', label), inp);
    row.dataset.tip = tip;
    return [row, inp];
  }
  function render() {
    clear(box);
    box.append(el('div', 'vas-p', T('desc')), el('div', 'vas-p small grey', T('privacy')));
    if (!data) { if (msg) box.append(el('div', 'vas-p red', msg)); return; }
    for (const ch of CHANNELS) {
      const c = data.channels[ch], i = (inputs[ch] = { events: {} });
      const sec = el('div', 'vas-ch');
      const head = el('label', 'vas-chhead');
      i.enabled = el('input', 'ws-box', null, { type: 'checkbox' });
      i.enabled.checked = !!c.enabled;
      head.append(i.enabled, el('span', 'gold shadow', T(ch)));
      head.dataset.tip = 'notifyhub_' + ch;
      sec.append(head, el('div', 'small grey vas-chdesc', T(ch + '_desc')));
      if (ch === 'ntfy') {
        let r; [r, i.topic] = field(T('topic'), c.topic, T('topic_hint'), 'notifyhub_ntfy'); sec.append(r);
        [r, i.server] = field(T('server'), c.server, 'https://ntfy.sh', 'notifyhub_ntfy'); sec.append(r);
      } else { const [r, inp] = field(T('url'), c.url, T(ch + '_hint'), 'notifyhub_' + ch); i.url = inp; sec.append(r); }
      const evs = el('div', 'vas-events');
      evs.dataset.tip = 'notifyhub_events';
      for (const e of data.events) {
        const l = el('label', 'vas-ev small');
        const b = el('input', null, null, { type: 'checkbox' });
        b.checked = c.events.includes(e);
        i.events[e] = b;
        l.append(b, el('span', null, T('ev_' + e)));
        evs.append(l);
      }
      const foot = el('div', 'vas-foot');
      const tb = el('button', 'btn btn-blue', T('test'), { type: 'button' });
      tb.dataset.tip = 'notifyhub_test';
      const out = el('span', 'vas-result small');
      tb.addEventListener('click', () => test(ch, out));
      foot.append(tb, out);
      sec.append(evs, foot);
      box.append(sec);
    }
    const sv = el('button', 'btn btn-green vas-save', T('save'), { type: 'button' });
    sv.dataset.tip = 'notifyhub_save';
    sv.addEventListener('click', save);
    box.append(sv);
    if (msg) box.append(el('div', 'vas-p ' + (bad ? 'red' : 'green'), msg));
  }
  load();
  return box;
}
