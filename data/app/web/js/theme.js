// Applies theme.json: colours, font, every frame and the skin images as CSS variables (frames.css and panels.css only read variables).
import { assetUrl } from './dom.js';
import { ninepatch } from './ninepatch.js';

export async function applyTheme(theme) {
  const root = document.documentElement.style;
  const url = (rel) => 'url("' + assetUrl(rel) + '")';
  for (const [k, v] of Object.entries(theme.colors)) root.setProperty('--' + k.replace(/_/g, '-'), v);
  for (const [k, v] of Object.entries(theme.sizes)) root.setProperty('--' + k.replace(/_/g, '-'), v + 'px');
  const f = theme.fonts.body;
  const face = new FontFace(f.family, 'url(' + assetUrl(f.file) + ')');
  document.fonts.add(await face.load());
  root.setProperty('--font-body', '"' + f.family + '", serif');
  root.setProperty('--bar-texture', url(theme.bar_texture));
  root.setProperty('--slot-texture', url(theme.slot_texture));
  for (const [k, v] of Object.entries(theme.buttons)) root.setProperty('--btn-' + k, url(v));
  for (const [k, v] of Object.entries(theme.skin || {})) root.setProperty('--skin-' + k.replace(/_/g, '-'), url(v));
  for (const [name, fr] of Object.entries(theme.frames)) {
    const edge = !fr.edge ? 'none' : fr.nine ? url(fr.edge) : 'url(' + await ninepatch(assetUrl(fr.edge), fr.cell) + ')';
    root.setProperty('--fi-' + name, edge);
    root.setProperty('--fcell-' + name, String(fr.cell));
    root.setProperty('--fw-' + name, fr.width + 'px');
    root.setProperty('--fbg-' + name, fr.background ? url(fr.background) : 'none');
    root.setProperty('--fbgtile-' + name, fr.bg_tile ? fr.bg_tile + 'px ' + fr.bg_tile + 'px' : '100% 100%');
    root.setProperty('--fbgc-' + name, fr.bg_color);
    root.setProperty('--finset-' + name, fr.inset + 'px');
    if (fr.rolls) { root.setProperty('--froll-top-' + name, url(fr.rolls.top)); root.setProperty('--froll-bottom-' + name, url(fr.rolls.bottom)); }
  }
}
