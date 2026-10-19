// Talk window (WoW gossip frame): the helper's face, name and job, a sentence in its own voice about what it is doing,
// a list of things to ask (each with a vanilla gossip icon) and the whisper thread with it. Opened with ctx.talk(agentId).
import { clear, el, icon } from '../dom.js';
import { nameFor, setPortrait, workerFor, historyOf, loadRoster } from '../workers.js';
import { resurrect, canResurrect } from '../resurrect.js';

const EVERYONE = 'everyone';
let voicesP = null;
const voices = () => (voicesP = voicesP || fetch('/assets/sound/voices/manifest.json').then((r) => (r.ok ? r.json() : {})).catch(() => ({})));

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v), o = c.options;
  const x = el('button', 'panel-close', '×', { type: 'button', 'aria-label': t('close') });
  x.dataset.tip = c.tips.close;
  x.addEventListener('click', (e) => { e.stopPropagation(); close(); });
  panel.root.append(x);

  const head = el('div', 'gossip-head');
  const ring = el('div', 'ring ring-small gossip-ring');
  ring.dataset.tip = c.tips.portrait;
  const pic = icon(c.icons.portrait, 'ring-pic');
  ring.append(pic, el('div', 'ring-frame'));
  const who = el('div', 'gossip-who');
  const name = el('div', 'gossip-name');
  const role = el('div', 'gossip-role small');
  who.append(name, role);
  head.append(ring, who);
  const greet = el('div', 'gossip-greet');
  greet.dataset.tip = c.tips.greeting;
  const divider = () => { const d = icon(c.icons.divider, 'gossip-break'); return d; };
  const view = el('div', 'gossip-view');
  const threadBox = el('div', 'gossip-thread');
  threadBox.dataset.tip = c.tips.thread;
  const threadHead = el('div', 'gossip-thread-head small', t('thread'));
  const thread = el('div', 'gossip-thread-log');
  threadBox.append(threadHead, thread);
  panel.body.append(head, greet, divider(), view, threadBox);

  let agent = null, mode = 'main', note = '', noteBad = false, last = null, viewSig = '', threadSig = '', copyCmd = '';

  // ---- voices: a random line of this hero's race and sex, at the master volume (silent when muted) ----
  async function say(kind) {
    const w = workerFor(agent);
    const st = ctx.sound && ctx.sound.settings && ctx.sound.settings.master;
    if (!w || (st && st.mute)) return;
    const list = ((await voices())[w.race + '_' + w.sex] || {})[kind] || [];
    if (!list.length) return;
    const a = new Audio('/assets/' + list[Math.floor(Math.random() * list.length)]);
    a.volume = Math.max(0, Math.min(1, (st && typeof st.volume === 'number' ? st.volume : 1) * o.voice_volume_pct / 100));
    a.play().catch(() => {});
  }

  // a helper that went home (V-039) is still someone to talk to: Resurrect brings its Claude session back
  const agentOf = (s) => (s && (s.agents.find((a) => a.id === agent) || (s.departed || []).find((a) => a.id === agent))) || null;
  const place = (a) => (a && a.branch ? t(a.worktree ? 'place_wt' : 'place', { branch: a.branch, worktree: a.worktree || '' }) : '');
  const questOf = (s, a) => (s && s.tasks.find((q) => q.id === (a && a.task))) || null;
  const checks = (q) => ({ passed: (q.objectives || []).filter((x) => x.verdict === 'VERIFIED').length, total: (q.objectives || []).length });

  function greeting(s) {
    const a = agentOf(s), q = questOf(s, a), parts = [];
    if (a && a.left !== undefined) return t('greet_left');
    const act = a && a.state === 'working' && a.activity ? a.activity.trim().replace(/[.!]+$/, '') : '';
    if (act) parts.push(t(/^[a-z]+ing\b/.test(act) ? 'greet_activity' : 'greet_doing', { activity: act }));      // "checking the fix" reads as a sentence; "Edit a.rs" is a step
    else if (q && (q.status === 'progress' || q.status === 'new')) parts.push(t('greet_quest', { title: q.title }));
    else if (!q || q.status === 'complete') parts.push(t('greet_idle'));
    if (q && ['ready', 'failed', 'blocked', 'complete'].includes(q.status)) parts.push(t('greet_' + q.status, { title: q.title }));
    if (q && q.objectives && q.objectives.length) parts.push(t('greet_checks', checks(q)));
    return parts.join(' ');
  }

  // ---- server calls: render the returned snapshot, say "yes" when it worked ----
  async function post(path, body, okNote) {
    note = ''; noteBad = false; copyCmd = '';
    try {
      const s = await ctx.net.post(path, body);
      if (s && s.agents) ctx.render(s);
      note = okNote; say('yes');
    } catch (e) { note = t('error', { error: e.message }); noteBad = true; }
    draw(true);
  }

  // V-039: a new terminal resumes this helper's Claude session (the owner's click only)
  async function doResurrect() {
    note = ''; noteBad = false; copyCmd = '';
    try {
      const r = await resurrect(ctx, agent);
      if (r.ok) { note = t('resurrect_ok', { name: nameFor(agent) }); say('yes'); } else { note = r.command ? t('resurrect_fail', { error: r.error }) : t('error', { error: r.error }); noteBad = true; copyCmd = r.command || ''; }
    } catch (e) { note = t('error', { error: e.message }); noteBad = true; }
    draw(true);
  }

  function option(key, act) {
    const row = el('button', 'gossip-opt', null, { type: 'button' });
    const k = key === 'unfollow' ? 'follow' : key;        // same icon and hover text as Follow me
    row.dataset.tip = c.tips[k];
    row.append(icon(c.icons[k], 'gossip-icon'), el('span', null, t('opt_' + key)));
    row.addEventListener('click', act);
    return row;
  }
  const go = (m) => () => { mode = m; note = ''; draw(true); };
  const line = (text, cls) => el('div', 'gossip-line ' + (cls || ''), text);
  const bullet = (text, cls) => { const r = el('div', 'gossip-bullet ' + (cls || '')); r.append(icon(c.icons.bullet, 'gossip-dot'), el('span', null, text)); return r; };
  function back() {
    const b = el('button', 'btn btn-metal gossip-back', t('back'), { type: 'button' });
    b.addEventListener('click', go('main'));
    return b;
  }
  function form(key, send) {
    const box = el('input', 'esc-input gossip-input', null, { type: 'text', maxlength: '300', placeholder: t(key) });
    const b = el('button', 'btn btn-green', t('send'), { type: 'button' });
    const fire = () => { if (box.value.trim()) { b.disabled = true; send(box.value.trim()); } };
    b.addEventListener('click', fire);
    box.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); fire(); } else if (e.key === 'Escape') box.blur(); });
    const r = el('div', 'gossip-form');
    r.append(box, b);
    requestAnimationFrame(() => box.focus());
    return r;
  }

  function drawView(s) {
    const a = agentOf(s), q = questOf(s, a);
    clear(view);
    const back2 = a && a.left !== undefined;
    if (mode === 'main' && back2) {          // gone home: what it did, bring it back, goodbye
      view.append(option('work', go('work')));
      if (canResurrect(a)) view.append(option('resurrect', doResurrect));
      view.append(option('bye', close));
    } else if (mode === 'main') {
      view.append(option('work', go('work')), option('changes', () => {
        const open = ctx.panels.inspect && ctx.panels.inspect.api && ctx.panels.inspect.api.open;
        if (open) open(agent); else go('changes')();
      }));
      if (q) view.append(option('explain', () => { mode = 'explain'; post('/api/quest', { task: q.id, action: 'explain' }, t('explain_asked', { title: q.title })); }),
        option('change', go('change')));
      if (q) view.append(option('verify', async () => { await ctx.actions.verify(); note = t('verify_sent'); noteBad = false; say('yes'); draw(true); }));
      view.append(option('whisper', go('whisper')));
      // V-028: inn extras; "Are you still there?" for a helper that went quiet or is waiting
      if (ctx.follow) view.append(option(ctx.following(agent) ? 'unfollow' : 'follow', () => { const on = ctx.follow(agent); note = t(on ? 'follow_on' : 'follow_off', { name: nameFor(agent) }); noteBad = false; draw(true); }));
      if (ctx.takeBreak) view.append(option('rest', () => { const ok = ctx.takeBreak(agent); note = t(ok ? 'rest_ok' : 'rest_busy', { name: nameFor(agent) }); noteBad = !ok; draw(true); }));
      const quiet = a && (a.needs || a.stunned || (a.seen && Date.now() / 1000 - (typeof a.seen === 'number' ? a.seen : Date.parse(a.seen) / 1000) > 900));
      if (quiet) view.append(option('wake', () => post('/api/whisper', { to: agent, text: t('wake_text') }, t('whisper_sent'))));
      if (canResurrect(a)) view.append(option('resurrect', doResurrect));
      view.append(option('bye', close));
    } else if (mode === 'work') {
      const h = historyOf(agent).slice(-o.max_lines);
      if (place(a)) view.append(line(place(a), 'gossip-place'));
      const clash = (s && s.clashes || []).find((x) => x.agents.includes(agent));
      if (clash) view.append(line(t('clash', { file: clash.file.split(/[\\/]/).pop(), names: clash.agents.filter((x) => x !== agent).map(nameFor).join(', ') }), 'red'));
      if (a && a.todo && a.todo.length) { view.append(line(t('todo_head'), 'gossip-h')); a.todo.forEach((x) => view.append(bullet(x.text, x.done ? 'green' : ''))); }
      view.append(line(t('work_recent'), 'gossip-h'));
      if (!h.length) view.append(line(t('work_none'), 'grey'));
      h.forEach((x) => view.append(bullet(x)));
      if (q) {
        view.append(line(t('work_quest', { title: q.title }), 'gossip-h'));
        if (!(q.objectives || []).length) view.append(line(t('work_no_objectives'), 'grey'));
        (q.objectives || []).forEach((ob) => view.append(bullet(ob.check, ob.verdict === 'VERIFIED' ? 'green' : ob.verdict ? 'red' : '')));
      }
    } else if (mode === 'changes') {
      const seen = new Set(), files = [];
      for (const f of [...(s ? s.files : [])].reverse()) if (f.agent === agent && !seen.has(f.file)) { seen.add(f.file); files.push(f.file); }
      view.append(line(t(files.length ? 'files_head' : 'files_none'), files.length ? 'gossip-h' : 'grey'));
      files.slice(0, o.max_files).forEach((f) => view.append(bullet(f, 'gossip-file')));
      if (files.length > o.max_files) view.append(line(t('files_more', { count: files.length - o.max_files }), 'grey'));
    } else if (mode === 'explain') {
      if (q && q.answer) view.append(line(t('answer', { text: q.answer })));
    } else if (mode === 'change') {
      if (q) view.append(line(q.title, 'gossip-h'), form('change_prompt', (note2) => { mode = 'main'; post('/api/quest', { task: q.id, action: 'change', note: note2 }, t('change_sent', { title: q.title })); }));
    } else if (mode === 'whisper') {
      view.append(form('whisper_prompt', (text) => { mode = 'main'; post('/api/whisper', { to: agent, text }, t('whisper_sent')); }));
    }
    if (note) view.append(line(note, noteBad ? 'red' : 'gossip-note'));
    if (copyCmd) { const cmd = el('code', 'gossip-cmd', copyCmd); cmd.dataset.tip = c.tips.resurrect_cmd; view.append(cmd); }
    if (note && mode === 'main') view.scrollTop = view.scrollHeight;       // the reply sits under the options: bring it into view
    if (mode !== 'main') view.append(back());
    const box = view.querySelector('input');
    if (box) box.focus();
  }

  function drawThread(s) {
    const msgs = (s ? s.messages || [] : []).filter((m) => m.to === agent || m.from === agent || m.to === EVERYONE).slice(-o.max_thread);
    const sig = agent + '|' + msgs.map((m) => m.ts).join(',');
    if (sig === threadSig) return;
    threadSig = sig;
    clear(thread);
    if (!msgs.length) thread.append(el('div', 'gossip-whisper grey', t('thread_empty')));
    msgs.forEach((m) => thread.append(el('div', 'gossip-whisper',
      m.from === agent ? t('from', { name: nameFor(agent), text: m.text }) : m.to === EVERYONE ? t('to_all', { text: m.text }) : t('to', { name: nameFor(agent), text: m.text }))));
    thread.scrollTop = thread.scrollHeight;
  }

  function draw(force) {
    if (!agent || !panel.isOpen()) return;
    const s = last, a = agentOf(s), q = questOf(s, a);
    name.textContent = nameFor(agent);
    role.textContent = a && a.role ? t('role', { role: a.role }) : '';
    greet.textContent = greeting(s);
    // input views keep their text box; the others follow the state live
    const sig = [mode, agent, q && q.id, q && q.status, q && JSON.stringify(q.objectives), q && JSON.stringify(q.answer || ''), historyOf(agent).join('|'), (s ? s.files.length : 0), !!q, note, a && a.branch, a && a.left, a && a.session, a && a.seen && Math.floor((Date.now() / 1000 - (typeof a.seen === 'number' ? a.seen : 0)) / 60)].join('#');
    if (force || (sig !== viewSig && mode !== 'change' && mode !== 'whisper')) { viewSig = sig; drawView(s); }
    drawThread(s);
  }

  function close() {
    if (!panel.isOpen()) return;
    say('goodbye');
    panel.root.hidden = true;
  }

  ctx.talk = (id) => {
    if (!id) return;
    const again = agent === id && panel.isOpen();
    agent = id; mode = 'main'; note = ''; threadSig = ''; copyCmd = '';
    setPortrait(pic, id);
    panel.root.hidden = false;
    if (!again) say('hello');
    draw(true);
  };
  const toggle = panel.toggle;
  panel.toggle = () => { if (panel.isOpen()) close(); else if (agent) ctx.talk(agent); else toggle(); };
  window.addEventListener('workers-ready', () => draw(true));
  loadRoster();

  return {
    update(s) { last = s; draw(false); },
    open: (id) => ctx.talk(id),
    resurrect: (id) => { ctx.talk(id); return doResurrect(); },
    close,
  };
}
