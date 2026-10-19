// Things an agent brings and uses: a sack carried in the right hand (set on the floor by the chair when it sits), a mug on the desk with rising steam.
// Models are baked game doodads (vanilla_agents/assets/models.json: prop_sack, prop_mug); sizes/positions live in tune.js (TUNE.bag, TUNE.mug).
import * as THREE from 'three';
import { loadModel, bounds } from './models.js';
import { boneByKey } from './typing.js';

// Model scaled so its longest side is h, origin at the middle of its base.
export async function loadProp(path, h) {
  const g = (await loadModel(path)).scene, grp = new THREE.Group();
  grp.add(g); grp.updateMatrixWorld(true);
  const b = bounds(grp), sz = b.getSize(new THREE.Vector3()), k = h / (Math.max(sz.x, sz.y, sz.z) || 1);
  g.scale.setScalar(k); g.position.set(-(b.min.x + b.max.x) / 2 * k, -b.min.y * k, -(b.min.z + b.max.z) / 2 * k);
  return grp;
}

const STEAM = (() => {
  const c = document.createElement('canvas'); c.width = c.height = 32; const g = c.getContext('2d'), r = g.createRadialGradient(16, 16, 1, 16, 16, 16);
  r.addColorStop(0, 'rgba(255,255,255,0.55)'); r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, 32, 32); return new THREE.CanvasTexture(c);
})();

// A few soft puffs that rise, widen and fade in a loop (one shared texture, 5 sprites per mug).
export function makeSteam(n, rise) {
  const grp = new THREE.Group(), puffs = [];
  for (let i = 0; i < n; i++) {
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: STEAM, transparent: true, depthWrite: false, fog: false, opacity: 0 }));
    grp.add(s); puffs.push({ s, off: i / n, sway: Math.random() * 6 });
  }
  return { group: grp, update(t) {
    for (const p of puffs) {
      const u = (t * 0.45 + p.off) % 1;
      p.s.position.set(Math.sin(t * 1.3 + p.sway) * 0.015 * u, u * rise, 0);
      p.s.scale.setScalar(0.04 + u * 0.09); p.s.material.opacity = Math.sin(u * Math.PI) * 0.5;
    }
  } };
}

// Hang the bag on the right-hand bone (hand offset in bone space); returns false when the model has no such bone.
export function holdBag(s, bag, B) {
  const hand = boneByKey(s.parts.character.scene, B.bone.key, B.bone.child);
  if (!hand) return false;
  hand.updateWorldMatrix(true, false);
  const k = 1 / (hand.getWorldScale(new THREE.Vector3()).x || 1);   // bone space can be scaled (model units): keep the sack at its real size
  bag.scale.setScalar(k); bag.position.set(...B.hand).multiplyScalar(k); hand.add(bag); s.bag = bag; return true;
}

// Let go: the bag keeps its place in the world, then settles onto the floor spot beside the chair.
export function dropBag(s, group, B) {
  const bag = s.bag; if (!bag) return;
  group.attach(bag);
  s.drop = { t: 0, from: bag.position.clone(), to: new THREE.Vector3(...B.floor), rot: bag.rotation.y };
}

export function stepBag(s, dt, B) {
  const d = s.drop; if (!d) return;
  d.t = Math.min(1, d.t + dt / B.drop_s);
  const e = d.t * d.t, bag = s.bag;    // accelerates like a falling thing
  bag.position.lerpVectors(d.from, d.to, e); bag.rotation.set(0, d.rot * (1 - e), 0); bag.scale.setScalar(1);
  if (d.t >= 1) s.drop = null;
}

// A legendary weapon standing against the worker's desk (station-frame position and rotation from TUNE.weapon): the hard, risky jobs get one.
export async function standWeapon(s, path, W) {
  const w = await loadProp(path, W.h);
  w.position.set(...W.pos); w.rotation.set(...W.rot);
  s.group.add(w); s.weapon = w;
}

// Helm and shoulders baked into the worker manifest (the worker manifest `attach`, empty until helm/shoulder baking exists): each is a static game model hung on the bone the model's own attachment point names, at that point's offset in bone space.
export async function wearGear(s, items) {
  const root = s.parts.character.scene;
  for (const it of items || []) {
    let bone = null; root.traverse((o) => { if (!bone && o.isBone && o.name.startsWith('b' + it.bone + '_')) bone = o; });
    if (!bone) continue;
    const g = (await loadModel(it.file)).scene;
    g.position.set(...it.pos); bone.add(g);
  }
}
