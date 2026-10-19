// An agent that starts work first walks in from the right side of the room, behind the chairs, to its own chair and sits down (workshop.json scene3d.arrival).
// State machine per agent: walk -> sit (crossfade into the real sit clip) -> done. The caller keeps the agent's screen/typing off until done.
import * as THREE from 'three';

const UP = new THREE.Vector3(0, 1, 0);

// Local-frame path (station group space): out of the door in the back wall (door_u = share of the room half-width) -> along the lane behind the chairs -> the seat itself.
export function makePath(group, slot, half, seat, A) {
  const dx = A.door_u * half, lz = slot.z - A.lane;
  const world = [new THREE.Vector3(dx, 0, lz - 0.3), new THREE.Vector3(dx, 0, lz), new THREE.Vector3(slot.x, 0, lz)];
  group.updateMatrixWorld(true);
  const pts = world.map((p) => group.worldToLocal(p.clone()));
  pts.push(new THREE.Vector3(seat[0], seat[1], seat[2]));
  for (const p of pts.slice(0, -1)) p.y = seat[1];
  return pts;
}

export function startArrival(s, walkClip, sitClip, pts, A, heading) {
  const mixer = s.mixer, node = s.parts.character.scene;
  s.sitAction = mixer.clipAction(sitClip); s.walkAction = mixer.clipAction(walkClip);
  s.sitAction.stop(); s.walkAction.reset().setEffectiveWeight(1).play();
  s.arr = { pts, i: 1, node, A, phase: 'walk', t: 0, heading };
  node.position.copy(pts[0]); face(node, pts[0], pts[1], heading);
}

// The way back out: stand up, walk the arrival path in reverse to the door, then s.gone = true (the caller hides the character; the desk stays so nobody else moves).
export function startDeparture(s, walkClip, pts, A, heading) {
  const node = s.parts.character.scene, back = pts.slice().reverse();
  s.walkAction = s.mixer.clipAction(walkClip); s.walkAction.reset().setEffectiveWeight(1).play();
  if (s.sitAction) s.sitAction.stop();
  s.arr = { pts: back, i: 1, node, A, phase: 'leave', t: 0, heading };
  node.position.copy(back[0]); face(node, back[0], back[1], heading);
}

function face(node, a, b, heading) {
  const dx = b.x - a.x, dz = b.z - a.z;
  if (dx * dx + dz * dz > 1e-6) node.rotation.y = Math.atan2(-dx, -dz) + heading;
}

// returns true while the arrival is still in progress
export function stepArrival(s, dt) {
  const a = s.arr; if (!a) return false;
  const n = a.node;
  if (a.phase === 'walk' || a.phase === 'leave') {
    let move = a.A.speed * dt;
    while (move > 0 && a.i < a.pts.length) {
      const to = a.pts[a.i], d = to.clone().sub(n.position); d.y = 0; const len = d.length();
      if (len <= move) { n.position.copy(to); move -= len; a.i++; if (a.i < a.pts.length) face(n, to, a.pts[a.i], a.heading); }
      else { n.position.addScaledVector(d, move / len); move = 0; }
    }
    if (a.i >= a.pts.length && a.phase === 'leave') { s.walkAction.stop(); s.arr = null; s.gone = true; n.visible = false; return false; }
    if (a.i >= a.pts.length) { a.phase = 'sit'; a.t = 0; n.rotation.y = a.heading; s.sitAction.reset().play(); s.sitAction.crossFadeFrom(s.walkAction, a.A.sit_fade_s, false); }
    return true;
  }
  a.t += dt;
  if (a.t >= a.A.sit_fade_s) { s.walkAction.stop(); s.arr = null; return false; }
  return true;
}
