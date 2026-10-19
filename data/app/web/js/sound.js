// Plays the sounds. One Audio object per sound file; a repeat of the same sound within 300 ms is ignored (no spam).
// Settings = sounds.json defaults merged with the owner's saved choices (GET/POST /api/settings).
import { soundEventsFor } from './sound_logic.js';
import { postHeaders } from './key.js';

const GAP_MS = 300;

export async function initSound(cfg) {
  const sd = cfg.sounds;
  const audio = new Map();
  const lastPlay = new Map();
  const files = new Map(sd.sounds.map((s) => [s.id, s.path]));
  let settings = await (await fetch('/api/settings')).json();
  let prevAgents = null, prevPct = null;

  const get = (id) => {
    if (!audio.has(id)) { const a = new Audio('/assets/' + files.get(id)); a.preload = 'auto'; audio.set(id, a); }
    return audio.get(id);
  };

  function play(soundId, volume) {
    const now = performance.now();
    if (!files.has(soundId) || now - (lastPlay.get(soundId) ?? -1e9) < GAP_MS) return false;
    lastPlay.set(soundId, now);
    const a = get(soundId);
    a.volume = Math.max(0, Math.min(1, volume));
    a.currentTime = 0;
    a.play().catch(() => {});
    return true;
  }

  const self = {
    get settings() { return settings; },
    files: sd.sounds,
    event(name) {
      const e = settings.events[name];
      if (settings.master.mute || !e || !e.enabled) return false;
      return play(e.sound, settings.master.volume * e.volume);
    },
    ui(soundId, volume) {     // a plain interface sound at a soft volume (Community chat, V-036): follows Mute and the master volume
      return settings.master.mute ? false : play(soundId, settings.master.volume * volume);
    },
    test(name, patch) {      // Test ignores the On box, but not Mute
      const e = { ...settings.events[name], ...patch };
      return settings.master.mute ? false : play(e.sound, settings.master.volume * e.volume);
    },
    async save(patch) {
      const r = await fetch('/api/settings', { method: 'POST', headers: postHeaders(), body: JSON.stringify(patch) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'could not save');
      settings = d;
      return d;
    },
    // called with every server state; events = the new chat lines (none on the first snapshot, so loading never makes noise)
    onUpdate(state, events) {
      const pct = state.usage ? state.usage.used_pct : null;
      if (prevAgents !== null) {
        for (const n of soundEventsFor(sd.triggers, events, prevAgents, state.agents, prevPct, pct, sd.usage_low_pct)) self.event(n);
      }
      prevAgents = state.agents.map((a) => ({ id: a.id, state: a.state }));
      prevPct = pct;
    },
  };
  return self;
}
