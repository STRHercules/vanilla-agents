// Mailbox (O): a MailFrame-style inbox. Letters are made in the browser from the state and the checker's run log:
// one per finished check run, per quest verified / failed / done elsewhere, per whisper a hero answered, and one daily summary per day.
// Letters, read marks and deleted letters are kept per project in this browser, so a letter stays after its chat line scrolls away.
import { clear, el, icon } from '../dom.js';
import { nameFor } from '../workers.js';
import { ago, qualityOf, took } from './bookutil.js';
import { clockText, dateText, dayOf, loadRuns, named, projectKey, readStore, runFinished, writeStore } from '../runlog.js';

export function build(ctx, panel) {
  const c = panel.cfg, T = (k, v) => ctx.t(c.text[k], v), Q = ctx.cfg.metaphors.item_quality;
  const head = el('div', 'book-head small grey');
  const list = el('div', 'mail-list');
  const view = el('div', 'mail-view');
  view.hidden = true;
  panel.body.classList.add('mail-body');
  panel.body.append(head, list, view);

  // the envelope on the controller frame's corner (minimap-style ring), with the unread count
  const btn = el('button', 'mail-button', null, { type: 'button' });
  btn.dataset.tip = c.tips.button;
  const badge = el('div', 'mail-badge shadow');
  btn.append(icon(c.icons.button, 'mail-button-icon'), icon(c.icons.ring, 'mail-button-ring'), badge);
  btn.addEventListener('click', (e) => { e.stopPropagation(); if (!panel.isOpen()) panel.toggle(); else showList(); });
  const host = ctx.panels.unit_frames ? ctx.panels.unit_frames.root : ctx.ui;
  host.append(btn);

  let state = null, prev = null, runs = [], key = '', box = null, openId = null, drawn = '';

  const storeKey = () => 'mail:' + projectKey(state);
  const loadBox = () => { const b = readStore(storeKey(), {}); return { kept: b.kept || {}, read: b.read || {}, del: b.del || {} }; };
  const save = () => writeStore(storeKey(), box);

  const checker = () => ctx.t('mail.checker'), inn = () => ctx.t('mail.innkeeper');
  const word = (outcome) => ctx.t(Q[qualityOf(ctx, outcome)].word);

  function runLetter(r) {
    const q = qualityOf(ctx, r.verdict);
    const lines = [ctx.t('mail.run_intro', { commit: String(r.commit || '').slice(0, 7) || '?', seconds: took(ctx, r.seconds || 0) }), ''];
    const checks = Array.isArray(r.checks) ? r.checks : [];
    if (checks.length) for (const k of checks) {
      const id = k.check_id || k.check || '';
      const label = ctx.cfg.strings.ui['check.' + id] !== undefined ? ctx.t('check.' + id) : id;
      lines.push(ctx.t('mail.run_check', { check: label, result: word(k.status) }));
    } else lines.push(ctx.t('mail.run_none'));
    lines.push('', q === 'VERIFIED' ? ctx.t('mail.run_verified') : q === 'FAILED' ? ctx.t('mail.run_failed') : ctx.t('mail.run_blocked', { reason: r.reason || '' }));
    return { id: 'run:' + r.run_id, ts: r.ts, icon: q === 'VERIFIED' ? 'run_ok' : 'run_failed', sender: checker(),
      subject: ctx.t('mail.run_subject', { title: r.title || r.run_id, result: word(r.verdict).toUpperCase() }), body: lines.join('\n') };
  }

  const QUEST = { task_verified: ['verified', 'mail.verified_subject', 'mail.verified_body', 'checker'],
    task_failed: ['failed', 'mail.failed_subject', 'mail.failed_body', 'checker'],
    done_elsewhere: ['elsewhere', 'mail.elsewhere_subject', 'mail.elsewhere_body', 'inn'] };

  function questLetter(line) {
    const [ic, subj, body, who] = QUEST[line.type];
    const title = line.title || '';
    return { id: 'q:' + (line.id || line.ts), ts: line.ts, icon: ic, sender: who === 'inn' ? inn() : checker(),
      subject: ctx.t(subj, { title }), body: ctx.t(body, { title }) + '\n\n' + named(state, line.text) };
  }

  function dailyLetters(s) {
    const days = new Map();
    const day = (ts) => { const k = dayOf(ts); if (!days.has(k)) days.set(k, { ts, done: 0, failed: 0, actions: 0, who: new Set() }); const d = days.get(k); d.ts = Math.max(d.ts, ts); return d; };
    for (const l of s.chat || []) {
      const d = day(l.ts);
      if (l.type === 'task_verified' || l.type === 'done_elsewhere') d.done++;
      else if (l.type === 'task_failed') d.failed++;
      else if (l.type === 'tool_use') d.actions++;
    }
    for (const f of s.files || []) if (f.agent) day(f.ts).who.add(f.agent);
    const today = dayOf(Date.now() / 1000);
    if (days.has(today)) for (const a of s.agents || []) if (a.actions > 0 || a.state === 'working') days.get(today).who.add(a.id);
    return [...days.entries()].map(([k, d]) => {
      const when = dateText(d.ts), names = [...d.who].map(nameFor).join(', ');
      const body = ctx.t('mail.daily_body', { when, done: d.done, total: d.failed, count: d.who.size, n: d.actions }) + (names ? '\n' + ctx.t('mail.daily_names', { names }) : '');
      return { id: 'day:' + k, ts: d.ts, icon: 'daily', sender: inn(), subject: ctx.t('mail.daily_subject', { when }), body, daily: true };
    });
  }

  // fold new letters into the kept box (a letter still in the state is rewritten when it changes: the daily summary as the day goes on, hero names once known)
  function collect(s) {
    const fresh = [];
    runs.forEach((r) => r.run_id && fresh.push(runLetter(r)));
    (s.chat || []).forEach((l) => QUEST[l.type] && fresh.push(questLetter(l)));
    (s.messages || []).forEach((m) => { if (m.from && m.from !== 'you') fresh.push({ id: 'w:' + m.ts + ':' + m.from, ts: m.ts, icon: 'whisper', sender: nameFor(m.from), subject: ctx.t('mail.whisper_subject'), body: m.text }); });
    fresh.push(...dailyLetters(s));
    let changed = false;
    for (const L of fresh) {
      const old = box.kept[L.id];
      if (!old || old.body !== L.body || old.sender !== L.sender || old.subject !== L.subject) { box.kept[L.id] = L; changed = true; }
    }
    const ids = Object.keys(box.kept).sort((a, b) => box.kept[b].ts - box.kept[a].ts);
    for (const id of ids.slice(c.options.max_letters)) { delete box.kept[id]; delete box.read[id]; delete box.del[id]; changed = true; }
    if (changed) save();
  }

  const letters = () => Object.values(box.kept).filter((L) => !box.del[L.id]).sort((a, b) => b.ts - a.ts);
  const unread = () => letters().filter((L) => !box.read[L.id]).length;

  function drawBadge() {
    const n = unread();
    badge.textContent = n > 99 ? '99+' : String(n);
    badge.hidden = !n;
    btn.classList.toggle('has-mail', n > 0);
  }

  function showList() { openId = null; view.hidden = true; list.hidden = false; draw(true); }

  function draw(force) {
    if (!box) return;
    drawBadge();
    const all = letters();
    const k = JSON.stringify([all.map((L) => L.id + (box.read[L.id] ? 1 : 0)), openId]);
    if (!force && k === drawn) return;
    drawn = k;
    head.textContent = all.length ? T('count', { count: all.length, n: unread() }) : '';
    if (openId) return;
    clear(list);
    if (!all.length) { list.append(el('div', 'mail-empty grey', T('empty'))); return; }
    for (const L of all) {
      const row = el('div', 'mail-row' + (box.read[L.id] ? '' : ' unread'));
      row.dataset.tip = c.tips.letter;
      const pic = el('div', 'mail-pic');
      pic.append(icon(c.icons[L.icon] || c.icons.daily, 'mail-pic-icon'));
      const col = el('div', 'mail-col');
      col.append(el('div', 'mail-sender gold shadow', L.sender), el('div', 'mail-subject white', L.subject), el('div', 'mail-when small grey', ago(ctx, L.ts)));
      row.append(pic, col);
      row.addEventListener('click', () => openLetter(L.id));
      list.append(row);
    }
  }

  function openLetter(id) {
    const L = box.kept[id];
    if (!L) return;
    openId = id;
    if (!box.read[id]) { box.read[id] = 1; save(); }
    clear(view);
    const paper = el('div', 'mail-paper frame frame-parchment');
    paper.append(el('div', 'mail-paper-from', ctx.t('mail.from', { text: L.sender })), el('div', 'mail-paper-subject', L.subject),
      el('div', 'mail-paper-when', ctx.t('mail.sent', { when: dateText(L.ts) + ' ' + clockText(L.ts) })), el('div', 'mail-paper-rule'),
      el('div', 'mail-paper-text', L.body), el('div', 'mail-paper-sign', ctx.t('mail.sign', { text: L.sender })));
    const row = el('div', 'mail-buttons');
    const del = el('button', 'btn btn-red', T('delete'), { type: 'button' });
    del.dataset.tip = c.tips.delete;
    del.addEventListener('click', () => { box.del[id] = 1; save(); showList(); });
    const back = el('button', 'btn btn-metal', T('back'), { type: 'button' });
    back.dataset.tip = c.tips.back;
    back.addEventListener('click', showList);
    row.append(del, back);
    view.append(paper, row);
    list.hidden = true;
    view.hidden = false;
    draw(true);
  }

  async function refresh(force) {
    runs = await loadRuns(ctx, force);
    if (state) { collect(state); draw(); }
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen()) { showList(); refresh(); } };

  return function update(s) {
    const pk = projectKey(s);
    state = s;
    if (pk !== key) { key = pk; box = loadBox(); openId = null; view.hidden = true; list.hidden = false; refresh(); }
    else if (runFinished(prev, s)) refresh(true);
    prev = s;
    collect(s);
    draw();
  };
}
