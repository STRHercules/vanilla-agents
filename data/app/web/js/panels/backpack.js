// Backpack (B): work products as items in a bag. Checker reports (newest first) and files agents changed; the border colour is the item quality.
import { clear, el, icon } from '../dom.js';
import { ago, colorVar, qualityOf, took } from './bookutil.js';

export function build(ctx, panel) {
  const c = panel.cfg, Q = ctx.cfg.metaphors.item_quality, tips = ctx.cfg.strings.tooltips;
  const head = el('div', 'book-head small grey');
  const grid = el('div', 'bag-grid');
  grid.style.gridTemplateColumns = 'repeat(' + c.options.columns + ', 40px)';
  const foot = el('div', 'book-note small grey');
  panel.body.append(head, grid, foot);
  let runs = [], state = null, loaded = false, key = '';

  async function load() {
    try { runs = (await ctx.net.get('/api/runs')).runs; } catch (e) { runs = []; }
    draw();
  }

  const runItem = (r) => {
    const q = qualityOf(ctx, r.verdict), word = Q[q].word;
    const result = q === 'VERIFIED' ? 'item.result_verified' : q === 'FAILED' ? 'item.result_failed' : 'item.result_blocked';
    return { q, icon: q === 'FAILED' ? c.icons.run_failed : c.icons.run, name: r.title || r.run_id, word,
      what: ctx.t('item.run_what', { commit: String(r.commit || '').slice(0, 7) }),
      when: ctx.t('item.when', { when: ago(ctx, r.ts) }) + (r.seconds ? ' ' + ctx.t('item.took', { seconds: took(ctx, r.seconds) }) : ''),
      result: ctx.t(result, { reason: q === 'BLOCKED' ? r.reason || '' : '' }) };
  };
  const fileItem = (f) => ({ q: 'FILE', icon: c.icons.file, name: f.file.split(/[\/]/).pop(), word: Q.FILE.word,
    what: ctx.t('item.file_what', { tool: f.tool }) + '\n' + f.file, when: ctx.t('item.when', { when: ago(ctx, f.ts) }), result: ctx.t('item.result_file') });

  function items() {
    const v = state && state.verify, out = [];
    if (v && v.running) out.push({ q: 'RUNNING', icon: c.icons.running, name: ctx.t(c.text.running_name), word: Q.RUNNING.word, what: ctx.t('item.running_what'),
      when: ctx.t('item.when', { when: ctx.t('time.now') }), result: ctx.t('item.result_running') });
    runs.slice(0, c.options.max_runs).forEach((r) => out.push(runItem(r)));
    ((state && state.files) || []).slice(0, c.options.max_files).forEach((f) => out.push(fileItem(f)));
    return out;
  }

  function draw() {
    const all = items(), shown = all.slice(0, c.options.slots);
    clear(grid);
    head.textContent = all.length ? ctx.t(c.text.count, { count: all.length }) : ctx.t(c.text.empty);
    for (let i = 0; i < c.options.slots; i++) {
      const it = shown[i];
      const slot = el('div', 'slot bag-slot');
      if (it) {
        const q = Q[it.q];
        slot.append(icon(it.icon, 'slot-icon'));
        if (q.border !== 'white' && q.border !== 'grey') {      // only real quality (green = verified) gets an edge; plain items stay clean
          const border = el('div', 'bag-border');
          border.style.borderColor = colorVar(q.border);
          slot.append(border);
        }
        slot.dataset.liveTip = '1';
        slot.tipData = { title: it.name, titleColor: q.color, body: ctx.t(q.word) + '\n' + it.what + '\n' + it.when, last: { text: it.result, color: q.color } };
      } else slot.dataset.tip = c.tips.empty_slot;
      grid.append(slot);
    }
    foot.textContent = all.length > shown.length ? ctx.t(c.text.more, { count: all.length - shown.length }) : '';
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen()) load(); };

  return function update(s) {
    const was = !!(state && state.verify && state.verify.running), now = !!(s.verify && s.verify.running);
    const k = JSON.stringify([s.files, now]);
    state = s;
    if (!loaded) { loaded = true; load(); }
    else if (was && !now) load();       // a run just finished: its report is in the run log
    else if (k !== key) draw();
    key = k;
  };
}
