// The Idea (I): what the project is for, what it must not become, what done means. A badge says draft / approved / changed; one button approves it.
import { clear, el, icon } from '../dom.js';

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  const head = el('div', 'idea-head');
  const body = el('div', 'idea-body');
  panel.body.append(head, body);
  let data = null, project = null, cursor = null, err = '';

  async function load() {
    try { data = await ctx.net.get('/api/idea'); err = ''; } catch (e) { data = { state: 'none' }; }
    draw();
  }

  async function call(path, bad, body) {
    err = '';
    try { data = await ctx.net.post(path, body || {}); } catch (e) { err = t(bad, { error: e.message }); }
    draw();
  }

  function section(key, items, text) {
    const s = el('div', 'idea-section');
    const h = el('div', 'idea-h small gold', t(key));
    h.dataset.tip = c.tips[key];
    s.append(h);
    if (text) s.append(el('div', 'idea-line', text));
    else if (!items.length) s.append(el('div', 'idea-line small grey', t('nothing')));
    else items.slice(0, c.options.max_lines).forEach((x) => s.append(el('div', 'idea-line', '• ' + x)));
    if (!text && items.length > c.options.max_lines) s.append(el('div', 'idea-line small grey', '… +' + (items.length - c.options.max_lines)));
    return s;
  }

  function draw() {
    clear(head); clear(body);
    const st = data ? data.state : 'none';
    if (st === 'none' || !data) { body.append(el('div', 'empty grey', t('none'))); return; }
    if (st === 'missing') {
      body.append(el('div', 'empty grey', t('missing')));
      const b = el('button', 'wow-btn idea-btn', t('draft'), { type: 'button' });
      b.dataset.tip = c.tips.draft;
      b.addEventListener('click', () => { b.disabled = true; call('/api/idea/draft', 'draft_error'); });
      body.append(b);
      if (err) body.append(el('div', 'small red', err));
      return;
    }
    const badge = el('div', 'idea-badge small ' + (st === 'approved' ? 'green' : st === 'changed' ? 'red' : 'gold'), (st === 'approved' ? '✓ ' : '! ') + t(st));
    badge.dataset.tip = c.tips[st];
    head.append(icon(c.icons.scroll, 'idea-icon'), badge);
    if (data.summary) body.append(el('div', 'idea-line gold idea-summary', data.summary));        // D-377: the one mandatory yes, in plain words
    body.append(section('goal', [], data.goal), section('non_goals', data.non_goals), section('done_means', data.done_means));
    if (data.needs_plain) {
      const np = el('div', 'idea-line small gold', t('needs_plain', { count: data.needs_plain }));
      np.dataset.tip = c.tips.needs_plain;
      body.append(np);
    }
    if (data.out_of_scope.length) body.append(section('out_of_scope', data.out_of_scope));
    if (st !== 'approved') {
      const b = el('button', 'wow-btn idea-btn', t('approve'), { type: 'button' });
      b.dataset.tip = c.tips.approve;
      b.addEventListener('click', () => { b.disabled = true; call('/api/idea/approve', 'approve_error', { key: data.key }); });        // the key of the Idea as shown: if it changed meanwhile, the server refuses
      body.append(b);
    }
    if (err) body.append(el('div', 'small red', err));
  }

  const toggle = panel.toggle;
  panel.toggle = () => { toggle(); if (panel.isOpen()) load(); };

  return function update(s) {
    const p = s.project ? s.project.path : '';
    if (p !== project || (panel.isOpen() && s.cursor !== cursor)) { project = p; cursor = s.cursor; load(); }
  };
}
