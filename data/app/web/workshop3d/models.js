// Loads the baked .glb models from /assets and turns their materials into the game look: lit but not glossy, point-filtered textures.
import * as THREE from 'three';
import { GLTFLoader } from '/vendor/GLTFLoader.js';

const loader = new GLTFLoader();
const buffers = new Map();

function gameLook(root) {
  root.traverse((o) => {
    if (!o.isMesh) return;
    const old = o.material, m = new THREE.MeshLambertMaterial({ color: old.map ? 0xffffff : old.color, side: THREE.DoubleSide });
    if (old.map) {
      m.map = old.map;
      m.map.magFilter = THREE.NearestFilter; m.map.minFilter = THREE.NearestFilter; m.map.generateMipmaps = false; m.map.needsUpdate = true;
    }
    m.name = old.name || '';         // V-047: an office GLB's "Screen" material is found by name
    if (old.alphaTest) m.alphaTest = old.alphaTest;
    if (old.transparent) { m.transparent = true; m.depthWrite = false; }
    if (old.userData && old.userData.additive) { m.transparent = true; m.depthWrite = false; m.blending = THREE.AdditiveBlending; }      // M2 glows and flames
    o.material = m; o.frustumCulled = !o.isSkinnedMesh;      // static meshes are culled; skinned ones move with their bones
  });
  return root;
}

// Fetch a model into the cache without building it, so the first loadModel (a worker walking in) has no network wait.
export async function preloadModel(path) {
  if (buffers.has(path)) return;
  const r = await fetch('/assets/' + path);
  if (r.ok) buffers.set(path, await r.arrayBuffer());
}

// Each call returns a fresh scene graph (skinned meshes cannot be shared between characters).
export async function loadModel(path) {
  if (!buffers.has(path)) {
    const r = await fetch('/assets/' + path);
    if (!r.ok) throw new Error('model ' + path + ' not found (run: python -m vanilla_agents.assets bake)');
    buffers.set(path, await r.arrayBuffer());
  }
  const gltf = await new Promise((ok, bad) => loader.parse(buffers.get(path).slice(0), '', ok, bad));
  gltf.scene = gameLook(gltf.scene);
  return gltf;
}

export function bounds(obj) { return new THREE.Box3().setFromObject(obj); }
