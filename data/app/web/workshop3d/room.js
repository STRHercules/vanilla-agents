// The room around the desks: textured floor, back wall, two side walls, a rug and real doodad props (bookshelves, banners, crates, barrel, brazier, anvil).
// Everything is placed from the desk layout (slots) so it follows the free-area fit; nothing here changes the camera framing. Numbers live in tune.js (TUNE.room).
import * as THREE from 'three';
import { loadModel, bounds } from './models.js';
import { makeGlow } from './props.js';
import { makeJukebox } from './jukebox.js';
import { TUNE } from './tune.js';

function tex(url, repeatX, repeatY, mip) {
  const t = new THREE.TextureLoader().load(url);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeatX, repeatY); t.colorSpace = THREE.SRGBColorSpace;
  t.magFilter = THREE.LinearFilter; t.minFilter = mip ? THREE.LinearMipmapLinearFilter : THREE.LinearFilter; t.anisotropy = 4;
  return t;
}

export function makeRoom(scene, R) {
  const mat = (name, col) => new THREE.MeshLambertMaterial({ color: col, map: tex('/assets/textures/' + name + '.png', 1, 1, true) });
  const floorM = mat('floor', R.tint.floor), wallM = mat('wall', R.tint.wall);
  const mk = (m, w, h) => { const o = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), m); o.scale.set(w, h, 1); scene.add(o); return o; };
  const floor = mk(floorM, 1, 1), back = mk(wallM, 1, 1), left = mk(wallM.clone(), 1, 1), right = mk(wallM.clone(), 1, 1);
  floor.rotation.x = -Math.PI / 2; left.rotation.y = Math.PI / 2; right.rotation.y = -Math.PI / 2;
  // texture repeat per object (materials are separate so each has its own repeat; the image is shared)
  for (const o of [left, right]) o.material.map = o.material.map.clone();
  const props = [];
  let last = null;

  async function loadProps() {
    for (const p of R.props) {
      const g = (await loadModel(R.models[p.m])).scene, grp = new THREE.Group();
      g.rotation.y = THREE.MathUtils.degToRad(p.rot0 || 0); grp.add(g); grp.updateMatrixWorld(true);
      const b = bounds(grp), size = b.getSize(new THREE.Vector3());
      const k = p.fit ? 1 : p.h / (size.y || 1);
      g.scale.setScalar(k); g.position.set(-(b.min.x + b.max.x) / 2 * k, -b.min.y * k, -(b.min.z + b.max.z) / 2 * k);
      grp.updateMatrixWorld(true);
      const top = bounds(grp).getSize(new THREE.Vector3());
      const o = { p, grp, size: top, glow: null };
      if (p.glow) { o.glow = makeGlow(p.glow); o.glow.sprite.position.set(0, top.y * p.glow.at, 0); grp.add(o.glow.sprite); }
      if (p.jukebox) o.jukebox = makeJukebox(grp, top, TUNE.jukebox);
      scene.add(grp); props.push(o);
    }
    if (last) layout(last);
  }
  loadProps();

  // slots: [{x,z,rot}] desk positions. Room box: width from the outermost desks plus side_gap, back wall behind the rearmost chairs.
  function layout(slots) {
    last = slots; if (!slots.length) return;
    const xs = slots.map((s) => s.x), zs = slots.map((s) => s.z), half = Math.max(...xs.map(Math.abs)) + R.side_gap;
    const wz = Math.min(...zs) - R.back_gap, zf = Math.max(...zs) + R.front_gap, depth = zf - wz, H = R.height;
    floor.scale.set(60, 60, 1); floor.position.set(0, 0, 0);
    floorM.map.repeat.set(60 / R.tile.floor, 60 / R.tile.floor);
    back.scale.set(half * 2, H, 1); back.position.set(0, H / 2, wz); back.material.map.repeat.set(half * 2 / R.tile.wall, H / R.tile.wall);
    for (const [o, sx] of [[left, -1], [right, 1]]) {
      o.scale.set(depth, H, 1); o.position.set(sx * half, H / 2, wz + depth / 2); o.material.map.repeat.set(depth / R.tile.wall, H / R.tile.wall);
    }
    for (const o of props) {
      const p = o.p;
      o.grp.position.set(p.u * half, p.y || 0, wz + p.v * depth);
      if (p.fit) { // rug: stretch to the desk arc, long side along x
        const w = (Math.max(...xs) - Math.min(...xs) + p.fit[0]), d = (Math.max(...zs) - Math.min(...zs) + p.fit[1]);
        o.grp.scale.set(w / (o.size.x || 1), 1, d / (o.size.z || 1));
        o.grp.position.set((Math.max(...xs) + Math.min(...xs)) / 2, p.y || 0.02, (Math.max(...zs) + Math.min(...zs)) / 2 - p.fit[2]);
      } else o.grp.rotation.y = THREE.MathUtils.degToRad(p.rot || 0);
    }
  }
  return { layout, jukebox: () => (props.find((o) => o.jukebox) || {}).jukebox || null, update(t) { for (const o of props) if (o.glow) o.glow.flicker(t); } };
}
