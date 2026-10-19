// Which baked worker (hero) an agent is, its WoW name, and a head-and-shoulders portrait rendered from its own 3D model.
// Same pick as the inn (FNV hash of the agent id over models/workers/manifest.json), so the panels and the inn agree.
import * as THREE from 'three';
import { GLTFLoader } from '/vendor/GLTFLoader.js';

const hash = (s) => { let h = 2166136261; for (const ch of String(s)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0; return h; };
let roster = null, rosterP = null;
const pics = new Map(), waiting = new Map();
let renderer = null, queue = Promise.resolve();
const history = new Map();      // agent id -> last few activity lines (task timeline "past steps")

export function loadRoster() {
  if (!rosterP) rosterP = fetch('/assets/models/workers/manifest.json').then((r) => (r.ok ? r.json() : [])).catch(() => []).then((l) => { roster = l; window.dispatchEvent(new Event('workers-ready')); return l; });
  return rosterP;
}

export function workerFor(id) {
  if (!roster || !roster.length || !id) return null;
  return roster[hash(id) % roster.length];
}

export function nameFor(id) {
  if (window.__workerName) { const n = window.__workerName(id); if (n) return n; }
  const w = workerFor(id);
  return w ? w.name : id;
}

// last activity lines of each agent, oldest first (kept here so every panel sees the same history)
export function noteActivity(agents) {
  for (const a of agents || []) {
    if (!a.activity || a.state !== 'working') continue;
    const h = history.get(a.id) || [];
    if (h[h.length - 1] !== a.activity) { h.push(a.activity); if (h.length > 6) h.shift(); history.set(a.id, h); }
  }
}
export const historyOf = (id) => history.get(id) || [];

function render(file) {
  if (!renderer) {
    const c = document.createElement('canvas'); c.width = c.height = 160;
    renderer = new THREE.WebGLRenderer({ canvas: c, alpha: true, antialias: true, preserveDrawingBuffer: true });
    renderer.setClearColor(0x000000, 0);
  }
  return new GLTFLoader().loadAsync('/assets/' + file).then((g) => {
    const scene = new THREE.Scene();
    scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const key = new THREE.DirectionalLight(0xfff0d8, 2.2); key.position.set(1.5, 2, -3); scene.add(key);
    const o = g.scene; scene.add(o); o.updateMatrixWorld(true);
    const b = new THREE.Box3().setFromObject(o), size = b.getSize(new THREE.Vector3());
    const head = new THREE.Vector3((b.min.x + b.max.x) / 2, b.max.y - size.y * 0.13, (b.min.z + b.max.z) / 2);
    const cam = new THREE.PerspectiveCamera(30, 1, 0.01, 50);
    const d = size.y * 0.62;
    cam.position.set(head.x, head.y + size.y * 0.02, head.z - d);        // the baked models face -Z
    cam.lookAt(head);
    renderer.render(scene, cam);
    const url = renderer.domElement.toDataURL('image/png');
    o.traverse((n) => { if (n.geometry) n.geometry.dispose(); if (n.material) [].concat(n.material).forEach((m) => { if (m.map) m.map.dispose(); m.dispose(); }); });
    return url;
  });
}

// Promise of a data: URL (or null when there is no worker art); one render at a time, cached per model file
export async function portraitFor(id) {
  await loadRoster();
  const w = workerFor(id);
  if (!w) return null;
  if (pics.has(w.file)) return pics.get(w.file);
  if (!waiting.has(w.file)) {
    const p = (queue = queue.then(() => render(w.file)).catch(() => null)).then((u) => { pics.set(w.file, u); return u; });
    waiting.set(w.file, p);
  }
  return waiting.get(w.file);
}

// fill an <img> with the agent's portrait when it is ready (keeps the fallback src until then)
export function setPortrait(img, id) {
  img.dataset.agent = id;
  const cached = (() => { const w = workerFor(id); return w && pics.get(w.file); })();
  if (cached) { img.src = cached; return; }
  portraitFor(id).then((u) => { if (u && img.dataset.agent === id) img.src = u; });
}
