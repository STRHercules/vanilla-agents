// Options > Integrations (V-044): GitHub import (gh or a token), Jira / Linear CSV import, optional Linear / Jira API keys.
// Saved by the server in YOUR settings file; secrets are never sent back (only "saved, ends in 1a2b"). Imported items land on the
// Bounty Board (H) as proposals; only the owner's "Make it a quest" there makes a quest. Words: strings/ui.json integ.*, hover texts
// strings/tooltips.json integ_*; source icons come from panels/auction.json icons.
import { clear, el, icon } from './dom.js';

const SERVICES = ['github', 'linear', 'jira'];

export function integrationsPage(ctx) {
  const T = (k, v) => ctx.t('integ.' + k, v);
  const icons = ctx.cfg.panels.auction.icons;
  const box = el('div', 'vas integ');
  let data = null, msg = '', bad = false;
  const inp = { github: {}, linear: {}, jira: {} }, forget = {};
  const line = {};         // service -> status line element

  async function load() {
    try { data = await ctx.net.get('/api/integrations'); } catch (e) { msg = e.message; bad = true; }
    render();
  }
  const read = () => {
    const g = inp.github, l = inp.linear, j = inp.jira;
    const out = {
      refresh_minutes: Math.max(1, Math.min(1440, parseInt(inp.minutes.value, 10) || 10)),
      github: { enabled: g.enabled.checked, mode: g.mode.value, issues: g.issues.checked, labels: g.labels.value, prs: g.prs.value, host: g.host.value.trim(), token: g.token.value.trim() },
      linear: { enabled: l.enabled.checked, api_key: l.api_key.value.trim(), team: l.team.value.trim(), mine: l.mine.checked },
      jira: { enabled: j.enabled.checked, site: j.site.value.trim(), email: j.email.value.trim(), token: j.token.value.trim(), jql: j.jql.value.trim() },
    };
    if (forget.github) out.github.clear_token = true;
    if (forget.linear) out.linear.clear_api_key = true;
    if (forget.jira) out.jira.clear_token = true;
    return out;
  };
  async function save() {
    msg = ''; bad = false;
    try { data = await ctx.net.post('/api/integrations', { settings: read() }); msg = T('saved'); for (const k of SERVICES) forget[k] = false; } catch (e) { msg = T('error', { error: e.message }); bad = true; }
    render();
  }
  const via = (how) => T('via_' + (['gh', 'token', 'demo', 'api'].includes(how) ? how : 'api'));
  function statusText(svc) {
    const s = data && data.status && data.status[svc];
    if (!s || s.ok === null) return [data && data[svc] && data[svc].enabled ? T('never') : T('off'), 'grey'];
    if (!s.ok) return [T('failed', { error: s.error }), 'red'];
    return [T(s.count ? 'signed_in_count' : 'signed_in', { name: s.who, tool: via(s.how), count: s.count }), 'green'];
  }
  function showStatus(svc) {
    if (!line[svc]) return;
    const [text, cls] = statusText(svc);
    line[svc].className = 'vas-result small ' + cls; line[svc].textContent = text;
  }
  async function test(svc) {
    const out = line[svc];
    out.className = 'vas-result small grey'; out.textContent = T('testing');
    try {
      const r = await ctx.net.post('/api/integrations/test', { service: svc, settings: read() });
      out.className = 'vas-result small ' + (r.ok ? 'green' : 'red');
      out.textContent = r.ok ? T('signed_in', { name: r.who, tool: via(r.how) }) + (r.repo ? ' ' + T('repo', { project: r.repo }) : '') : T('failed', { error: r.error });
    } catch (e) { out.className = 'vas-result small red'; out.textContent = T('failed', { error: e.message }); }
  }
  async function refresh(svc, btn) {
    btn.disabled = true;
    line[svc].className = 'vas-result small grey'; line[svc].textContent = T('refreshing');
    try { data = await ctx.net.post('/api/integrations/refresh', { source: svc }); showStatus(svc); } catch (e) { line[svc].className = 'vas-result small red'; line[svc].textContent = T('failed', { error: e.message }); }
    btn.disabled = false;
  }

  // ---- small builders ----
  function field(svc, key, label, value, placeholder, tip, type) {
    const row = el('div', 'ws-row');
    const i = el('input', 'esc-input vas-input', null, { type: type || 'text', spellcheck: 'false', autocomplete: 'off', placeholder: placeholder || '' });
    i.value = value || '';
    row.append(el('span', 'ws-label vas-flabel integ-flabel', label), i);
    row.dataset.tip = tip;
    inp[svc][key] = i;
    return row;
  }
  function secret(svc, key, label, hint, tip) {
    const d = data[svc], set = d[key + '_set'], end = d[key + '_end'];
    const row = field(svc, key, label, '', set ? (end ? T('secret_saved', { word: end }) : T('secret_saved_short')) : hint, tip, 'password');
    if (set) {
      const b = el('button', 'btn btn-metal integ-forget', T('forget'), { type: 'button' });
      b.dataset.tip = 'integ_forget';
      b.addEventListener('click', () => { forget[svc] = true; inp[svc][key].value = ''; inp[svc][key].placeholder = T('forgot'); b.disabled = true; });
      row.append(b);
    }
    return row;
  }
  function check(svc, key, label, value, tip, cls) {
    const l = el('label', cls || 'vas-ev small integ-check');
    const b = el('input', cls ? 'ws-box' : null, null, { type: 'checkbox' });
    b.checked = !!value;
    inp[svc][key] = b;
    l.append(b, el('span', cls ? 'gold shadow' : null, label));
    l.dataset.tip = tip;
    return l;
  }
  function select(svc, key, label, value, options, tip) {
    const row = el('div', 'ws-row');
    const s = el('select', 'ws-select integ-select');
    for (const [v, text] of options) { const o = el('option', null, text, { value: v }); if (v === value) o.selected = true; s.append(o); }
    inp[svc][key] = s;
    row.append(el('span', 'ws-label vas-flabel integ-flabel', label), s);
    row.dataset.tip = tip;
    return row;
  }
  function head(svc, title, on, tip, iconKey) {
    const h = el('div', 'integ-head');
    if (iconKey) h.append(icon(icons[iconKey], 'integ-icon'));
    if (on !== undefined) h.append(check(svc, 'enabled', title, on, tip, 'vas-chhead'));
    else h.append(el('span', 'gold shadow integ-title', title));
    return h;
  }
  function foot(svc, withRefresh) {
    const f = el('div', 'vas-foot');
    const tb = el('button', 'btn btn-blue', T('test'), { type: 'button' });
    tb.dataset.tip = 'integ_test';
    tb.addEventListener('click', () => test(svc));
    f.append(tb);
    if (withRefresh) {
      const rb = el('button', 'btn btn-metal', T('refresh'), { type: 'button' });
      rb.dataset.tip = 'integ_refresh';
      rb.addEventListener('click', () => refresh(svc, rb));
      f.append(rb);
    }
    line[svc] = el('span', 'vas-result small');
    f.append(line[svc]);
    return f;
  }

  // ---- file import ----
  function fileBox() {
    const sec = el('div', 'vas-ch');
    const h = el('div', 'integ-head');
    h.append(icon(icons.src_jira, 'integ-icon'), icon(icons.src_linear, 'integ-icon'), el('span', 'gold shadow integ-title', T('files')));
    const out = el('span', 'vas-result small');
    const st = data.status && data.status.file;
    if (st && st.ok) { out.className = 'vas-result small green'; out.textContent = T('imported', { count: st.count, name: ctx.t('auction.src_' + st.how), n: st.skipped || 0 }); }
    const picker = el('input', 'integ-file', null, { type: 'file', accept: '.csv,text/csv' });
    picker.hidden = true;
    const btn = el('button', 'btn btn-green', T('import'), { type: 'button' });
    btn.dataset.tip = 'integ_import';
    btn.addEventListener('click', () => picker.click());
    picker.addEventListener('change', async () => {
      const f = picker.files && picker.files[0];
      picker.value = '';
      if (!f) return;
      if (f.size > data.max_file_bytes) { out.className = 'vas-result small red'; out.textContent = T('too_big', { n: Math.round(data.max_file_bytes / 1000) }); return; }
      out.className = 'vas-result small grey'; out.textContent = T('importing');
      try {
        const r = await ctx.net.post('/api/integrations/import', { text: await f.text() });
        const name = ctx.t('auction.src_' + r.imported.kind);
        out.className = 'vas-result small green'; out.textContent = T('imported', { count: r.imported.count, name, n: r.imported.skipped });
      } catch (e) { out.className = 'vas-result small red'; out.textContent = T('import_error', { error: e.message }); }
    });
    const row = el('div', 'vas-foot');
    row.append(btn, out, picker);
    sec.append(h, el('div', 'small grey vas-chdesc', T('files_desc')), row);
    return sec;
  }

  function render() {
    clear(box);
    box.append(el('div', 'vas-p', T('desc')), el('div', 'vas-p small grey', T('privacy')));
    if (!data) { if (msg) box.append(el('div', 'vas-p red', msg)); return; }
    if (data.fake) box.append(el('div', 'vas-warn', T('demo')));
    const g = data.github;
    const gh = el('div', 'vas-ch');
    gh.append(head('github', T('github_on'), g.enabled, 'integ_github', 'src_github'), el('div', 'small grey vas-chdesc', T('github_desc')),
      select('github', 'mode', T('mode'), g.mode, [['gh', T('mode_gh')], ['token', T('mode_token')]], 'integ_mode'),
      secret('github', 'token', T('token'), T('token_hint'), 'integ_token'));
    const issues = el('div', 'ws-row integ-pair');
    issues.append(check('github', 'issues', T('issues'), g.issues, 'integ_labels'));
    const lab = field('github', 'labels', T('labels'), (g.labels || []).join(', '), T('labels_hint'), 'integ_labels');
    gh.append(issues, lab, select('github', 'prs', T('prs'), g.prs, data.pr_modes.map((m) => [m, T('pr_' + m)]), 'integ_prs'),
      field('github', 'host', T('host'), g.host, T('host_hint'), 'integ_host'));
    const mins = el('div', 'ws-row');
    mins.dataset.tip = 'integ_minutes';
    inp.minutes = el('input', 'esc-input integ-num', null, { type: 'number', min: '1', max: '1440', step: '1' });
    inp.minutes.value = data.refresh_minutes;
    mins.append(el('span', 'ws-label vas-flabel integ-flabel', T('minutes')), inp.minutes);
    gh.append(mins, foot('github', true));
    const sync = () => { const tok = inp.github.mode.value === 'token'; inp.github.token.closest('.ws-row').hidden = !tok; lab.hidden = !inp.github.issues.checked; };
    inp.github.mode.addEventListener('change', sync);
    inp.github.issues.addEventListener('change', sync);
    sync();
    box.append(gh, fileBox());

    const l = data.linear, ln = el('div', 'vas-ch');
    ln.append(head('linear', T('linear'), l.enabled, 'integ_linear', 'src_linear'), secret('linear', 'api_key', T('api_key'), T('api_key_hint'), 'integ_linear'),
      field('linear', 'team', T('team'), l.team, T('team_hint'), 'integ_linear'), check('linear', 'mine', T('mine'), l.mine, 'integ_linear'), foot('linear', true));
    const j = data.jira, jr = el('div', 'vas-ch');
    jr.append(head('jira', T('jira'), j.enabled, 'integ_jira', 'src_jira'), field('jira', 'site', T('site'), j.site, T('site_hint'), 'integ_jira'),
      field('jira', 'email', T('email'), j.email, '', 'integ_jira'), secret('jira', 'token', T('token'), '', 'integ_jira'),
      field('jira', 'jql', T('jql'), j.jql, T('jql_hint'), 'integ_jira'), foot('jira', true));
    box.append(ln, jr);
    for (const s of SERVICES) showStatus(s);
    const sv = el('button', 'btn btn-green vas-save', T('save'), { type: 'button' });
    sv.dataset.tip = 'integ_save';
    sv.addEventListener('click', save);
    box.append(sv);
    if (msg) box.append(el('div', 'vas-p ' + (bad ? 'red' : 'green'), msg));
  }
  load();
  return box;
}
