// Centre scene: a simple 2D placeholder workshop. One placeholder character per agent at a desk; typing while working, resting while idle.
// Real 3D characters, furniture and live monitor textures come in build steps 6-7.
const FONT = '"Friz Quadrata", serif';

export function initWorkshop(ctx) {
  const canvas = document.getElementById('workshop');
  const g = canvas.getContext('2d');
  const w = ctx.cfg.workshop;
  let state = { agents: [] }, frame = 0, timer = null, boxes = [];

  function size() {
    const r = window.devicePixelRatio || 1;
    canvas.width = Math.round(window.innerWidth * r);
    canvas.height = Math.round(window.innerHeight * r);
    g.setTransform(r, 0, 0, r, 0, 0);
  }

  function room(W, H) {
    const s = w.scene, hy = H * s.horizon;
    g.fillStyle = s.wall; g.fillRect(0, 0, W, hy);
    g.fillStyle = s.floor; g.fillRect(0, hy, W, H - hy);
    g.strokeStyle = s.floor_line; g.lineWidth = 2;
    for (let y = hy + 28, step = 28; y < H; y += step, step += 8) { g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
    const gl = g.createRadialGradient(W / 2, hy * 0.55, 10, W / 2, hy * 0.55, W * 0.35);
    gl.addColorStop(0, s.window_glow + 'aa'); gl.addColorStop(1, s.window_glow + '00');
    g.fillStyle = gl; g.fillRect(0, 0, W, hy);
    g.fillStyle = '#00000055'; g.fillRect(0, hy - 4, W, 8);
  }

  function rect(x, y, ww, hh, fill) { g.fillStyle = fill; g.fillRect(Math.round(x), Math.round(y), ww, hh); }

  function person(x, y, role, typing) {
    const r = w.roles[role] || w.roles.default, sw = w.animation.arm_swing_px;
    rect(x - 30, y - 100, 60, 84, '#2a1c12');                         // chair back
    rect(x - 22, y - 72, 44, 54, r.body);                             // body
    g.fillStyle = r.skin; g.beginPath(); g.arc(x, y - 86, 14, 0, 7); g.fill();
    g.fillStyle = r.hair; g.beginPath(); g.arc(x, y - 90, 14, Math.PI, 0); g.fill();
    g.strokeStyle = r.body; g.lineWidth = 9; g.lineCap = 'round';
    const lift = typing ? (frame % 2 ? sw : -sw) : 0;
    [[-1, lift], [1, -lift]].forEach(([side, d]) => {
      g.beginPath(); g.moveTo(x + side * 22, y - 62); g.lineTo(x + side * 14, y - 22 + d); g.stroke();
    });
    g.fillStyle = r.skin;
    [[-1, lift], [1, -lift]].forEach(([side, d]) => { g.beginPath(); g.arc(x + side * 14, y - 20 + d, 5, 0, 7); g.fill(); });
  }

  function monitor(x, y, text, working) {
    const m = w.monitor;
    rect(x - 6, y - 8, 12, 10, w.desk.edge);                            // stand
    rect(x - m.w / 2 - 4, y - 8 - m.h - 4, m.w + 8, m.h + 8, m.bezel); // brass bezel
    rect(x - m.w / 2, y - 8 - m.h, m.w, m.h, m.glass);
    if (working) { g.fillStyle = m.glow + '33'; g.fillRect(x - m.w / 2, y - 8 - m.h, m.w, m.h); }
    g.fillStyle = m.text; g.font = '8px ' + FONT; g.textAlign = 'left';
    const words = text.split(' '); let line = '', n = 0;
    for (const word of words) {
      if (g.measureText(line + word).width > m.w - 8) { g.fillText(line, x - m.w / 2 + 4, y - 8 - m.h + 11 + n * 11); line = ''; if (++n >= m.lines) break; }
      line += word + ' ';
    }
    if (n < m.lines) g.fillText(line, x - m.w / 2 + 4, y - 8 - m.h + 11 + n * 11);
  }

  function station(a, pos, W, H, selected) {
    const x = pos.x * W, y = pos.y * H, d = w.desk;
    const ax = x - 26, working = a.state === 'working';
    person(ax, y, a.role, working);
    rect(x - d.w / 2, y - 14, d.w, 16, d.top);                          // desk top
    rect(x - d.w / 2, y + 2, d.w, d.h - 16, d.front);
    rect(x - d.w / 2, y - 14, d.w, 2, d.edge);
    rect(ax - 24, y - 12, 48, 6, '#2b2b2b');                            // keyboard
    monitor(x + d.w / 2 - w.monitor.w / 2 - 12, y - 14, working ? a.activity : ctx.t(w.text.screen_idle), working);
    g.textAlign = 'center'; g.font = '13px ' + FONT;
    g.fillStyle = '#000'; g.fillText(a.id, x + 1, y + d.h - 1);
    g.fillStyle = selected ? '#ffd100' : '#e8d9a8'; g.fillText(a.id, x, y + d.h - 2);
    if (selected) { g.strokeStyle = '#ffd100'; g.lineWidth = 1; g.strokeRect(x - d.w / 2 - 6, y - 114, d.w + 12, d.h + 120); }
    boxes.push({ id: a.id, x: x - d.w / 2 - 6, y: y - 114, w: d.w + 12, h: d.h + 120 });
  }

  function draw() {
    const W = window.innerWidth, H = window.innerHeight;
    g.clearRect(0, 0, W, H);
    room(W, H);
    boxes = [];
    const agents = state.agents.slice(0, w.desks.length);
    if (!agents.length) {
      g.fillStyle = '#e8d9a8'; g.font = '16px ' + FONT; g.textAlign = 'center';
      g.fillText(ctx.t(w.text.empty), W / 2, H * 0.5);
    }
    // back row first so nearer desks draw over farther ones
    agents.map((a, i) => ({ a, pos: w.desks[i] })).sort((p, q) => p.pos.y - q.pos.y)
      .forEach(({ a, pos }) => station(a, pos, W, H, a.id === ctx.selected));
    const busy = agents.some((a) => a.state === 'working');
    if (busy && !timer) timer = setInterval(() => { frame++; draw(); }, w.animation.typing_ms);
    if (!busy && timer) { clearInterval(timer); timer = null; }
  }

  function hit(e) { return boxes.slice().reverse().find((b) => e.clientX >= b.x && e.clientX <= b.x + b.w && e.clientY >= b.y && e.clientY <= b.y + b.h); }
  canvas.addEventListener('click', (e) => { const b = hit(e); ctx.select(b ? b.id : null); });
  canvas.addEventListener('mousemove', (e) => { const b = hit(e); canvas.style.cursor = b ? 'pointer' : 'default'; if (b) ctx.tip.show({ dataset: { tip: w.agent_tip } }, e.clientX, e.clientY); else ctx.tip.hide(); });
  window.addEventListener('resize', () => { size(); draw(); });
  size();
  draw();
  return { update(s) { state = s; draw(); }, redraw: draw };
}
