// Small low-poly props built from plain boxes/sprites: keyboard (wood + brass canvas texture), candle (wax, additive flame sprite, tiny light), dust motes.
import * as THREE from 'three';

function keyboardTexture() {
  const c = document.createElement('canvas'); c.width = 128; c.height = 48; const g = c.getContext('2d');
  g.fillStyle = '#4a2c14'; g.fillRect(0, 0, 128, 48);
  g.fillStyle = '#6b4222'; for (let i = 0; i < 48; i += 6) g.fillRect(0, i, 128, 2); // wood grain
  g.fillStyle = '#b08a3e'; // brass keys
  for (let r = 0; r < 4; r++) for (let k = 0; k < 14; k++) g.fillRect(6 + k * 8.4, 5 + r * 9.5, 6.4, 7.4);
  g.fillRect(30, 43, 60, 3);
  const t = new THREE.CanvasTexture(c); t.magFilter = THREE.NearestFilter; t.colorSpace = THREE.SRGBColorSpace; return t;
}
const KB = keyboardTexture();

export function makeKeyboard(size) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(size[0], size[1], size[2]), new THREE.MeshLambertMaterial({ map: KB }));
  return m;
}

function flameTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 32; const g = c.getContext('2d'), r = g.createRadialGradient(16, 16, 1, 16, 16, 16);
  r.addColorStop(0, 'rgba(255,230,160,1)'); r.addColorStop(0.4, 'rgba(255,150,50,0.6)'); r.addColorStop(1, 'rgba(255,100,20,0)');
  g.fillStyle = r; g.fillRect(0, 0, 32, 32); return new THREE.CanvasTexture(c);
}
const FLAME = flameTexture();

export function makeCandle(cfg) {
  const grp = new THREE.Group();
  const wax = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.03, cfg.h, 6), new THREE.MeshLambertMaterial({ color: '#e8d8b0' }));
  wax.position.y = cfg.h / 2; grp.add(wax);
  const flame = new THREE.Sprite(new THREE.SpriteMaterial({ map: FLAME, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, fog: false }));
  flame.scale.set(0.16, 0.2, 1); flame.position.y = cfg.h + 0.05; grp.add(flame);
  const light = new THREE.PointLight('#ffa850', cfg.intensity, cfg.range, 1.5); light.position.y = cfg.h + 0.1; grp.add(light);
  return { group: grp, flame, light };
}

// One Points object for the whole room, drifting slowly; a box (min/max) around the key light.
export function makeDust(cfg) {
  const n = cfg.count, pos = new Float32Array(n * 3), seed = new Float32Array(n * 3);
  for (let i = 0; i < n * 3; i++) seed[i] = Math.random();
  const c = document.createElement('canvas'); c.width = c.height = 16; const g = c.getContext('2d'), r = g.createRadialGradient(8, 8, 0, 8, 8, 8);
  r.addColorStop(0, 'rgba(255,230,190,1)'); r.addColorStop(1, 'rgba(255,230,190,0)'); g.fillStyle = r; g.fillRect(0, 0, 16, 16);
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const pts = new THREE.Points(geo, new THREE.PointsMaterial({ map: new THREE.CanvasTexture(c), size: cfg.size, color: cfg.color, transparent: true, opacity: cfg.opacity, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }));
  pts.frustumCulled = false;
  const [lo, hi] = [cfg.min, cfg.max];
  return { points: pts, update(t) {
    for (let i = 0; i < n; i++) for (let a = 0; a < 3; a++) {
      const s = seed[i * 3 + a], sp = cfg.speed * (0.4 + s);
      const u = (s + t * sp * (a === 1 ? 0.35 : 1) / (hi[a] - lo[a]) + 0.05 * Math.sin(t * 0.3 + i)) % 1;
      pos[i * 3 + a] = lo[a] + (u < 0 ? u + 1 : u) * (hi[a] - lo[a]);
    }
    geo.attributes.position.needsUpdate = true;
  } };
}

// Soft additive glow sprite for a brazier or lantern (no bloom, no real light): size in metres, flickers a little.
export function makeGlow(cfg) {
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: FLAME, color: cfg.color, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, fog: false, opacity: cfg.opacity }));
  sprite.scale.set(cfg.size, cfg.size * 1.3, 1);
  return { sprite, flicker(t) { const f = 1 + 0.08 * Math.sin(t * 9 + cfg.size * 7) * Math.sin(t * 3.1); sprite.scale.set(cfg.size * f, cfg.size * 1.3 * f, 1); } };
}
