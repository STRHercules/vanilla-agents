// Need or Greed (V-045): competing fixes for one quest, as a group loot roll. The item = the quest; each helper's proposal is a row with its
// portrait, summary, files changed (+/-) and the checker's verdict. Check this one = POST /api/contest check (the project's checks on that branch),
// Show changes = Inspect on the branch (GET /api/diff?branch=), Need = pick the winner (owner only: POST /api/contest pick), Pass = hide the row
// for this session. The dice numbers are cosmetic: the winner's Need roll is always the highest; Reduce motion shows them at once.
// api.open(questId) opens it on that quest; opened without one it shows the newest competition.
import { assetUrl, clear, el, icon } from '../dom.js';
import { nameFor, setPortrait } from '../workers.js';
import { reducedMotion } from '../wsettings.js';
import { quality } from './lootroll.js';

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  panel.root.classList.add('ng');
  const top = el('div', 'ng-top');
  const list = el('div', 'ng-list');
  const log = el('div', 'ng-log small');
  log.dataset.tip = c.tips.log;
  const foot = el('div', 'ng-foot');
  const err = el('div', 'ng-err small red');
  panel.body.append(top, list, log, foot);

  let quest = null, confirm = null, sig = '', last = null, busy = false;
  const passed = new Map();        // quest id -> Set(helper) hidden with Pass (this session only)
  const showPassed = new Set();
  const rolls = new Map();         // quest id -> { winner, nums: {helper: n}, lines: [[cls, text]], start }
  let tick = null;

  const tasks = () => (last && last.tasks) || [];
  function pickQuest() {
    const all = tasks().filter((q) => q.contest);
    const q = all.find((x) => x.id === quest);
    if (q) return q;
    const open = all.filter((x) => x.contest.open).sort((a, b) => b.contest.ts - a.contest.ts);
    return open[0] || all.sort((a, b) => b.contest.ts - a.contest.ts)[0] || null;
  }

  function lootBtn(kind, tip, label, fn) {
    const b = el('button', 'loot-btn ng-roll-btn', null, { type: 'button', 'aria-label': label });
    b.style.setProperty('--up', 'url("' + assetUrl(c.icons[kind]) + '")');
    b.style.setProperty('--down', 'url("' + assetUrl(c.icons[kind + '_down']) + '")');
    b.style.setProperty('--hl', 'url("' + assetUrl(c.icons[kind + '_hl']) + '")');
    b.dataset.tip = tip;
    b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
    return b;
  }

  async function post(body) {
    if (busy) return null;
    busy = true; err.textContent = '';
    try { const s = await ctx.net.post('/api/contest', body); if (s && s.tasks) ctx.render(s); return s; } catch (e) { err.textContent = t('error', { error: e.message }); draw(true); return null; } finally { busy = false; }
  }

  // ---- the roll: numbers spin for options.roll_ms, then settle (winner highest); purely for show ----
  function startRoll(q, winner) {
    const props = q.contest.proposals.map((p) => p.agent);
    const high = 60 + Math.floor(Math.random() * 41);
    const nums = {};
    for (const a of props) nums[a] = a === winner ? high : 1 + Math.floor(Math.random() * (high - 1));
    const lines = props.filter((a) => a !== winner).map((a) => ['ng-greed', t('greed_roll', { n: nums[a], title: q.title, name: nameFor(a) })]);
    lines.push(['ng-need', t('need_roll', { n: nums[winner], title: q.title, name: nameFor(winner) })]);
    lines.push(['ng-won', t('won', { name: nameFor(winner), title: q.title })]);
    rolls.set(q.id, { winner, nums, lines, start: performance.now(), done: reducedMotion() });
    clearInterval(tick);
    if (!reducedMotion()) tick = setInterval(() => { const r = rolls.get(q.id); if (!r || performance.now() - r.start > c.options.roll_ms) { if (r) r.done = true; clearInterval(tick); } draw(true); }, 70);
    draw(true);
  }
  const rollShown = (r, a) => (r.done ? r.nums[a] : 1 + Math.floor(Math.random() * 100));

  function row(q, p, running, roll) {
    const con = q.contest, win = con.winner === p.agent;
    const r = el('div', 'ng-row frame-inset' + (win ? ' ng-win' : '') + (con.winner && !win ? ' ng-lost' : ''));
    r.dataset.tip = c.tips.row;
    const face = el('div', 'ng-face');
    const pic = el('img', 'ng-pic', null, { alt: '', draggable: 'false' });
    setPortrait(pic, p.agent);
    face.append(pic);
    face.addEventListener('click', () => ctx.select(p.agent));
    const mid = el('div', 'ng-mid');
    mid.append(el('div', 'ng-name gold shadow', t('by', { name: nameFor(p.agent) })));
    mid.append(el('div', 'ng-branch small grey', p.worktree ? t('worktree', { branch: p.branch, worktree: p.worktree.split(/[\\/]/).pop() }) : t('branch', { branch: p.branch })));
    mid.append(el('div', 'ng-summary small', p.summary));
    const files = el('div', 'ng-files small');
    files.append(el('span', 'grey', t('files', { files: p.files.length, added: p.added, removed: p.removed })));
    if (p.files.length) { files.dataset.liveTip = '1'; files.tipData = { title: t('files', { files: p.files.length, added: p.added, removed: p.removed }), body: p.files.slice(0, 20).join('\n') }; }
    mid.append(files);
    const isRunning = running && running.quest === q.id && running.agent === p.agent;
    const v = isRunning ? ['verdict_running', 'gold'] : p.verdict === 'VERIFIED' ? ['verdict_pass', 'green'] : p.verdict ? ['verdict_fail', 'red'] : ['verdict_none', 'grey'];
    mid.append(el('div', 'ng-verdict small ' + v[1], t(v[0], { reason: p.reason || p.verdict || '' })));
    const side = el('div', 'ng-side');
    if (roll) {
      const n = el('div', 'ng-num shadow ' + (roll.winner === p.agent ? 'ng-num-need' : 'ng-num-greed'), String(rollShown(roll, p.agent)));
      n.dataset.tip = c.tips.log;
      side.append(n);
    }
    if (con.open) {
      const acts = el('div', 'ng-acts');
      const chk = el('button', 'btn btn-blue ng-btn', t('check'), { type: 'button' });
      chk.dataset.tip = c.tips.check;
      chk.disabled = !!(last && last.verify && last.verify.running) || busy;
      chk.addEventListener('click', () => post({ action: 'check', quest: q.id, agent: p.agent }));
      const show = el('button', 'btn btn-metal ng-btn', t('show'), { type: 'button' });
      show.dataset.tip = c.tips.show;
      show.addEventListener('click', () => ctx.panels.inspect && ctx.panels.inspect.api.openBranch && ctx.panels.inspect.api.openBranch({ branch: p.branch, base: con.base, agent: p.agent }));
      acts.append(chk, show);
      const dice = el('div', 'ng-dice');
      dice.append(lootBtn('need', c.tips.need, t('need'), () => { confirm = p.agent; draw(true); }),
        lootBtn('pass', c.tips.pass, t('pass'), () => { if (!passed.has(q.id)) passed.set(q.id, new Set()); passed.get(q.id).add(p.agent); confirm = null; draw(true); }));
      side.append(dice, acts);
    } else if (con.winner) {
      const show = el('button', 'btn btn-metal ng-btn', t('show'), { type: 'button' });
      show.dataset.tip = c.tips.show;
      show.addEventListener('click', () => ctx.panels.inspect && ctx.panels.inspect.api.openBranch && ctx.panels.inspect.api.openBranch({ branch: p.branch, base: con.base, agent: p.agent }));
      side.append(show);
    }
    r.append(face, mid, side);
    if (confirm === p.agent && con.open) {
      const box = el('div', 'ng-confirm');
      const yes = el('button', 'btn btn-green ng-btn', t('yes'), { type: 'button' });
      const no = el('button', 'btn btn-metal ng-btn', t('no'), { type: 'button' });
      yes.addEventListener('click', async () => { yes.disabled = true; const s = await post({ action: 'pick', quest: q.id, agent: p.agent }); confirm = null; if (s) { const nq = s.tasks.find((x) => x.id === q.id) || q; startRoll(nq, p.agent); } });
      no.addEventListener('click', () => { confirm = null; draw(true); });
      box.append(el('span', 'gold', t('confirm', { name: nameFor(p.agent) })), yes, no);
      const wrap = el('div', 'ng-rowwrap');
      wrap.append(r, box);
      return wrap;
    }
    return r;
  }

  function draw(force) {
    if (!panel.isOpen()) return;
    const q = pickQuest();
    const running = last && last.verify && last.verify.running ? last.verify.target : null;
    const roll = q ? rolls.get(q.id) : null;
    const key = JSON.stringify([q && q.id, q && q.title, q && q.contest, running, confirm, q && [...(passed.get(q.id) || [])], [...showPassed], last && last.verify && last.verify.running, err.textContent]);
    if (!force && key === sig) return;
    sig = key;
    clear(top); clear(list); clear(log); clear(foot);
    if (!q) { list.append(el('div', 'grey ng-empty', t('pick_quest'))); return; }
    quest = q.id;
    const con = q.contest, qual = quality(q);
    const slot = el('div', 'loot-slot ng-slot q-' + qual);
    slot.dataset.tip = c.tips.item;
    slot.append(icon(c.icons['item_' + (['small', 'medium', 'big'].includes(q.size) ? q.size : 'unknown')], 'loot-icon'));
    const info = el('div', 'ng-info');
    info.append(el('div', 'ng-title shadow qt-' + qual, q.title));
    const sub = el('div', 'ng-sub small');
    const sw = icon(c.icons.swords, 'ng-swords');
    sub.append(sw, el('span', 'grey', t('head', { count: con.proposals.length, total: con.tries, branch: con.base || 'HEAD' })));
    info.append(sub);
    top.append(slot, info);
    if (con.winner) top.append(el('div', 'ng-banner gold shadow', t('winner', { name: nameFor(con.winner) })));
    const hidden = passed.get(q.id) || new Set();
    const props = con.proposals.filter((p) => con.winner || !hidden.has(p.agent) || showPassed.has(q.id));
    if (!con.proposals.length) list.append(el('div', 'grey ng-empty', con.open ? t('empty') : t('stopped')));
    for (const p of props) list.append(row(q, p, running, roll));
    const nHidden = con.proposals.length - props.length;
    if (nHidden > 0) {
      const sp = el('button', 'ng-link small grey', t('passed', { count: nHidden }), { type: 'button' });
      sp.addEventListener('click', () => { showPassed.add(q.id); draw(true); });
      list.append(sp);
    }
    if (roll && roll.done) {
      for (const [cls, text] of roll.lines) log.append(el('div', cls, text));
      log.append(el('div', 'ng-told grey', t('told', { name: nameFor(roll.winner), branch: con.base || 'HEAD' })));
    } else if (con.winner && !roll) {
      log.append(el('div', 'ng-won', t('won', { name: nameFor(con.winner), title: q.title })), el('div', 'ng-told grey', t('told', { name: nameFor(con.winner), branch: con.base || 'HEAD' })));
    } else if (!con.open && !con.winner) log.append(el('div', 'grey', t('stopped')));
    log.hidden = !log.childNodes.length;
    if (con.open) {
      const stop = el('button', 'btn btn-red ng-btn', t('stop'), { type: 'button' });
      stop.dataset.tip = c.tips.stop;
      stop.addEventListener('click', () => post({ action: 'stop', quest: q.id }));
      foot.append(stop);
    }
    foot.append(err);
  }

  function open(qid) {
    if (qid) quest = qid;
    confirm = null;
    if (!panel.isOpen()) panel.toggle();
    draw(true);
    return panel;
  }
  function update(s) { last = s; draw(false); }
  return { update, open, onOpen: () => draw(true) };
}
