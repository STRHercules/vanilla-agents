// Options > Chat replies (V-050): answer from Discord, Slack or ntfy. Per service: on/off, the bot token (masked: "saved, ends in 1a2b";
// an empty field keeps the saved one; Forget), the channel id / ntfy server + reply topic, who may command (allow list; ntfy: a
// passphrase), how often to read, Allow pause/resume, Test, and a status line. Saved by the server in YOUR settings file; secrets are
// never sent back. Words: strings/ui.json chatreply.*, hover texts strings/tooltips.json chatreply_*.
import { clear, el } from './dom.js';

const SERVICES = ['discord', 'slack', 'ntfy'];

export function chatRepliesPage(ctx) {
  const T = (k, v) => ctx.t('chatreply.' + k, v);
  const box = el('div', 'vas integ chatr');
  let data = null, msg = '', bad = false, timer = 0;
  const inp = { discord: {}, slack: {}, ntfy: {} }, forget = {}, line = {}, testLine = {};

  async function load() {
    try { data = await ctx.net.get('/api/chat'); } catch (e) { msg = e.message; bad = true; }
    render();
  }
  const secretKey = (svc) => (svc === 'ntfy' ? 'passphrase' : 'token');
  function read(svc) {
    const i = inp[svc];
    const out = { enabled: i.enabled.checked, poll_seconds: Math.max(data.poll_min, Math.min(data.poll_max, parseInt(i.poll.value, 10) || 10)), allow_pause: i.allow_pause.checked };
    out[secretKey(svc)] = i[secretKey(svc)].value.trim();
    if (svc === 'ntfy') { out.server = i.server.value.trim() || 'https://ntfy.sh'; out.topic = i.topic.value.trim(); } else { out.channel = i.channel.value.trim(); out.allow = i.allow.value; }
    if (forget[svc]) out['clear_' + secretKey(svc)] = true;
    return out;
  }
  async function save() {
    msg = ''; bad = false;
    try {
      data = await ctx.net.post('/api/chat', { settings: Object.fromEntries(SERVICES.map((s) => [s, read(s)])) });
      msg = T('saved'); for (const s of SERVICES) forget[s] = false;
    } catch (e) { msg = T('error', { error: e.message }); bad = true; }
    render();
  }
  async function test(svc) {
    const out = testLine[svc];
    out.className = 'vas-result small grey'; out.textContent = T('testing');
    try {
      const body = read(svc); delete body.enabled;
      const r = await ctx.net.post('/api/chat/test', { service: svc, settings: body });
      out.className = 'vas-result small ' + (r.ok ? 'green' : 'red'); out.textContent = r.ok ? T('test_ok', { name: r.where }) : T('test_fail', { error: r.error });
    } catch (e) { out.className = 'vas-result small red'; out.textContent = T('test_fail', { error: e.message }); }
  }
  const ago = (ts) => {
    const s = Math.max(0, Date.now() / 1000 - ts);
    return s < 60 ? T('just_now') : s < 3600 ? T('min_ago', { n: Math.round(s / 60) }) : T('hours_ago', { n: Math.round(s / 3600) });
  };
  function statusText(svc) {
    const s = data.status && data.status[svc], on = data.services[svc].enabled;
    if (!on) return [T('off'), 'grey'];
    if (!s || !s.on || s.ok === null) return [T('starting'), 'grey'];
    if (!s.ok) return [T('failed', { error: s.error }), 'red'];
    const parts = [T('listening', { name: s.where }), s.last_cmd ? T('last_cmd', { when: ago(s.last_cmd) }) : T('no_cmd')];
    if (s.ignored) parts.push(T('ignored', { count: s.ignored }));
    if (s.error) parts.push(T('failed', { error: s.error }));
    if (s.hint) parts.push(s.hint);
    return [parts.join(' '), s.error || s.hint ? 'orange' : 'green'];
  }
  function showStatus() {
    for (const svc of SERVICES) {
      if (!line[svc]) continue;
      const [text, cls] = statusText(svc);
      line[svc].className = 'chatr-status small ' + cls; line[svc].textContent = text;
    }
  }
  async function refreshStatus() {          // only while this page is open: the status line's "2 min ago" and new errors
    if (!box.isConnected) { clearInterval(timer); timer = 0; return; }
    try { const d = await ctx.net.get('/api/chat'); data.status = d.status; showStatus(); } catch (e) { /* keep the last line */ }
  }

  // ---- small builders ----
  function field(svc, key, label, value, placeholder, tip, type) {
    const row = el('div', 'ws-row');
    const i = el('input', 'esc-input vas-input', null, { type: type || 'text', spellcheck: 'false', autocomplete: type === 'password' ? 'new-password' : 'off', placeholder: placeholder || '' });
    i.value = value || '';
    row.append(el('span', 'ws-label vas-flabel integ-flabel', label), i);
    row.dataset.tip = tip;
    inp[svc][key] = i;
    return row;
  }
  function secret(svc, label, hint) {
    const key = secretKey(svc), d = data.services[svc], set = d[key + '_set'], end = d[key + '_end'];
    const row = field(svc, key, label, '', set ? (end ? T('secret_saved', { word: end }) : T('secret_saved_short')) : hint, svc === 'ntfy' ? 'chatreply_passphrase' : 'chatreply_token', 'password');
    if (set) {
      const b = el('button', 'btn btn-metal integ-forget', T('forget'), { type: 'button' });
      b.dataset.tip = 'chatreply_forget';
      b.addEventListener('click', () => { forget[svc] = true; inp[svc][key].value = ''; inp[svc][key].placeholder = T('forgot'); b.disabled = true; });
      row.append(b);
    }
    return row;
  }
  function section(svc) {
    const c = data.services[svc], sec = el('div', 'vas-ch chatr-' + svc);
    const head = el('label', 'vas-chhead');
    inp[svc].enabled = el('input', 'ws-box', null, { type: 'checkbox' });
    inp[svc].enabled.checked = !!c.enabled;
    head.append(inp[svc].enabled, el('span', 'gold shadow', T(svc)));
    head.dataset.tip = 'chatreply_' + svc;
    sec.append(head, el('div', 'small grey vas-chdesc', T(svc + '_desc')));
    if (svc === 'ntfy') {
      sec.append(field(svc, 'server', T('server'), c.server, 'https://ntfy.sh', 'chatreply_ntfy'), field(svc, 'topic', T('topic'), c.topic, T('topic_hint'), 'chatreply_ntfy'),
        secret(svc, T('passphrase'), T('passphrase_hint')));
    } else {
      sec.append(secret(svc, T('token'), T(svc + '_token_hint')), field(svc, 'channel', T('channel'), c.channel, T(svc + '_channel_hint'), 'chatreply_channel'),
        field(svc, 'allow', T('allow'), (c.allow || []).join(', '), T(svc + '_allow_hint'), 'chatreply_allow'));
    }
    const opts = el('div', 'ws-row chatr-opts');
    const poll = el('label', 'chatr-poll small');
    poll.dataset.tip = 'chatreply_poll';
    inp[svc].poll = el('input', 'esc-input integ-num', null, { type: 'number', min: String(data.poll_min), max: String(data.poll_max), step: '1' });
    inp[svc].poll.value = c.poll_seconds;
    poll.append(el('span', null, T('poll')), inp[svc].poll);
    const pz = el('label', 'vas-ev small integ-check');
    pz.dataset.tip = 'chatreply_pause';
    inp[svc].allow_pause = el('input', null, null, { type: 'checkbox' });
    inp[svc].allow_pause.checked = !!c.allow_pause;
    pz.append(inp[svc].allow_pause, el('span', null, T('allow_pause')));
    opts.append(poll, pz);
    const foot = el('div', 'vas-foot');
    const tb = el('button', 'btn btn-blue', T('test'), { type: 'button' });
    tb.dataset.tip = 'chatreply_test';
    tb.addEventListener('click', () => test(svc));
    testLine[svc] = el('span', 'vas-result small');
    foot.append(tb, testLine[svc]);
    line[svc] = el('div', 'chatr-status small');
    line[svc].dataset.tip = 'chatreply_status';
    sec.append(opts, foot, line[svc]);
    return sec;
  }

  function render() {
    clear(box);
    box.append(el('div', 'vas-p', T('desc')), el('div', 'vas-p small grey', T('privacy')));
    if (!data) { if (msg) box.append(el('div', 'vas-p red', msg)); return; }
    if (data.fake) box.append(el('div', 'vas-warn', T('demo')));
    box.append(el('div', 'chatr-commands small', T('commands')));
    for (const svc of SERVICES) box.append(section(svc));
    showStatus();
    const sv = el('button', 'btn btn-green vas-save', T('save'), { type: 'button' });
    sv.dataset.tip = 'chatreply_save';
    sv.addEventListener('click', save);
    box.append(sv);
    if (msg) box.append(el('div', 'vas-p ' + (bad ? 'red' : 'green'), msg));
    if (!timer) timer = setInterval(refreshStatus, 10000);
  }
  load();
  return box;
}
