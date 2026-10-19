// One window on screen: frame, optional title plate, close button, body. Position, size, frame and title all come from panels/<name>.json.
import { el } from './dom.js';

export function createPanel(ctx, name) {
  const c = ctx.cfg.panels[name];
  const root = el('section', 'panel frame frame-' + c.frame);
  root.dataset.panel = name;
  const a = c.anchor;
  for (const side of ['left', 'right', 'top', 'bottom']) if (a[side] !== undefined) root.style[side] = a[side] + 'px';
  if (a.center_x) { root.style.left = '50%'; root.style.transform = 'translateX(-50%)'; }
  root.style.width = c.size.w + 'px';
  root.style.height = c.size.h + 'px';
  if (c.title) root.append(el('div', 'panel-title shadow', ctx.t(c.title)));
  // screen readers: pop-up windows are dialogs, always-there windows regions, both named by their title (V-033)
  root.setAttribute('role', c.visible ? 'region' : 'dialog');
  root.setAttribute('aria-label', c.title ? ctx.t(c.title) : ctx.t('a11y.' + name));
  root.tabIndex = -1;
  // the window you opened or clicked last is drawn on top; Esc closes the last opened pop-up window first (main.js ctx.escape)
  ctx.opened = ctx.opened || [];
  const front = () => { ctx.zTop = (ctx.zTop || 10) + 1; root.style.zIndex = ctx.zTop; };
  const api = { root, cfg: c, front, isOpen() { return !root.hidden; },
    toggle() {
      root.hidden = !root.hidden; ctx.opened = ctx.opened.filter((n) => n !== name);
      if (!root.hidden && !c.visible) document.querySelectorAll('#toasts .toast').forEach((t) => t.remove());   // a banner never covers the window you just opened
      if (!root.hidden) { ctx.opened.push(name); front(); const a = document.activeElement; if (!c.visible && !root.contains(a) && !(a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) root.focus({ preventScroll: true }); }
      else if (root.contains(document.activeElement)) document.activeElement.blur();      // focus never stays inside a hidden window
      if (!root.hidden && api.api && typeof api.api.onOpen === 'function') api.api.onOpen();      // a window that loads fresh data each time it opens (V-042)
    } };
  root.addEventListener('mousedown', front);
  if (c.hotkey || name === 'monitor' || name === 'unit_frames' || name === 'raid_frames' || name === 'guide' || name === 'contest' || name === 'addons') {      // the red x of the mockup: hides the window (hotkey or Esc brings it back)
    const x = el('button', 'panel-close', '×', { type: 'button', 'aria-label': ctx.t('panel.close') });
    x.dataset.tip = 'panel_close';
    x.addEventListener('click', (e) => { e.stopPropagation(); api.toggle(); });
    root.append(x);
  }
  const body = el('div', 'panel-body');
  root.append(body);
  api.body = body;
  root.hidden = !c.visible;
  ctx.ui.append(root);
  return api;
}
