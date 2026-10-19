// Typing pose (rest_deg in workshop.json = constant lean toward the desk when idle; deg/hz = typing motion): small repeating rotation offsets on arm bones, layered on top of the real sit animation. Data comes from workshop.json.
import * as THREE from 'three';

export function boneByKey(root, key, child) {
  let node = null;
  root.traverse((o) => { if (!node && o.isBone && o.name.endsWith('_k' + key)) node = o; });
  for (let i = 0; node && i < child; i++) node = node.children.find((c) => c.isBone) || null;
  return node;
}

// Keystroke schedule: bursts of taps (alternating hands, a little irregular) with thinking pauses. O(1) per frame, no allocations.
function makeTypist(seed) {
  let x = (seed * 2654435761 + 1) >>> 0;
  const rnd = () => { x ^= x << 13; x >>>= 0; x ^= x >> 17; x ^= x << 5; x >>>= 0; return x / 4294967296; };
  const tap = [-9, -9];   // time of each hand's latest tap
  let next = 0, left = 0, hand = 0;
  return {
    step(t, typing) {
      if (!typing) { left = 0; next = Math.min(next, t + 0.3); return; }
      if (t < next) return;
      if (left <= 0) { left = 5 + Math.floor(rnd() * 10); next = t + 0.5 + rnd() * 1.5; if (t - tap[0] < 5 && t - tap[1] < 5) return; }
      hand = rnd() < 0.3 ? hand : 1 - hand;
      tap[hand] = t; left--;
      next = t + 0.08 + rnd() * 0.1;
    },
    // 0..1 key press shape: quick down (35 ms), slower release (110 ms)
    pulse(t, h) { const d = t - tap[h]; return d < 0 ? 0 : d < 0.035 ? d / 0.035 : d < 0.145 ? 1 - (d - 0.035) / 0.11 : 0; },
  };
}

export function makeTyping(root, rules, seed = 1) {
  const typist = makeTypist(seed);
  const bones = rules.map((r) => ({ r, node: boneByKey(root, r.key, r.child || 0), last: new THREE.Quaternion(), q: new THREE.Quaternion(), axis: new THREE.Vector3(...r.axis).normalize() }));
  return {
    // call before the animation mixer updates: removes last frame's offset
    undo() { for (const b of bones) if (b.node) b.node.quaternion.multiply(b.last.clone().invert()); },
    // call after the mixer updated: adds this frame's offset (amount 0 = resting)
    apply(t, amount) {
      typist.step(t, amount > 0.5);
      for (const b of bones) {
        if (!b.node) continue;
        const wave = b.r.tap ? typist.pulse(t, b.r.key) : Math.sin((t * b.r.hz + b.r.phase) * Math.PI * 2);   // tap rules follow keystrokes, the rest sway
        const a = THREE.MathUtils.degToRad(b.r.rest_deg || 0) + (amount ? THREE.MathUtils.degToRad(b.r.deg) * wave * amount : 0);
        b.last.setFromAxisAngle(b.axis, a);
        b.node.quaternion.multiply(b.last);
      }
    },
    found: bones.filter((b) => b.node).length,
  };
}
