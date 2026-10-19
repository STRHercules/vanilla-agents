// Curved monitor: brass bezel, a live canvas texture on the curved glass (current work text), additive glow sprite (no bloom). Sizes and colours from workshop.json.
import * as THREE from 'three';

function glowTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d'), r = g.createRadialGradient(32, 32, 2, 32, 32, 32);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}
const GLOW = glowTexture();

// Draws the screen: dark teal background, brass monospace text. lines = [{text, dim}], head = title line.
function screenPainter(cfg, canvas) {
  const g = canvas.getContext('2d'), W = canvas.width, H = canvas.height, fs = Math.round(H / 11), pad = fs * 0.7, cols = Math.floor((W - pad * 2) / (fs * 0.6));
  return (head, lines, cursor) => {
    g.fillStyle = cfg.glass; g.fillRect(0, 0, W, H);
    g.fillStyle = 'rgba(78,208,192,0.10)'; g.fillRect(0, 0, W, fs * 1.5);
    g.font = 'bold ' + fs + 'px Consolas, "Courier New", monospace'; g.textBaseline = 'top';
    g.fillStyle = cfg.text; g.fillText(head.slice(0, cols), pad, fs * 0.3);
    const rows = Math.floor((H - fs * 2) / (fs * 1.15));
    lines.slice(-rows).forEach((l, i) => {
      g.globalAlpha = l.dim ? 0.55 : 1; g.fillStyle = cfg.text;
      g.fillText(l.text.length > cols ? l.text.slice(0, cols - 1) + '~' : l.text, pad, fs * 1.9 + i * fs * 1.15);
    });
    g.globalAlpha = 1;
    if (cursor) { g.fillStyle = cfg.text; g.fillRect(pad, H - fs * 1.2, fs * 0.55, fs * 0.9); }
  };
}

export function makeMonitor(cfg) {
  const out = new THREE.Group(), g = new THREE.Group(), R = cfg.radius, arc = cfg.arc_deg * Math.PI / 180, start = -arc / 2 + Math.PI;
  const brass = new THREE.MeshLambertMaterial({ color: cfg.bezel, side: THREE.DoubleSide });
  const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = Math.round(512 * cfg.h / (R * arc));
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping; tex.repeat.x = -1; tex.offset.x = 1; // cylinder u runs the other way when seen from inside
  tex.anisotropy = 4; tex.colorSpace = THREE.SRGBColorSpace;
  const glass = new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide, fog: false });
  const seg = (h, y, r, mat) => { const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 24, 1, true, start, arc), mat); m.position.y = y; g.add(m); return m; };
  seg(cfg.h, 0, R, glass);
  seg(cfg.bezel_w, cfg.h / 2 + cfg.bezel_w / 2, R + 0.005, brass);
  seg(cfg.bezel_w, -cfg.h / 2 - cfg.bezel_w / 2, R + 0.005, brass);
  for (const s of [-1, 1]) {
    const a = Math.PI + s * arc / 2, side = new THREE.Mesh(new THREE.BoxGeometry(cfg.bezel_w, cfg.h + cfg.bezel_w * 2, cfg.bezel_w), brass);
    side.position.set(Math.sin(a) * R, 0, Math.cos(a) * R); side.rotation.y = a; g.add(side);
  }
  g.position.z = R; out.add(g); // screen middle sits at the group origin, curving toward +z (the viewer)
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOW, color: cfg.glow, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, fog: false }));
  glow.scale.set(cfg.h * 3.2, cfg.h * 2.2, 1); glow.position.set(0, 0, 0.08); out.add(glow);
  const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.06, cfg.stand_h, 8), brass);
  stand.position.set(0, -cfg.h / 2 - cfg.stand_h / 2 - cfg.bezel_w, -0.06); out.add(stand);
  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.22, 0.03, 12), brass);
  base.position.set(0, stand.position.y - cfg.stand_h / 2, -0.02); out.add(base);
  const paint = screenPainter(cfg, canvas);
  return { group: out, glow, tex, width: R * arc, paint: (head, lines, cursor) => { paint(head, lines, cursor); tex.needsUpdate = true; } };
}
