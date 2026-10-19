// Cast bar (spellbook style): fills while the checker runs a test. Progress is a guess from the expected time in app.json; it never passes 95% until the server says the check ended.
import { bar, el } from '../dom.js';

export function build(ctx, panel) {
  const c = panel.cfg;
  const b = bar('--cast', c.tips.cast);
  b.root.classList.add('cast-bar');
  panel.body.append(b.root);
  let base = 0, at = 0, expected = 1, check = '', timer = null, was = false;
  const draw = () => {
    const e = base + (Date.now() - at) / 1000;
    b.set(Math.min(95, (100 * e) / expected), ctx.t(c.text.casting, { check }));
  };
  return function update(s) {
    const v = s.verify;
    if (v && v.running) {
      base = v.elapsed; at = Date.now(); expected = Math.max(1, v.expected); check = v.check;
      panel.root.hidden = false; was = true;
      if (!timer) timer = setInterval(draw, 250);
      draw();
    } else if (was) {
      was = false; clearInterval(timer); timer = null;
      b.set(100, ctx.t(c.text.done));
      setTimeout(() => { if (!was) panel.root.hidden = true; }, 2500);
    }
  };
}
