// AddOns window (V-051, docs/ADDONS.md): the installed addons on the left (checkbox = enabled, name, version, author, summary,
// folder or old single file), the chosen one's details on the right (description, what it says it does, licence, links, Enable /
// Package / Remove), Install from .zip and Browse the Workshop at the top, Reload window to apply. Opened from the Game Menu and
// Options > Vanilla Agents (no hotkey). Every change is a POST with the window key; addons never change themselves.
import { clear, el, icon } from '../dom.js';

const MAX_ZIP = 2 * 1024 * 1024;      // until /api/addons says (app.json addons.max_zip_bytes); the server checks again

export function build(ctx, panel) {
  const c = panel.cfg, t = (k, v) => ctx.t(c.text[k], v);
  const top = el('div', 'ad-top'), body = el('div', 'ad-body'), foot = el('div', 'ad-foot');
  const list = el('div', 'ad-list'), side = el('div', 'ad-side');
  body.append(list, side);
  panel.body.append(top, body, foot);
  let data = { addons: [] }, sel = null, view = 'list', msg = null, changed = false, pending = null, pkg = null, shop = null;

  const btn = (label, cls, fn, tip) => { const b = el('button', 'btn ' + cls, label, { type: 'button' }); b.addEventListener('click', fn); if (tip) b.dataset.tip = tip; return b; };
  const say = (text, color) => { msg = text ? { text, color: color || 'green' } : null; drawFoot(); };
  const fail = (e) => say(t('error', { error: e.message }), 'red');
  const current = () => data.addons.find((a) => a.key === sel) || null;

  async function load() {
    try { data = await ctx.net.get('/api/addons'); } catch (e) { fail(e); }
    if (sel && !current()) sel = null;
    if (!sel && data.addons.length) sel = data.addons[0].key;
    draw();
  }
  async function call(path, payload, okText) {
    try {
      const r = await ctx.net.post(path, payload);
      if (r.addons) data = r;
      if (okText) say(typeof okText === 'function' ? okText(r) : okText);
      return r;
    } catch (e) { fail(e); return null; }
  }

  // ---- top bar: install, browse, reload
  const file = el('input', 'ad-file', null, { type: 'file', accept: '.zip,application/zip' });
  file.addEventListener('change', () => { const f = file.files[0]; file.value = ''; if (f) installFile(f); });
  function drawTop() {
    clear(top);
    top.append(btn(t('install'), 'btn-green', () => file.click(), c.tips.install), btn(t('browse'), 'btn-blue', browse, c.tips.browse),
      el('div', 'ad-spacer'), btn(t('reload'), 'btn-orange' + (changed ? ' ad-glow' : ''), () => location.reload(), c.tips.reload), file);
  }

  // ---- install from a zip on your disk (base64 in JSON: every POST is JSON with the window key)
  function installFile(f, replace) {
    const max = data.max_zip_bytes || MAX_ZIP;
    if (f.size > max) { say(t('too_big', { n: Math.floor(max / 1024) }), 'red'); return; }
    const rd = new FileReader();
    rd.onload = () => sendZip(String(rd.result), replace);
    rd.onerror = () => say(t('error', { error: String(rd.error) }), 'red');
    say(t('installing'), 'grey');
    rd.readAsDataURL(f);
  }
  async function sendZip(b64, replace) {
    try {
      const r = await ctx.net.post('/api/addons/install', { zip: b64, replace: !!replace });
      afterInstall(r);
    } catch (e) {
      if (/installed already/.test(e.message)) { pending = { kind: 'zip', b64 }; say(t('exists'), 'gold'); }
      else fail(e);
    }
  }
  function afterInstall(r) {
    data = r; pending = null; changed = true; view = 'list'; sel = r.result.installed; pkg = null;
    say(t('installed', { name: r.result.name, version: r.result.version }));
    draw();
  }

  // ---- the list
  function row(a) {
    const r = el('div', 'ad-row' + (a.key === sel ? ' on' : '') + (a.problem ? ' bad' : ''));
    const box = el('input', 'ad-check', null, { type: 'checkbox', 'aria-label': t('enabled') + ': ' + a.name });
    box.checked = a.enabled; box.disabled = !!a.problem; box.dataset.tip = c.tips.toggle;
    box.addEventListener('click', (e) => e.stopPropagation());
    box.addEventListener('change', () => toggle(a, box.checked));
    const text = el('div', 'ad-text');
    const head = el('div', 'ad-head');
    head.append(el('span', 'ad-name', a.name), el('span', 'ad-ver small', a.version ? t('version', { version: a.version }) : ''));
    text.append(head, el('div', 'ad-meta small', [a.author ? t('by', { author: a.author }) : '', a.legacy ? t('legacy') : t('kind_folder')].filter(Boolean).join('  ·  ')));
    if (a.problem) text.append(el('div', 'ad-sum small red', t('problem', { error: a.problem })));
    else if (a.summary) text.append(el('div', 'ad-sum small', a.summary));
    const more = btn(t('details'), 'btn-metal ad-more', () => { sel = a.key; pkg = null; draw(); });
    r.append(box, icon(a.legacy ? c.icons.legacy : c.icons.folder, 'ad-icon'), text, more);
    r.addEventListener('click', () => { sel = a.key; pkg = null; draw(); });
    return r;
  }
  async function toggle(a, on) {
    if (await call('/api/addons/enable', { key: a.key, enabled: on }, t('reload_hint'))) changed = true;
    draw();
  }
  function drawList() {
    clear(list);
    list.append(el('div', 'ad-count small grey', t('count', { n: data.addons.length })));
    if (!data.addons.length) list.append(el('div', 'ad-empty grey', t('empty')));
    data.addons.forEach((a) => list.append(row(a)));
  }

  // ---- details of one addon
  const link = (href, label) => { const a = el('a', 'ad-link', label, { href, target: '_blank', rel: 'noopener noreferrer' }); return a; };
  function drawDetails() {
    clear(side);
    const a = current();
    if (pkg) return drawPackage();
    if (!a) { side.append(el('div', 'ad-pick grey', t('pick'))); return; }
    side.append(el('div', 'ad-title gold shadow', a.name));
    side.append(el('div', 'ad-sub small', [a.version ? t('version', { version: a.version }) : '', a.author ? t('by', { author: a.author }) : '', a.legacy ? t('legacy') : t('kind_folder'), a.enabled ? t('enabled') : t('disabled')].filter(Boolean).join('  ·  ')));
    if (a.problem) side.append(el('div', 'ad-para red', t('problem', { error: a.problem })));
    if (a.description || a.summary) { side.append(el('div', 'ad-h gold', t('about'))); (a.description || a.summary).split('\n').forEach((p) => p.trim() && side.append(el('div', 'ad-para', p))); }
    side.append(el('div', 'ad-h gold', t('permissions')));
    const perms = el('ul', 'ad-perms');
    if (a.legacy) perms.append(el('li', 'ad-perm', t('perm_legacy')));
    else if (!a.permissions.length) perms.append(el('li', 'ad-perm', t('perm_none')));
    else a.permissions.forEach((p) => { const li = el('li', 'ad-perm'); li.append(el('span', 'ad-pname', p), document.createTextNode(' ' + t('perm_' + p))); perms.append(li); });
    const trust = el('div', 'ad-trust small', t('trust')); trust.dataset.tip = c.tips.trust;
    side.append(perms, trust);
    if (!a.legacy) {
      side.append(el('div', 'ad-para small', t('license', { text: a.license || '?' })));
      if (!a.has_license && !a.problem) side.append(el('div', 'ad-para small grey', t('no_license_file')));
      const links = el('div', 'ad-links');
      if (a.homepage) links.append(link(a.homepage, t('homepage')));
      if (a.source_url) links.append(link(a.source_url, t('source')));
      if (links.childNodes.length) side.append(links);
    }
    const acts = el('div', 'ad-acts');
    if (!a.problem) acts.append(btn(a.enabled ? t('disable') : t('enable'), a.enabled ? 'btn-metal' : 'btn-green', () => toggle(a, !a.enabled), c.tips.toggle));
    if (!a.legacy && !a.problem) acts.append(btn(t('package'), 'btn-blue', () => makePackage(a), c.tips.package));
    acts.append(btn(t('remove'), 'btn-red', () => { pending = { kind: 'remove', key: a.key, name: a.name }; drawFoot(); }, c.tips.remove));
    side.append(acts);
  }
  async function makePackage(a) {
    const r = await call('/api/addons/package', { key: a.key });
    if (r) { pkg = r.package; msg = null; draw(); }
  }
  function drawPackage() {
    const p = pkg;
    side.append(el('div', 'ad-title gold shadow', t('packaged')));
    side.append(el('div', 'ad-para', t('pkg_where', { path: p.folder })));
    for (const [k, v] of [['pkg_zip', p.zip_name], ['pkg_yml', p.id + '.yml']]) side.append(el('div', 'ad-para ad-mono', t(k, { file: v })));
    side.append(el('div', 'ad-para ad-mono small', t('pkg_sha', { text: p.sha256 })));
    side.append(el('div', 'ad-para ' + (p.rehost ? 'green' : 'gold'), t(p.rehost ? 'pkg_rehost' : 'pkg_link', { text: p.license })));
    if (!p.has_license) side.append(el('div', 'ad-para small grey', t('no_license_file')));
    side.append(el('div', 'ad-h gold', t('submit_head')));
    const ol = el('ol', 'ad-steps');
    ol.append(el('li', null, t('submit_1', { file: p.id + '.yml' })), el('li', null, t('submit_2')), el('li', null, t('submit_3')));
    side.append(ol, el('pre', 'ad-yaml', p.yaml));
    side.append(el('div', 'ad-acts'));
    side.lastChild.append(btn(t('back'), 'btn-metal', () => { pkg = null; draw(); }));
  }

  // ---- the Workshop (online only on this click)
  async function browse() {
    view = 'shop'; shop = null; pkg = null; msg = null; pending = null; draw();
    try { shop = await ctx.net.post('/api/addons/browse', {}); } catch (e) { shop = { error: e.message, entries: [] }; }
    draw();
  }
  async function getFromShop(e, replace) {
    say(t('installing'), 'grey');
    try { afterInstall(await ctx.net.post('/api/addons/workshop_install', { id: e.id, replace: !!replace })); }
    catch (err) { if (/installed already/.test(err.message)) { pending = { kind: 'shop', entry: e }; say(t('exists'), 'gold'); } else fail(err); }
  }
  function drawShop() {
    clear(list); clear(side);
    const wrap = el('div', 'ad-shop');
    wrap.append(el('div', 'ad-title gold shadow', t('browse_title')), el('div', 'ad-para small grey', t('browse_note')));
    if (!shop) wrap.append(el('div', 'ad-empty grey', t('browse_loading')));
    else if (shop.error) wrap.append(el('div', 'ad-empty red', t('error', { error: shop.error })));
    else if (!shop.entries.length) { const e = el('div', 'ad-shop-empty'); e.append(icon(c.icons.workshop, 'ad-big-icon'), el('div', 'ad-empty', t('browse_empty'))); wrap.append(e); }
    else shop.entries.forEach((e) => {
      const r = el('div', 'ad-row');
      const text = el('div', 'ad-text'), head = el('div', 'ad-head');
      head.append(el('span', 'ad-name', e.name), el('span', 'ad-ver small', e.version ? t('version', { version: e.version }) : ''));
      text.append(head, el('div', 'ad-meta small', [e.author ? t('by', { author: e.author }) : '', e.license ? t('license', { text: e.license }) : ''].filter(Boolean).join('  ·  ')), el('div', 'ad-sum small', e.summary));
      r.append(icon(c.icons.folder, 'ad-icon'), text);
      if (e.homepage) r.append(link(e.homepage, t('homepage')));
      r.append(e.installed ? el('span', 'ad-have small green', t('have')) : e.installable ? btn(t('get'), 'btn-green', () => getFromShop(e)) : el('span', 'ad-have small grey', t('link_only')));
      wrap.append(r);
    });
    wrap.append(el('div', 'ad-acts'));
    wrap.lastChild.append(btn(t('back'), 'btn-metal', () => { view = 'list'; draw(); }));
    list.append(wrap);
  }

  // ---- foot: messages, confirmations (Replace / Remove), folder
  function drawFoot() {
    clear(foot);
    if (pending && pending.kind === 'remove') {
      foot.append(el('span', 'ad-msg gold', t('remove_sure', { name: pending.name })),
        btn(t('yes'), 'btn-red', async () => { const k = pending.key; pending = null; const r = await call('/api/addons/remove', { key: k }, (x) => t('removed', { path: x.moved_to })); if (r) { changed = true; sel = null; } load(); }),
        btn(t('cancel'), 'btn-metal', () => { pending = null; drawFoot(); }));
      return;
    }
    if (pending && (pending.kind === 'zip' || pending.kind === 'shop')) {
      const p = pending;
      foot.append(el('span', 'ad-msg gold', t('exists')),
        btn(t('replace'), 'btn-orange', () => (p.kind === 'zip' ? sendZip(p.b64, true) : getFromShop(p.entry, true)), c.tips.replace),
        btn(t('cancel'), 'btn-metal', () => { pending = null; say(null); }));
      return;
    }
    if (msg) foot.append(el('span', 'ad-msg ' + msg.color, msg.text));
    else if (changed) foot.append(el('span', 'ad-msg gold', t('reload_hint')));
    else if (data.folder) foot.append(el('span', 'ad-msg small grey ad-mono', t('folder', { path: data.folder })));
  }

  function draw() {
    drawTop();
    body.classList.toggle('shop', view === 'shop');
    if (view === 'shop') drawShop(); else { drawList(); drawDetails(); }
    drawFoot();
  }

  draw();
  return { update() {}, onOpen() { view = 'list'; pending = null; msg = null; pkg = null; load(); } };
}
