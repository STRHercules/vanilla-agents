// The loading screen: the game's own Eastern Kingdoms loading art, its loading bar and a game tip, shown until the inn is ready,
// so nothing has to load (models, paths, heroes, shaders) once you are playing. Steps report a label and 0..1 progress.
const box = () => document.getElementById('loading');
let tipsTimer = null, shown = 0, target = 0, raf = 0;

function animate() {          // the bar glides toward the newest progress instead of jumping
  shown += (target - shown) * 0.12;
  const fill = document.getElementById('loading-fill');
  if (fill) fill.style.width = (shown * 100).toFixed(1) + '%';
  raf = target > shown + 0.001 ? requestAnimationFrame(animate) : 0;
}

export const loading = {
  step(label, pct) {
    const l = document.getElementById('loading-label');
    if (l && label) l.textContent = label;
    target = Math.max(target, Math.min(1, pct));
    if (!raf) raf = requestAnimationFrame(animate);
  },
  async tips() {
    const t = document.getElementById('loading-tip');
    let list = [];
    try { list = await fetch('/assets/ui/tips.json').then((r) => (r.ok ? r.json() : [])); } catch (e) { list = []; }
    if (!t || !list.length) return;
    const show = () => { t.innerHTML = ''; const s = list[Math.floor(Math.random() * list.length)]; const g = document.createElement('span'); g.className = 'gold'; g.textContent = 'Tip: '; t.append(g, document.createTextNode(s)); };
    show(); tipsTimer = setInterval(show, 7000);
  },
  done() {
    this.step('', 1);
    const b = box();
    if (!b) return;
    setTimeout(() => { b.classList.add('loading-out'); clearInterval(tipsTimer); setTimeout(() => b.remove(), 700); }, 350);
  },
  fail(msg) { const l = document.getElementById('loading-label'); if (l) { l.textContent = msg; l.classList.add('red'); } },
};
