// Accessibility helpers (V-033): one visually hidden aria-live region that reads new chat lines and banners aloud to screen readers,
// and the in-app "Reduce motion" setting mirrored as a class on <html> so CSS can stop pulses even when the system setting is off.
import { el } from './dom.js';
import { reducedMotion } from './wsettings.js';

const MAX_NODES = 12;
let live = null;

function region() {
  if (live && live.isConnected) return live;
  live = el('div', 'sr-only', null, { 'aria-live': 'polite', 'aria-atomic': 'false', role: 'log', id: 'a11y-live' });
  document.body.append(live);
  return live;
}

// called once with the window's ctx (chat.js): names the region and starts following the reduce-motion setting
export function initA11y(ctx) {
  region().setAttribute('aria-label', ctx.t('a11y.live'));
  const motion = () => document.documentElement.classList.toggle('va-reduced-motion', reducedMotion());
  motion();
  setInterval(motion, 2000);
}

// queue a sentence for screen readers (new nodes in a polite live region are read in turn; old ones are dropped)
export function announce(text) {
  const t = String(text || '').trim();
  if (!t) return;
  const r = region();
  r.append(el('div', null, t));
  while (r.childNodes.length > MAX_NODES) r.removeChild(r.firstChild);
}

// which chat lines are new since the last call (by position after the last line seen); the first call only remembers
export function newLines(lines, seen) {
  if (!seen.init) { seen.init = true; seen.key = lines.length ? lineKey(lines[lines.length - 1]) : ''; seen.n = lines.length; return []; }
  if (!lines.length) { seen.key = ''; seen.n = 0; return []; }
  let i = lines.length - 1;
  while (i >= 0 && lineKey(lines[i]) !== seen.key) i--;
  const fresh = i >= 0 ? lines.slice(i + 1) : lines.slice(-3);     // last line seen scrolled out of the kept log: read only the newest few
  seen.key = lineKey(lines[lines.length - 1]);
  seen.n = lines.length;
  return fresh.slice(-3);
}
const lineKey = (l) => l.ts + '|' + l.channel + '|' + l.text;
