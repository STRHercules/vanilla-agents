// Quest log (L): a task is a quest. Gold ! = new, gold ? = ready to hand in, grey ? = in progress; objectives carry check marks.
import { assetUrl, clear, el, icon } from '../dom.js';
import { nameFor } from '../workers.js';
import { ciLine } from '../ci.js';

const ORDER = ['ready', 'new', 'progress', 'blocked', 'failed', 'complete'];

function objectiveMark(verdict) {
  if (verdict === null || verdict === undefined) return { ch: '•', cls: 'grey' };
  return verdict === 'VERIFIED' ? { ch: '✓', cls: 'green' } : { ch: '✗', cls: 'red' };
}

export function build(ctx, panel) {
  const c = panel.cfg;
  const states = ctx.cfg.metaphors.task_states;
  const head = el('div', 'quest-head small grey');
  const list = el('div', 'quest-list');
  panel.body.append(head, list);
  let ci = null;       // V-044: s.ci, the branch's GitHub Actions runs (read-only extra evidence; the checker still decides)

  function row(task) {
    const st = states[task.status];
    const r = el('div', 'quest');
    r.dataset.tip = st.tip;
    const mark = el('div', 'quest-mark');
    if (st.marker) {
      const i = el('img', 'icon', null, { alt: '' });
      i.src = assetUrl(ctx.cfg.theme.markers[st.marker]);
      mark.append(i);
    }
    const info = el('div', 'quest-info');
    const title = el('div', 'quest-title shadow', task.title);
    title.style.color = 'var(--' + st.color.replace(/_/g, '-') + ')';
    const unapproved = task.status === 'complete' && !task.counts;      // D-313: done, but approval is required and not given yet
    if (task.details) {        // D-364: the original technical wording lives in the tooltip only
      title.dataset.liveTip = '1';
      title.tipData = { ...ctx.cfg.strings.tooltips[c.tips.details], body: task.details };
    }
    info.append(title, el('div', 'quest-word small ' + (task.reason ? '' : 'grey'), ctx.t(unapproved ? c.text.unapproved : st.word) + (task.reason ? ' - ' + task.reason : '')));
    if (task.why) info.append(el('div', 'quest-why small', ctx.t(c.text.why, { reason: task.why })));
    const facts = [task.size && ctx.t(c.text['size_' + task.size]), task.risk === 'high' && ctx.t(c.text.risk_high)].filter(Boolean);
    if (facts.length) info.append(el('div', 'quest-facts small ' + (task.risk === 'high' ? 'red' : 'grey'), facts.join(' · ')));
    if (task.needs_plain) {
      const np = el('div', 'quest-needs small gold', ctx.t(c.text.needs_plain));
      np.dataset.tip = c.tips.needs_plain;
      info.append(np);
    }
    const act = (label, tip, fn, color = 'green') => {       // one small button that calls the server once; colour like the mockup (green yes, orange change, blue investigate)
      const b = el('button', 'btn btn-' + color + ' quest-approve', ctx.t(label), { type: 'button' });
      b.dataset.tip = tip;
      b.addEventListener('click', async (e) => {
        e.stopPropagation();
        b.disabled = true;
        try { await fn(b); } catch (err) { b.disabled = false; b.textContent = ctx.t(c.text.approve_error, { error: err.message }); }
      });
      return b;
    };
    const tier = task.tier;
    if (task.approved && task.approved_by === 'auto') {        // D-372: the auto-approved buff icon with its reason, and the veto until the first check
      const ok = el('div', 'quest-approved small green');
      ok.append(icon(ctx.cfg.metaphors.auras.auto.icon, 'aura-icon'), ctx.t(c.text.tier_auto, { reason: task.approved_why }));
      ok.dataset.tip = c.tips.tier_auto;
      info.append(ok);
      if (task.vetoable) info.append(act(c.text.tier_veto, c.tips.tier_veto, () => ctx.net.post('/api/quest', { task: task.id, action: 'veto' }), 'red'));
    } else if (task.approved) {        // D-314: the plan-approved mark
      const ok = el('div', 'quest-approved small green', ctx.t(c.text.approved));
      ok.dataset.tip = c.tips.approved;
      info.append(ok);
    } else {
      if (task.vetoed) info.append(el('div', 'quest-needs small gold', ctx.t(c.text.tier_vetoed)));
      if (task.explain_pending || task.change_pending) info.append(el('div', 'quest-needs small gold', ctx.t(c.text.waiting)));
      {
        if (tier && tier.tier === 'never') {        // D-387: never done automatically; the owner's own Yes is still allowed
          const nv = el('div', 'quest-facts small red', ctx.t(c.text.tier_never, { reason: tier.why }));
          nv.dataset.tip = c.tips.tier_never;
          info.append(nv);
        } else if (tier) info.append(el('div', 'quest-facts small gold', ctx.t(c.text.tier_ask, { reason: tier.why })));
        const row = el('div', 'quest-decide');
        row.append(act(tier ? c.text.tier_yes : c.text.approve, tier ? c.tips.tier_yes : c.tips.approve, () => ctx.net.post('/api/approve', { task: task.id })));
        if (tier) {
          ['change', 'explain'].forEach((k) => row.append(act(c.text['tier_' + k], c.tips['tier_' + k], async (b) => {
            if (k === 'change') {       // ask WHAT should change, in the owner's own words, before sending
              const box = el('input', 'esc-input quest-note', null, { type: 'text', maxlength: '300', placeholder: ctx.t(c.text.change_prompt) });
              const send = act(c.text.change_send, c.tips.tier_change, async (sb) => {
                await ctx.net.post('/api/quest', { task: task.id, action: 'change', note: box.value });
                sb.textContent = ctx.t(c.text.tier_asked); box.disabled = true;
              });
              b.replaceWith(box, send);
              box.focus();
              return;
            }
            await ctx.net.post('/api/quest', { task: task.id, action: k });
            b.textContent = ctx.t(c.text.tier_asked);
          }, k === 'change' ? 'orange' : 'blue')));
        }
        info.append(row);
      }
    }
    if (task.answer) info.append(el('div', 'quest-facts small green', ctx.t(c.text.answer, { text: task.answer })));        // D-386: a Claude session's answer to 'Explain it more'
    if (task.done_elsewhere !== undefined) info.append(el('div', 'quest-facts small green', ctx.t(c.text.done_elsewhere, { note: task.done_elsewhere })));       // D-414
    (task.sent_back || []).forEach((sb) => {       // D-322: the failing check and its evidence, from the checker's own finding
      const line = el('div', 'quest-sentback small red', ctx.t(c.text.sent_back, { check: sb.check, evidence: sb.evidence }));
      line.dataset.tip = c.tips.sent_back;
      info.append(line);
    });
    if (task.route) {       // D-306: the route the checker chose, in plain words
      const rt = el('div', 'quest-route small grey', ctx.t(c.text['route_' + task.route.route], { reason: task.route.why }));
      rt.dataset.tip = c.tips['route_' + task.route.route];
      info.append(rt);
    }
    task.objectives.forEach((o) => {
      const m = objectiveMark(o.verdict);
      const line = el('div', 'objective small');
      line.dataset.tip = c.tips.objective;
      line.append(el('span', 'obj-mark ' + m.cls, m.ch), el('span', null, o.check));
      info.append(line);
    });
    info.append(compete(task, act));       // V-045: several helpers try, you roll for the winner
    if (ci && ['progress', 'ready'].includes(task.status)) {       // a quest being worked on or handed in: show CI next to its objectives
      const l = ciLine(ctx, c.text, ci);
      const line = el('div', 'objective small quest-ci ' + l.cls + (l.state === 'failed' ? ' quest-ci-failed' : ''));
      line.dataset.tip = c.tips.ci;
      line.append(el('span', 'obj-mark ' + l.cls, l.state === 'failed' ? '\u2717' : l.state === 'passed' ? '\u2713' : '\u2022'), el('span', null, l.text));
      info.append(line);
    }
    const badge = el('div', 'quest-badge ' + (task.approved || task.status === 'complete' ? 'qb-ok' : tier && !task.approved ? 'qb-warn' : 'qb-none'));
    badge.textContent = task.approved || task.status === 'complete' ? '\u2713' : tier ? '!' : '';
    r.append(mark, info, badge);
    return r;
  }

  function compete(task, act) {
    const line = el('div', 'quest-compete small');
    const con = task.contest, swords = ctx.cfg.panels.contest && ctx.cfg.panels.contest.icons.swords;
    const openRoll = () => { if (ctx.panels.contest) ctx.panels.contest.api.open(task.id); };
    if (con && (con.open || con.winner)) {
      if (swords) line.append(icon(swords, 'quest-compete-mark'));
      line.append(el('span', 'gold', con.winner ? ctx.t(c.text.compete_won, { name: nameFor(con.winner) }) : ctx.t(c.text.competing, { count: con.proposals.length, total: con.tries })));
      const roll = el('button', 'btn btn-blue', ctx.t(c.text.compete_roll), { type: 'button' });
      roll.dataset.tip = c.tips.compete_roll;
      roll.addEventListener('click', (e) => { e.stopPropagation(); openRoll(); });
      line.append(roll);
      return line;
    }
    if (task.status === 'complete' || task.status === 'failed') return line;
    const pick = el('select', null, null, { 'aria-label': ctx.t(c.text.compete) });
    for (const n of [2, 3, 4]) pick.append(el('option', null, ctx.t(c.text.compete_tries, { n }), { value: String(n) }));
    pick.addEventListener('click', (e) => e.stopPropagation());
    line.append(act(c.text.compete, c.tips.compete, async () => {
      const s = await ctx.net.post('/api/contest', { action: 'open', quest: task.id, tries: parseInt(pick.value, 10) });
      if (s && s.tasks) ctx.render(s);
    }, 'metal'), pick);
    return line;
  }

  let sig = '';
  return function update(s) {
    ci = s.ci || null;
    const k = JSON.stringify([s.tasks, ci, ci ? Math.floor(Date.now() / 60000) : 0]);       // redraw only when a quest or the CI line changed (state arrives often)
    if (k === sig) return;
    sig = k;
    clear(list);
    head.textContent = ctx.t(c.text.objectives) + ' / ' + ctx.t(c.text.count, { count: s.tasks.length });
    if (!s.tasks.length) {
      list.append(el('div', 'empty grey', ctx.t(c.text.empty)));
      return;
    }
    const sorted = s.tasks.slice().sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
    sorted.slice(0, c.options.max_tasks).forEach((task) => list.append(row(task)));
  };
}
