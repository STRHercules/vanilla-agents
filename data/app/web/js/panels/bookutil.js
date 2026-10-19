// Shared by the Spellbook and Backpack: plain-language times and durations, and the outcome -> look mapping (all wording and colours come from config).
export function ago(ctx, ts) {
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 45) return ctx.t('time.now');
  if (d < 3600) return ctx.t('time.minutes', { count: Math.max(1, Math.round(d / 60)) });
  if (d < 86400) return ctx.t('time.hours', { count: Math.round(d / 3600) });
  return ctx.t('time.days', { count: Math.round(d / 86400) });
}

export function took(ctx, sec) {
  return sec < 90 ? ctx.t('duration.seconds', { count: Math.round(sec) }) : ctx.t('duration.minutes', { count: Math.round(sec / 60) });
}

// The item-quality name (VERIFIED, FAILED, BLOCKED) for a checker outcome; anything not listed counts as BLOCKED (metaphors.json item_quality).
export function qualityOf(ctx, outcome) {
  const q = ctx.cfg.metaphors.item_quality;
  return Object.keys(q).find((k) => q[k].outcomes.includes(outcome)) || 'BLOCKED';
}

export const colorVar = (name) => 'var(--' + name.replace(/_/g, '-') + ')';
