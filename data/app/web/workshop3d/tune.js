// Effect numbers that are not (yet) in workshop.json: adding keys there needs schema.py + check_app + CHANGE-GUIDE entries (owned elsewhere), so they live here for now.
// Positions are in the station's own frame (metres), like workshop.json scene3d.layout. Listed in docs/LIMITATIONS.md as candidates to move into workshop.json.
export const TUNE = {
  camera: { zoom_fill: 0.42, glide: 4, zoom_pitch: 40 },                       // share of the window width the screen fills when zoomed; glide speed (1/s)
  leave_idle_s: 60,                                            // an agent idle this long stands up and walks out; it comes back as a NEW random worker when it has a job again
  // legendary weapon standing by the desk: model height (m), position in the station frame, rotation (rad)
  weapon: { h: 1.9, pos: [1.15, 0, 0.55], rot: [0, 0, -0.1] },
  keyboard: { pos: [0, 0.93, 1.02], rot: 0, size: [0.5, 0.03, 0.18] },
  // sack the agent carries in (hand = offset in the right-hand bone's space), where it ends up by the chair, seconds to fall; mug on the desk with steam
  bag: { model: 'models/prop_sack.glb', h: 0.42, bone: { key: 1, child: 1 }, hand: [0, -0.1, 0], floor: [-0.75, 0.02, 1.5], drop_s: 0.35 },
  mug: { model: 'models/prop_mug.glb', h: 0.13, pos: [0.42, 0.93, 0.85], steam_puffs: 5, steam_rise: 0.3 },
  candle: { pos: [0.62, 0.9, 0.5], h: 0.16, intensity: 0.9, range: 2.5 },
  screen: { refresh_hz: 3, glow_base: 0.3, glow_work: 0.25, pulse_s: 0.8, pulse_gain: 0.35 },
  dust: { count: 90, size: 0.06, color: '#ffe0b0', opacity: 0.5, speed: 0.05, min: [-8, 0.3, -3], max: [8, 5, 5] },
  // which baked character model each agent role gets (workshop.json "roles" only holds colours; unknown role = models.character in workshop.json). Model files are made by python -m vanilla_agents.assets bake models.
  role_model: { builder: 'models/character.glb', reviewer: 'models/character_f.glb', scribe: 'models/character_dwarf.glb' },
  // Jukebox (jukebox.js lights + jukeaudio.js sound): music = the game's own tracks (`python -m vanilla_agents.assets bake music`). rows = LED ring heights (share of the model height), arcs = lightning pool.
  jukebox: {
    color: '#cfe8ff', bands: 16, fft: 1024, smoothing: 0.65, volume: 0.7, ref_distance: 3, rolloff: 1.1,
    beat_ratio: 1.25, beat_min: 0.2, beat_gap: 0.16, onset_ratio: 1.8, onset_min: 0.004,
    rows: [0.1, 0.22, 0.34, 0.46, 0.58, 0.7, 0.82, 0.93], per_row: 12, led_size: 0.07, led_decay: 9, standby: 0.1,
    glow_size: 1.25, glow_idle: 0.04, glow_base: 0.06, glow_gain: 0.4, light_range: 4.5, light_gain: 5,
    arcs: 22, arc_points: 9, arc_jitter: 0.11, arc_life: [0.14, 0.36], flux_gain: 0.5, idle_arcs: 3.5,
  },
  // Room around the desks (metres, placed from the desk layout). u = -1..1 across the room width, v = 0 (back wall) .. 1 (front); h = target height; rot in degrees (0 = faces the camera).
  room: {
    tint: { floor: '#6a5a4a', wall: '#6c5c4e' }, tile: { floor: 2.4, wall: 3 }, height: 5.5,
    back_gap: 2.4, side_gap: 2.2, front_gap: 1.6,
    models: { jukebox: 'models/prop_jukebox.glb', questboard: 'models/prop_questboard.glb', bookshelf: 'models/prop_bookshelf.glb', banner: 'models/prop_banner.glb', crate: 'models/prop_crate.glb', barrel: 'models/prop_barrel.glb', brazier: 'models/prop_brazier.glb', anvil: 'models/prop_anvil.glb', rug: 'models/prop_rug.glb', door: 'models/prop_door.glb' },
    props: [
      { m: 'rug', fit: [0.9, 2.2, 0], y: 0.02 },
      { m: 'bookshelf', u: -0.45, v: 0.02, h: 2.4, rot: 0 },
      { m: 'bookshelf', u: 0.38, v: 0.02, h: 2.4, rot: 0 },
      { m: 'banner', u: 0, v: 0.01, h: 2.6, y: 1.9, rot: 0 },
      { m: 'crate', u: -0.92, v: 0.1, h: 0.8, rot: 20 },
      { m: 'barrel', u: 0.975, v: 0.1, h: 1.0, rot: 0 },
      { m: 'brazier', u: -0.95, v: 0.5, h: 1.0, rot: 0, glow: { color: '#ff9a40', size: 1.1, at: 1.0, opacity: 0.8 } },
      { m: 'door', u: 0.72, v: 0.005, h: 2.4, rot: 0 },   // the entrance: new agents walk in here
      { m: 'anvil', u: 0.95, v: 0.45, h: 0.7, rot: -30 },
      { m: 'jukebox', u: -0.52, v: 0.36, h: 1.5, rot: 0, jukebox: true },   // plays the game's music from here (J)
    ],
  },
};
