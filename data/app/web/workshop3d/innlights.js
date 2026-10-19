// V-043: warm lights at the building's own candles, lanterns, chandeliers and braziers (doodads named in workshop.json inn.lights.names),
// plus a soft window light from the side away from the sun in the zone's own light colour. Point lights cost time on every pixel, so only a
// few exist (inn.lights.max per render quality); every few seconds they move to the light sources nearest you (fading out and in), and when
// the inn runs slow they are the first thing switched off. No shadows. Flicker is a sum of slow sines per light; Reduce motion stops it.
import * as THREE from 'three';

export function makeInnLights(scene, L, opts = {}) {
  const cfg = L || { max: [0, 0, 0, 0] }, reduced = opts.reduced || (() => false), quality = opts.quality || (() => 2);
  const fill = new THREE.DirectionalLight('#ffffff', 0); fill.position.set(9, 10, -7); scene.add(fill);
  const pool = []; let spots = [], cap = Infinity, timeK = 1, pickAt = -99;

  // candles closer than `merge` metres become one light (a chandelier is one light, not twelve); a cluster's weight = how many it holds
  function setSpots(list) {
    const m = cfg.merge || 1.5, out = [];
    for (const p of list) {
      const c = out.find((q) => q.pos.distanceTo(p) < m);
      if (c) { c.pos.multiplyScalar(c.w).add(p).multiplyScalar(1 / (c.w + 1)); c.w++; } else out.push({ pos: p.clone(), w: 1 });
    }
    spots = out; resize(); pickAt = -99;
  }
  const budget = () => Math.max(0, Math.min((cfg.max || [])[quality()] ?? 0, cap, spots.length));
  function resize() {        // changing the number of lights rebuilds the shaders once: only on a quality change or when shedding lights
    const n = budget();
    while (pool.length < n) {
      // decay 1 (not the physical 2): a soft pool of light that reaches the floor under a high lantern without burning the wall next to a candle
      const light = new THREE.PointLight(cfg.color || '#ffa04a', 0, cfg.distance || 7, 1); light.castShadow = false;
      scene.add(light); pool.push({ light, spot: null, next: null, k: 0, ph: pool.length * 2.399 });
    }
    while (pool.length > n) { const e = pool.pop(); scene.remove(e.light); e.light.dispose(); }
  }
  // the n sources nearest `from` (a bigger cluster counts as a little nearer); lights already on a chosen source stay put
  function pick(from) {
    if (!pool.length) return;
    const want = spots.map((s) => [s.pos.distanceTo(from) / (1 + 0.15 * Math.log2(s.w)), s]).sort((a, b) => a[0] - b[0]).slice(0, pool.length).map((x) => x[1]);
    const free = want.filter((s) => !pool.some((e) => (e.next || e.spot) === s));
    for (const e of pool) if (!want.includes(e.next || e.spot)) e.next = free.shift() || null;
  }
  function update(t, dt, from) {
    if (!pool.length) return;
    if (t - pickAt > (cfg.pick_s || 3)) { pickAt = t; pick(from); }
    const F = reduced() ? 0 : (cfg.flicker || 0), base = (cfg.intensity || 2) * timeK;
    for (const e of pool) {
      if (e.next !== null && e.next !== e.spot) {           // moving: fade out, jump, fade in
        e.k = Math.max(0, e.k - dt * 3);
        if (e.k === 0) { e.spot = e.next; e.next = null; if (e.spot) e.light.position.copy(e.spot.pos); }
      } else if (e.spot) e.k = Math.min(1, e.k + dt * 2);
      if (e.next === e.spot) e.next = null;
      const f = F ? 1 + F * (0.55 * Math.sin(t * 7.1 + e.ph) + 0.3 * Math.sin(t * 12.7 + e.ph * 2.3) + 0.15 * Math.sin(t * 23.3 + e.ph * 3.1)) : 1;
      e.light.intensity = e.spot ? base * Math.min(1.6, 1 + 0.12 * (e.spot.w - 1)) * e.k * f : 0;
    }
  }
  // time of day: candles count for more at night; the window light takes the zone's colour for that time
  function setTime(name, color) {
    timeK = (cfg.time || {})[name] ?? 1;
    fill.color.set(color || '#ffffff'); fill.intensity = (cfg.fill || {})[name] ?? 0;
  }
  // too slow: halve the lights (then none); true when something was switched off
  function shed() { const n = pool.length; if (!n) return false; cap = Math.floor(n / 2); resize(); return true; }
  function reset() { cap = Infinity; resize(); pickAt = -99; }       // a new quality chosen: the full budget for it again
  return { setSpots, update, setTime, shed, reset, resize, fill, get lights() { return pool.map((e) => ({ pos: e.light.position.clone(), intensity: e.light.intensity, w: e.spot ? e.spot.w : 0 })); },
    get spots() { return spots.map((s) => ({ pos: s.pos.clone(), w: s.w })); }, get count() { return pool.length; } };
}
