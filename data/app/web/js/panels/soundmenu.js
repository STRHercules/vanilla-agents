// Settings > Sound: master volume + mute, and one row per event (sound, volume, On, Test). Every change is saved at once.
import { clear, el } from '../dom.js';

export function build(ctx, panel) {
  const sound = ctx.sound;
  const t = (k, v) => ctx.t(panel.cfg.text[k], v);
  const view = el('div', 'snd');
  panel.body.append(view);
  const msg = el('div', 'small grey snd-msg', '');

  const slider = (v, onChange) => {
    const s = el('input', 'snd-range', null, { type: 'range', min: '0', max: '100', step: '1' });
    s.value = Math.round(v * 100);
    s.addEventListener('change', () => onChange(s.value / 100));
    return s;
  };
  const check = (v, onChange) => {
    const c = el('input', 'snd-check', null, { type: 'checkbox' });
    c.checked = v;
    c.addEventListener('change', () => onChange(c.checked));
    return c;
  };
  const save = async (patch) => {
    try { await sound.save(patch); msg.className = 'small green snd-msg'; msg.textContent = t('saved'); }
    catch (e) { msg.className = 'small red snd-msg'; msg.textContent = t('error', { error: e.message }); }
  };

  function render() {
    clear(view);
    const s = sound.settings;
    const top = el('div', 'snd-row snd-master');
    top.append(el('span', 'gold snd-label', t('master')), slider(s.master.volume, (v) => save({ master: { volume: v } })),
      check(s.master.mute, (m) => save({ master: { mute: m } })), el('span', 'small', t('mute')));
    top.dataset.tip = 'setting_sound_master';      // V-037: every option explains itself
    view.append(top);
    const hrow = el('div', 'snd-row snd-helpers');
    const hsel = el('select', 'snd-select');
    for (const m of ['off', 'ask', 'on']) { const o = el('option', null, t('helpers_' + m), { value: m }); o.selected = m === (s.helpers || 'off'); hsel.append(o); }
    hsel.addEventListener('change', () => save({ helpers: hsel.value }));
    hrow.append(el('span', 'gold snd-label', t('helpers')), hsel);
    hrow.dataset.tip = 'setting_sound_helpers';
    view.append(hrow);
    for (const [name, e] of Object.entries(s.events)) {
      const row = el('div', 'snd-row');
      row.dataset.event = name;
      row.dataset.tip = 'setting_sound_event';
      const sel = el('select', 'snd-select');
      for (const f of sound.files) { const o = el('option', null, f.label, { value: f.id }); o.selected = f.id === e.sound; sel.append(o); }
      let vol = e.volume;
      sel.addEventListener('change', () => save({ events: { [name]: { sound: sel.value } } }));
      const test = el('button', 'wow-btn snd-test', t('test'), { type: 'button' });
      test.addEventListener('click', () => sound.test(name, { sound: sel.value, volume: vol }));
      row.append(el('span', 'snd-label', e.label), sel, slider(e.volume, (v) => { vol = v; save({ events: { [name]: { volume: v } } }); }),
        check(e.enabled, (on) => save({ events: { [name]: { enabled: on } } })), el('span', 'small snd-on', t('enabled')), test);
      view.append(row);
    }
    const back = el('button', 'wow-btn', t('back'), { type: 'button' });
    back.addEventListener('click', () => { panel.toggle(); ctx.panels.esc_menu.toggle(); });
    view.append(msg, back);
  }
  return { update() {}, show() { msg.textContent = ''; render(); } };
}
