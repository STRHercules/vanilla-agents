// Celebration banner (the gold frame from the skin) for quest complete; red for failed, grey for out of range, with a burst of sparks.
// Wording and colours come from metaphors.json and strings/ui.json.
import { el } from './dom.js';
import { reducedMotion } from './wsettings.js';
import { announce } from './a11y.js';

export function showToast(text, colorName, ms = 4600) {
  const box = document.getElementById('toasts');
  const t = el('div', 'toast toast-' + colorName);
  const label = el('div', 'toast-text', text);
  label.style.color = 'var(--' + colorName.replace(/_/g, '-') + ')';
  t.append(label);
  if (colorName === 'gold' && !reducedMotion()) for (let i = 0; i < 18; i++) {
    const s = el('i', 'spark');
    s.style.setProperty('--x', (Math.random() * 120 - 60).toFixed(0) + 'px');
    s.style.setProperty('--y', (-30 - Math.random() * 60).toFixed(0) + 'px');
    s.style.left = (10 + Math.random() * 80).toFixed(1) + '%';
    s.style.animationDelay = (Math.random() * 0.8).toFixed(2) + 's';
    s.style.background = ['#ffd75a', '#b88cff', '#7fe6ff', '#ff9ad5'][i % 4];
    t.append(s);
  }
  box.append(t);
  announce(text);                      // screen readers hear the banner too (V-033)
  setTimeout(() => t.remove(), ms);
}

export function toastForEvents(ctx, events) {
  const cfgT = ctx.cfg.metaphors.toasts;
  for (const ev of events) {
    const m = ev.toast && cfgT[ev.toast];
    if (m) showToast(ctx.t(m.text, { title: ev.title }), m.color);
  }
}
