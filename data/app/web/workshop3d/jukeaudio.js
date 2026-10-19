// Jukebox sound: the game's own music (baked by `python -m vanilla_agents.assets bake music` into /assets/sound/music) playing from the jukebox's spot in the room.
// Web Audio: element -> analyser (for the lights and lightning) -> gain -> 3D panner at the jukebox -> speakers. The listener is the camera, so the music pans and fades as the view moves.
// Browsers only start audio after a click or key press: nothing plays until the first toggle().

export function makeJukeAudio(J) {
  let words = [], all = null;   // words: the building's music (group / title matches); empty = every track
  let ctx = null, el = null, gain = null, panner = null, analyser = null, bins = null, tracks = null, order = [], at = -1, playing = false, onTrack = () => {};
  const level = { playing: false, bands: new Float32Array(J.bands), bass: 0, mid: 0, treble: 0, loud: 0, beat: 0, flux: 0 };
  let prev = null, avgBass = 0.1, avgFlux = 0.002, lastBeat = 0;

  async function list() {
    if (all === null) all = await fetch('/assets/sound/music/manifest.json').then((r) => (r.ok ? r.json() : [])).catch(() => []);
    if (tracks === null) {
      const fit = all.filter((t) => words.some((w) => ((t.group || '') + ' ' + t.title).toLowerCase().includes(w.toLowerCase())));
      tracks = fit.length ? fit : all;
    }
    return tracks;
  }
  const shuffle = () => { order = tracks.map((_, i) => i); for (let i = order.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; } };

  function boot() {
    if (ctx) return;
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    el = new Audio(); el.crossOrigin = 'anonymous'; el.preload = 'auto';
    el.addEventListener('ended', () => next());
    const src = ctx.createMediaElementSource(el);
    analyser = ctx.createAnalyser(); analyser.fftSize = J.fft; analyser.smoothingTimeConstant = J.smoothing; bins = new Uint8Array(analyser.frequencyBinCount);
    gain = ctx.createGain(); gain.gain.value = J.volume;
    panner = ctx.createPanner(); panner.panningModel = 'HRTF'; panner.distanceModel = 'inverse';
    panner.refDistance = J.ref_distance; panner.rolloffFactor = J.rolloff; panner.maxDistance = 200;
    src.connect(analyser); analyser.connect(gain); gain.connect(panner); panner.connect(ctx.destination);
  }

  async function load(i) {
    await list(); if (!tracks.length) return false;
    if (!order.length) shuffle();
    at = ((i % order.length) + order.length) % order.length;
    const t = tracks[order[at]];
    el.src = '/assets/' + t.file; onTrack(t);
    try { await el.play(); playing = true; return true; } catch { playing = false; return false; }
  }

  async function toggle() {
    boot();
    if (ctx.state === 'suspended') await ctx.resume();
    if (playing) { el.pause(); playing = false; return false; }
    if (el.src && at >= 0) { try { await el.play(); playing = true; return true; } catch { return false; } }
    return load(0);
  }
  async function next() { boot(); if (ctx.state === 'suspended') await ctx.resume(); return load(at + 1); }

  // each frame: where the jukebox is (world), where the listener is, and the music's bands / beat since the last frame
  function update(t, camera, at3) {
    level.playing = playing;
    if (!ctx) return level;
    const L = ctx.listener, p = camera.position, f = camera.getWorldDirection(camera.position.clone().set(0, 0, 0)), u = camera.up;
    if (L.positionX) {
      L.positionX.value = p.x; L.positionY.value = p.y; L.positionZ.value = p.z;
      L.forwardX.value = f.x; L.forwardY.value = f.y; L.forwardZ.value = f.z; L.upX.value = u.x; L.upY.value = u.y; L.upZ.value = u.z;
    }
    panner.positionX.value = at3.x; panner.positionY.value = at3.y; panner.positionZ.value = at3.z;
    if (!playing) { level.bass = level.mid = level.treble = level.beat = level.flux = 0; level.bands.fill(0); return level; }
    analyser.getByteFrequencyData(bins);
    const n = bins.length, B = level.bands.length, edge = (k) => Math.min(n, Math.round(Math.pow(k / B, 1.7) * n * 0.75) + 1);   // bands widen with pitch, like hearing
    let flux = 0;
    for (let b = 0; b < B; b++) {
      let s = 0, a = edge(b), z = Math.max(edge(b + 1), a + 1); for (let i = a; i < z; i++) s += bins[i];
      const v = s / (z - a) / 255; flux += prev ? Math.max(0, v - prev[b]) : 0; level.bands[b] = v;
    }
    prev = prev || new Float32Array(B); prev.set(level.bands);
    const avg = (a, z) => { let s = 0; for (let i = a; i < z; i++) s += level.bands[i]; return s / (z - a); };
    level.bass = avg(0, 3); level.mid = avg(3, Math.ceil(B * 0.6)); level.treble = avg(Math.ceil(B * 0.6), B);
    level.flux = flux / B; level.loud = avg(0, B);
    // a "beat" is any clear onset: bass well above its recent average (drums) or a spike of new energy across the bands (plucks, horns, chords) - most game music has no drums
    avgBass += (level.bass - avgBass) * 0.04; avgFlux += (level.flux - avgFlux) * 0.05;
    level.beat = 0;
    const drum = level.bass > avgBass * J.beat_ratio && level.bass > J.beat_min, onset = level.flux > avgFlux * J.onset_ratio && level.flux > J.onset_min;
    if ((drum || onset) && t - lastBeat > J.beat_gap) { level.beat = Math.min(1, (drum ? (level.bass - avgBass) * 3 : 0) + (onset ? (level.flux - avgFlux) * 40 : 0) + 0.35); lastBeat = t; }
    return level;
  }

  function setWords(list) { words = list || []; tracks = null; order = []; at = -1; }
  return { toggle, next, update, level, setWords, setVolume(v) { if (gain) gain.gain.value = v; }, get playing() { return playing; }, set onTrack(f) { onTrack = f; } };
}
