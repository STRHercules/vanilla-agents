// V-044 CI line: the latest GitHub Actions runs of the open project's branch (s.ci from the server; null when GitHub import is off
// or the project has no workflows). Informational only: a quest is still verified by the local checker.
// text = a panel's text map with ci_passed/ci_failed/ci_running/ci_cancelled/ci_none/ci_unknown and ci_now/ci_min/ci_hour/ci_day.
export function ago(ctx, text, at) {
  const s = Math.max(0, Date.now() / 1000 - (at || 0));
  if (s < 60) return ctx.t(text.ci_now);
  if (s < 3600) return ctx.t(text.ci_min, { n: Math.floor(s / 60) });
  if (s < 86400) return ctx.t(text.ci_hour, { n: Math.floor(s / 3600) });
  return ctx.t(text.ci_day, { n: Math.floor(s / 86400) });
}

export function ciLine(ctx, text, ci) {
  const state = ['passed', 'failed', 'running', 'cancelled', 'none'].includes(ci.state) ? ci.state : 'unknown';
  return { state, text: ctx.t(text['ci_' + state], { name: ci.name || 'CI', when: ago(ctx, text, ci.at), branch: ci.branch || '-', error: ci.error || '' }),
    cls: state === 'failed' ? 'red' : state === 'passed' ? 'green' : state === 'running' ? 'gold' : 'grey' };
}
