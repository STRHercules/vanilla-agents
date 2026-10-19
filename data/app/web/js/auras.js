// Buff/debuff icons (metaphors.json auras): small game icons with a green (buff) or red (debuff) edge and a count. Used by the player frame and the raid frames.
import { icon, el } from './dom.js';

export function drawAuras(ctx, row, list) {
  const defs = ctx.cfg.metaphors.auras;
  row.replaceChildren(...list.map((a) => {
    const d = defs[a.id];
    const n = el('div', 'aura aura-' + d.kind);
    n.dataset.tip = d.tip;
    n.append(icon(d.icon, 'aura-icon'));
    if (a.count > 1) n.append(el('span', 'aura-count shadow', a.count));
    if (a.expires_at) {
      const left = Math.max(0, Math.round(a.expires_at - Date.now() / 1000));
      n.append(el('span', 'aura-time shadow', left >= 3600 ? Math.round(left / 3600) + 'h' : left >= 60 ? Math.round(left / 60) + 'm' : left + 's'));
    }
    return n;
  }));
}
