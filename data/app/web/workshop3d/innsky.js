// V-046: the inn's time-of-day light from the game's own lighting tables (places/index.json zone "lighting", baked by
// vanilla_agents/assets/lighting.py) and a smooth blend of the three times by the clock. No three.js here: plain hex maths.
// A TIMES entry is [sky background, hemisphere top, hemisphere bottom, hemisphere intensity, sun colour, sun intensity, fog colour?].
const rgb = (h) => { const n = parseInt(String(h).slice(1), 16) || 0; return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255]; };
const hex = (c) => '#' + c.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, '0')).join('');
const mix = (a, b, f) => a.map((v, i) => v + (b[i] - v) * f);
const lum = (c) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const WHITE = [1, 1, 1], WOOD = [0.42, 0.35, 0.28], NIGHT = [0.04, 0.055, 0.1];
const norm = (c) => { const m = Math.max(...c); return m > 0.02 ? c.map((v) => v / m) : WHITE; };     // the hue at full strength
// keep a colour's brightness between lo and hi (indoors nothing outside may glare or go black)
const within = (c, lo, hi) => {
  const l = lum(c);
  if (l < lo) return l < 0.01 ? mix(NIGHT, WHITE, lo) : c.map((v) => v * lo / l);
  return l > hi ? c.map((v) => v * hi / l) : c;
};
// the indoor look per time (the hand-tuned default: hemisphere 1.9 / 1.5 / 1.05, sun 0.6 / 0.5 / 0.25), scaled a little by how bright the game's light is
const BASE = { day: [1.9, 0.62, 0.25], dusk: [1.5, 0.5, 0.15], night: [1.05, 0.26, 0.05] };

export function gameEntry(g, t) {
  const amb = rgb(g.ambient), dif = rgb(g.diffuse), skyc = rgb(g.sky), [k, s, lo] = BASE[t];
  const b = 0.6 * lum(amb) + 0.4 * lum(dif);                                          // ~0.5 at an Elwynn noon, ~0.3 at midnight
  const top = mix(norm(mix(amb, dif, 0.5)), WHITE, 0.5);                              // the game's light, lifted so the room stays readable
  const bottom = mix(norm(amb).map((v) => v * 0.45), WOOD, 0.5);                      // the floor bounce: ambient, darkened, toward the wood
  const sun = mix(norm(dif), g.sun ? norm(rgb(g.sun)) : WHITE, 0.5);              // direct light, softened by the sun's own colour
  return [hex(within(skyc, lo, 0.72)), hex(top), hex(bottom), +(k * clamp(0.6 + 0.8 * b, 0.85, 1.12)).toFixed(3),
    hex(sun), +(s * clamp(lum(dif) / 0.55, 0.5, 1.2)).toFixed(3), hex(within(rgb(g.fog || g.sky), lo * 0.6, 0.6))];
}

// {day, dusk, night, far} from a zone's baked lighting, or null when it is missing or incomplete (then the hand presets apply)
export function gameTimes(L) {
  if (!L || typeof L !== 'object') return null;
  const out = {};
  for (const t of ['day', 'dusk', 'night']) {
    const g = L[t]; if (!g || !/^#[0-9a-f]{6}$/i.test(g.ambient) || !/^#[0-9a-f]{6}$/i.test(g.diffuse) || !/^#[0-9a-f]{6}$/i.test(g.sky)) return null;
    out[t] = gameEntry(g, t);
  }
  out.far = clamp((L.day.fog_end || 400) * 0.25, 90, 180);                           // the game's fog distance (yards), squeezed: far away only
  return out;
}

// the clock: night until 5, dawn to day by 8, day until 17, dusk at 19:30, night again by 22
const ANCHORS = [[0, 'night'], [5, 'night'], [8, 'day'], [17, 'day'], [19.5, 'dusk'], [22, 'night'], [24, 'night']];
const lerpEntry = (a, b, f) => a.map((v, i) => (typeof v === 'number' ? v + ((b[i] ?? v) - v) * f : v && b[i] ? hex(mix(rgb(v), rgb(b[i]), f)) : v));
export function blendAt(TIMES, hour) {
  const h = ((hour % 24) + 24) % 24;
  let i = 0; while (i < ANCHORS.length - 2 && ANCHORS[i + 1][0] <= h) i++;
  const [h0, n0] = ANCHORS[i], [h1, n1] = ANCHORS[i + 1], f = h1 > h0 ? (h - h0) / (h1 - h0) : 0;
  return { name: f < 0.5 ? n0 : n1, entry: n0 === n1 ? TIMES[n0] : lerpEntry(TIMES[n0], TIMES[n1], f) };
}
