// Game-style tooltips: gold title, white body, green "Use:" line. Text comes from strings/tooltips.json (plain-language, fixed).
import { clear, el } from './dom.js';

export function initTooltips(cfg) {
  const box = document.getElementById('tooltip');
  const tips = cfg.strings.tooltips;

  function place(x, y) {
    const w = box.offsetWidth, h = box.offsetHeight;
    box.style.left = Math.max(4, Math.min(x + 16, window.innerWidth - w - 4)) + 'px';
    box.style.top = Math.max(4, Math.min(y + 16, window.innerHeight - h - 4)) + 'px';
  }

  // a node shows either a fixed tooltip (data-tip = key in strings/tooltips.json) or a live one built by its panel (node.tipData = {title, body, use, titleColor, last: {text, color}})
  let owner = null;        // the element the tooltip belongs to (a drawn canvas spot passes a plain object instead)
  function show(n, x, y) {
    owner = n;
    const d = n.tipData || tips[n.dataset.tip];
    if (!d) return hide();
    clear(box);
    const title = el('div', 'tip-title', d.title);
    if (d.titleColor) title.style.color = 'var(--' + d.titleColor + ')';
    box.append(title, el('div', 'tip-body', d.body));
    if (d.last) { const l = el('div', 'tip-last', d.last.text); l.style.color = 'var(--' + d.last.color + ')'; box.append(l); }
    if (d.use) box.append(el('div', 'tip-use', d.use));
    box.hidden = false;
    place(x, y);
  }

  function hide() {
    box.hidden = true;
  }

  document.addEventListener('mouseover', (e) => {
    const n = e.target.closest && e.target.closest('[data-tip],[data-live-tip]');
    if (n) show(n, e.clientX, e.clientY);
    else if (!e.target.closest || !e.target.closest('canvas')) hide();
  });
  document.addEventListener('mousemove', (e) => {
    if (box.hidden) return;
    // left the element (or it was redrawn under the pointer, which fires no mouseout): hide instead of leaving it stuck
    if (owner instanceof Element && (!owner.isConnected || !owner.contains(e.target))) { const n = e.target.closest && e.target.closest('[data-tip],[data-live-tip]'); if (n) show(n, e.clientX, e.clientY); else hide(); return; }
    place(e.clientX, e.clientY);
  });
  document.addEventListener('mouseleave', hide);
  return { show, hide };
}
