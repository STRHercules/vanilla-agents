// The jukebox's lights: LED dots all around the tower flashing with the music bands, a cool white glow + light that swells on the bass, and lightning lines that jump out of the rings
// (only while music plays; how far they push follows the beat and the bass). The body is the game's own gnome machine (vanilla_agents/assets/models.json prop_jukebox). Numbers live in tune.js (TUNE.jukebox).
import * as THREE from 'three';

function dotTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d'), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(0.25, 'rgba(255,255,255,0.7)'); r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, 64, 64); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

export function makeJukebox(grp, size, J) {
  const H = size.y, col = new THREE.Color(J.color), tex = dotTexture();
  // widest reach of the model at each height: LEDs and lightning start on the real surface
  const BINS = 40, prof = new Float32Array(BINS);
  grp.updateMatrixWorld(true);
  const inv = new THREE.Matrix4().copy(grp.matrixWorld).invert(), m = new THREE.Matrix4(), v = new THREE.Vector3();
  grp.traverse((o) => {
    if (!o.isMesh) return;
    m.multiplyMatrices(inv, o.matrixWorld); const pa = o.geometry.attributes.position;
    for (let i = 0; i < pa.count; i++) { v.fromBufferAttribute(pa, i).applyMatrix4(m); const b = Math.max(0, Math.min(BINS - 1, Math.floor(v.y / H * BINS))); prof[b] = Math.max(prof[b], Math.hypot(v.x, v.z)); }
  });
  for (let i = 1; i < BINS; i++) if (!prof[i]) prof[i] = prof[i - 1];
  const reach = (y) => prof[Math.max(0, Math.min(BINS - 1, Math.floor(y / H * BINS)))];

  const fx = new THREE.Group(); grp.add(fx);
  // LED dots: one Points object, brightness per dot written each frame
  const leds = []; let k = 0;
  for (const [ri, f] of J.rows.entries()) {
    for (let i = 0; i < J.per_row; i++) {
      const a = (i / J.per_row + (ri % 2) * 0.5 / J.per_row) * Math.PI * 2, y = f * H, r = reach(y) * 1.02;
      leds.push({ x: Math.cos(a) * r, y, z: Math.sin(a) * r, band: (ri * 5 + i * 3) % J.bands, v: 0, row: ri, k: k++ });
    }
  }
  const pos = new Float32Array(leds.length * 3), colr = new Float32Array(leds.length * 3);
  leds.forEach((l, i) => { pos.set([l.x, l.y, l.z], i * 3); });
  const lg = new THREE.BufferGeometry(); lg.setAttribute('position', new THREE.BufferAttribute(pos, 3)); lg.setAttribute('color', new THREE.BufferAttribute(colr, 3));
  const pts = new THREE.Points(lg, new THREE.PointsMaterial({ size: H * J.led_size, map: tex, vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true }));
  pts.frustumCulled = false; fx.add(pts);

  // glow: big soft sprite round the tower + a point light
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color: col, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0 }));
  glow.scale.setScalar(H * J.glow_size); glow.position.set(0, H * 0.5, 0); fx.add(glow);
  const light = new THREE.PointLight(col, 0, H * J.light_range, 1.6); light.position.set(0, H * 0.55, H * 0.35); fx.add(light);

  // lightning arcs
  const P = J.arc_points, arcs = [];
  for (let n = 0; n < J.arcs; n++) {
    const lines = [0, 1, 2].map((i) => {
      const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(P * 3), 3));
      const l = new THREE.Line(g, new THREE.LineBasicMaterial({ color: i === 0 ? '#ffffff' : '#7fb8ff', transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0 }));
      l.frustumCulled = false; l.visible = false; fx.add(l); return l;
    });
    arcs.push({ lines, age: 1, life: 0, a: 0, y: 0, len: 0, up: 0, seed: 0, last: 0 });
  }
  const tip = new THREE.Points(new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(J.arcs * 3), 3)),
    new THREE.PointsMaterial({ size: H * 0.05, map: tex, color: '#ffffff', transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true }));
  tip.frustumCulled = false; fx.add(tip);

  const rnd = (a, b) => a + Math.random() * (b - a);
  function spawn(len, bias) {   // len in model heights; bias 0..1 = how high up the arc starts (bass arcs low, treble high)
    const a = arcs.find((x) => x.age >= x.life); if (!a) return;
    a.age = 0; a.life = rnd(J.arc_life[0], J.arc_life[1]); a.a = rnd(0, Math.PI * 2); a.y = H * (J.rows[0] * 0.6 + bias * (J.rows[J.rows.length - 1] - J.rows[0] * 0.6) + rnd(-0.06, 0.06));
    a.len = len * H; a.up = rnd(-0.35, 0.6); a.seed = Math.random() * 1000; a.last = -1;
  }
  const d = new THREE.Vector3(), s0 = new THREE.Vector3(), s1 = new THREE.Vector3(), pa = new THREE.Vector3(), pb = new THREE.Vector3(), q = new THREE.Vector3();
  function shape(a, reveal) {   // a jagged line from the surface outwards; endpoints fixed, the middle shakes
    const r = reach(a.y) * 0.98, ca = Math.cos(a.a), sa = Math.sin(a.a);
    s0.set(ca * r, a.y, sa * r); d.set(ca, a.up, sa).normalize(); s1.copy(s0).addScaledVector(d, a.len);
    pa.set(-sa, 0, ca); pb.crossVectors(d, pa).normalize(); // two directions across the arc
    for (const [li, L] of a.lines.entries()) {
      const arr = L.geometry.attributes.position.array, off = li ? (li === 1 ? 1 : -1) * H * 0.006 : 0;
      for (let i = 0; i < P; i++) {
        const f = i / (P - 1), env = Math.sin(f * Math.PI);
        q.lerpVectors(s0, s1, f * Math.max(0.05, reveal)); const j = a.len * J.arc_jitter * env * Math.max(0.3, reveal);
        if (li === 0) { a.j = a.j || []; a.j[i] = [rnd(-1, 1) * j, rnd(-1, 1) * j]; }
        const jj = a.j[i] || [0, 0];
        arr[i * 3] = q.x + pa.x * jj[0] + pb.x * jj[1] + off; arr[i * 3 + 1] = q.y + pa.y * jj[0] + pb.y * jj[1]; arr[i * 3 + 2] = q.z + pa.z * jj[0] + pb.z * jj[1] + off;
      }
      L.geometry.attributes.position.needsUpdate = true; L.geometry.setDrawRange(0, P);
    }
    return q;
  }

  let spark = 0;
  return {
    group: grp,
    worldPos(out) { return grp.getWorldPosition(out).add(new THREE.Vector3(0, H * 0.5, 0)); },
    update(t, dt, lv) {
      const on = lv.playing, B = lv.bands;
      // LEDs: each follows its own band (fast up, slow down); a beat makes alternate dots flash
      const att = 1 - Math.exp(-dt * 40), rel = Math.exp(-dt * J.led_decay);
      for (const l of leds) {
        const target = on ? Math.min(1, Math.pow(B[l.band], 1.7) * 1.7 + (lv.beat ? lv.beat * ((l.k + (Math.floor(t * 4) % 2)) % 2 ? 0.9 : 0.35) : 0)) : J.standby * (0.6 + 0.4 * Math.sin(t * 1.5 + l.k));
        l.v = target > l.v ? l.v + (target - l.v) * att : l.v * rel + target * (1 - rel);
        const b = Math.min(1, l.v), i = l.k * 3; colr[i] = col.r * (0.1 + 0.9 * b) + b * 0.3; colr[i + 1] = col.g * (0.1 + 0.9 * b) + b * 0.3; colr[i + 2] = col.b * (0.1 + 0.9 * b) + b * 0.3;
      }
      lg.attributes.color.needsUpdate = true;
      // glow and light swell with the bass and settle between beats
      const goal = on ? J.glow_base + lv.bass * J.glow_gain + lv.beat * 0.25 : J.glow_idle;
      glow.material.opacity += (goal - glow.material.opacity) * (1 - Math.exp(-dt * (goal > glow.material.opacity ? 30 : 6)));
      light.intensity = glow.material.opacity * J.light_gain;
      // lightning: a beat throws a burst (length from the beat's strength and bass), treble flux throws small short ones in between
      if (on) {
        if (lv.beat) { const n = 2 + Math.round(lv.beat * 4); for (let i = 0; i < n; i++) spawn(rnd(0.25, 0.55 + lv.bass * 1.1) * (0.5 + lv.beat), rnd(0, 0.5)); }
        spark += dt * (J.idle_arcs * lv.loud * 2 + lv.flux * J.flux_gain * 60); while (spark > 1) { spark -= 1; spawn(rnd(0.1, 0.28 + lv.treble * 0.4), rnd(0.3, 1)); }
      }
      const tp = tip.geometry.attributes.position.array;
      arcs.forEach((a, ai) => {
        const live = a.age < a.life;
        if (live) a.age += dt;
        const show = live && a.age < a.life;
        for (const L of a.lines) L.visible = show;
        if (!show) { tp[ai * 3 + 1] = -99; return; }
        const u = a.age / a.life, push = Math.min(1, a.age / (a.life * 0.3)), fade = u < 0.3 ? 1 : 1 - (u - 0.3) / 0.7;   // shoots out fast, then dies away
        if (t - a.last > 0.035) { a.last = t; const ease = 1 - Math.pow(1 - push, 3); const e = shape(a, Math.max(0.15, ease)); tp[ai * 3] = e.x; tp[ai * 3 + 1] = e.y; tp[ai * 3 + 2] = e.z; }
        const flick = 0.65 + 0.35 * Math.random();
        a.lines[0].material.opacity = fade * flick; a.lines[1].material.opacity = a.lines[2].material.opacity = fade * 0.55 * flick;
      });
      tip.geometry.attributes.position.needsUpdate = true; tip.material.opacity = 0.9;
    },
  };
}
