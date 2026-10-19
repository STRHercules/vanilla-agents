// Player frame (the controller: quests done as the gold bar, usage as mana) and the target line (the selected agent).
import { bar, el, icon } from '../dom.js';
import { drawAuras } from '../auras.js';
import { nameFor } from '../workers.js';

export function build(ctx, panel) {
  const c = panel.cfg;
  const t = (k, v) => ctx.t(c.text[k], v);
  const player = el('div', 'unit-row');
  const ring = el('div', 'ring ring-small');
  ring.dataset.tip = c.tips.player;
  const portrait = icon(c.icons.player, 'ring-pic');
  const badge = el('div', 'ring-badge', '0');
  ring.append(portrait, el('div', 'ring-frame'), badge);
  const col = el('div', 'unit-col');
  const name = el('div', 'unit-name gold shadow', t('player_name'));
  name.dataset.tip = c.tips.player;
  const health = bar('--xp', c.tips.health);
  const mana = bar('--mana', c.tips.mana);
  const paused = el('div', 'unit-paused red shadow', t('paused'));
  paused.hidden = true;
  const auras = el('div', 'aura-row');
  col.append(name, health.root, mana.root, auras, paused);
  player.append(ring, col);

  const target = el('div', 'unit-target');
  target.dataset.tip = c.tips.target;
  const tIcon = icon(c.icons.target, 'unit-target-icon');
  const tName = el('div', 'unit-target-name');
  target.append(tIcon, tName);
  panel.body.append(player, target);

  return function update(s) {
    drawAuras(ctx, auras, s.auras.player);
    paused.hidden = !(s.verify && s.verify.paused);
    badge.textContent = String(s.agents.filter((a) => a.state === 'working').length);
    const { done, total } = s.counts;
    health.set(total ? (100 * done) / total : 0, t('health', { done, total }));
    const plan = s.usage.five_hour, week = s.usage.seven_day, used = s.usage.used_pct;
    if (plan && typeof plan.used_pct === 'number') {     // V-030: the real 5-hour plan limit from Claude Code's status line
      const when = plan.resets_at ? new Date(plan.resets_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '?';
      const v = { pct: Math.round(plan.used_pct), when, n: week && typeof week.used_pct === 'number' ? Math.round(week.used_pct) : 0 };
      mana.set(100 - plan.used_pct, t(week && typeof week.used_pct === 'number' ? 'mana_week' : 'mana_plan', v));
    } else if (used === null || used === undefined) mana.set(0, t('mana_unknown'));
    else mana.set(100 - used, t('mana', { pct: Math.round(used) }));
    const a = s.agents.find((x) => x.id === ctx.selected);
    tName.textContent = a ? nameFor(a.id) + (a.role ? ' (' + a.role + ')' : '') : t('target_none');
    tName.className = 'unit-target-name ' + (a ? 'gold' : 'grey');
    target.classList.toggle('has-target', !!a);
  };
}
