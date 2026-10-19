// Help (F1): every key, how to move, three lines to get started, and About (version, disclaimer, licences) with a button that saves
// a diagnostics text file for asking for help (POST /api/diagnostics; vanilla_agents/app/diagnostics.py says what is in it).
import { el } from '../dom.js';

const KEY_NAME = { enter: 'Enter', escape: 'Esc' };

// the name shown next to a hotkey: the window's title, else a known action's wording, else the setting name made readable
export function keyLabel(ctx, name) {
  const p = Object.values(ctx.cfg.panels).find((x) => x.hotkey === name);
  if (p && p.title) return ctx.t(p.title);
  const words = { verify: 'action.verify', pause: 'action.pause', jukebox: 'jukebox.key', jukebox_next: 'jukebox.key_next', chat: 'chat.key', esc_menu: 'esc.title' };
  const s = words[name] && ctx.t(words[name]);
  const plain = name.replace(/_/g, ' ');
  return s && s !== words[name] ? s : plain.charAt(0).toUpperCase() + plain.slice(1);
}
export const keyCap = (key) => KEY_NAME[key] || key.toUpperCase();

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  const view = el('div', 'hp');
  panel.body.append(view);
  const label = (name) => keyLabel(ctx, name);
  const keys = el('div', 'hp-keys');
  for (const [name, key] of Object.entries(ctx.cfg.hotkeys)) {
    const row = el('div', 'hp-key');
    row.append(el('kbd', 'hp-kbd', keyCap(key)), el('span', null, label(name)));
    keys.append(row);
  }
  const msg = el('div', 'small hp-msg');
  const diag = el('button', 'btn', t('diag'), { type: 'button' });
  diag.dataset.tip = c.tips.diag;
  diag.addEventListener('click', async () => {
    diag.disabled = true;
    try { const r = await ctx.net.post('/api/diagnostics', {}); msg.className = 'small green hp-msg'; msg.textContent = t('diag_saved', { file: r.file }); } catch (e) { msg.className = 'small red hp-msg'; msg.textContent = t('diag_error', { error: e.message }); }
    diag.disabled = false;
  });
  const rebake = ctx.cfg.rebake || [];      // V-029: art baked by an older version: the exact command, selectable
  if (rebake.length) {
    const cmd = el('code', 'hp-cmd', ctx.t('ws.rebake_cmd', { text: rebake.join(' ') }));
    view.append(el('div', 'red hp-p', ctx.t('ws.rebake_help')), cmd);
  }
  // V-037: the feature guide, first thing in Help
  const guide = el('button', 'btn btn-blue hp-guide', null, { type: 'button' });
  guide.dataset.tip = c.tips.guide;
  guide.append(el('span', 'hp-guide-name', t('guide')), el('span', 'hp-guide-hint', t('guide_hint')));
  guide.addEventListener('click', () => { panel.toggle(); ctx.panels.guide.api.open(); });
  view.prepend(guide);
  view.append(
    el('div', 'gold shadow hp-h', t('start')),
    el('div', 'hp-p', '1. ' + t('start_1')), el('div', 'hp-p', '2. ' + t('start_2')), el('div', 'hp-p', '3. ' + t('start_3')),
    el('div', 'gold shadow hp-h', t('keys')), keys,
    el('div', 'gold shadow hp-h', t('moving')), el('div', 'hp-p', t('move_keys')), el('div', 'hp-p', t('move_mouse')),
    el('div', 'gold shadow hp-h', t('phone')), el('div', 'hp-p', t('phone_text')), el('div', 'hp-phone'),
    el('div', 'gold shadow hp-h', t('about')), el('div', 'hp-p', t('version', { text: ctx.cfg.version || 'dev' })),
    el('div', 'small grey hp-p', t('disclaimer')), el('div', 'small grey hp-p', t('licences')),
    el('div', 'hp-diag'), msg, el('div', 'small grey hp-p', t('diag_hint')));
  view.querySelector('.hp-diag').append(diag);
  // V-040 / V-041: straight to Options > Phone (link + QR code) and Options > Notifications
  const go = (title, key) => { const b = el('button', 'btn btn-metal', t(key), { type: 'button' }); b.dataset.tip = c.tips[key];
    b.addEventListener('click', () => { panel.toggle(); const g = ctx.panels.game_settings; if (g && g.api && g.api.page) g.api.page(ctx.t(title)); }); return b; };
  view.querySelector('.hp-phone').append(go('phonelink.title', 'phone_open'), go('notifyhub.title', 'notify_open'));
  return { update() {} };
}

// the window's own errors go to the server's log (Help > Save diagnostics shows the last ones); a few per minute at most
export function reportErrors(ctx) {
  let sent = 0, since = Date.now();
  const send = (message, source, line, stack) => {
    if (Date.now() - since > 60000) { sent = 0; since = Date.now(); }
    if (++sent > 5 || !ctx.net) return;
    ctx.net.post('/api/log', { message: String(message).slice(0, 500), source: String(source || '').split('/').slice(-2).join('/'), line: String(line || ''), stack: String(stack || '').slice(0, 1500) }).catch(() => {});
  };
  window.addEventListener('error', (e) => send(e.message, e.filename, e.lineno, e.error && e.error.stack));
  window.addEventListener('unhandledrejection', (e) => send(e.reason && e.reason.message || e.reason, '', '', e.reason && e.reason.stack));
}
