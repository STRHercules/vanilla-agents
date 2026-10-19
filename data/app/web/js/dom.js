// Tiny DOM helpers shared by every panel.
export function el(tag, cls, text, attrs) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  if (attrs) for (const k of Object.keys(attrs)) e.setAttribute(k, attrs[k]);
  return e;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function assetUrl(rel) {
  return '/assets/' + rel;
}

export function icon(rel, cls) {
  const i = el('img', 'icon ' + (cls || ''), null, { alt: '', draggable: 'false' });
  i.src = assetUrl(rel);
  return i;
}

// Status bar: coloured fill over the real game bar texture, text centred on top.
export function bar(colorVar, tipKey) {
  const root = el('div', 'bar');
  const fill = el('div', 'bar-fill');
  fill.style.backgroundColor = 'var(' + colorVar + ')';
  const label = el('div', 'bar-text');
  root.append(fill, label);
  if (tipKey) root.dataset.tip = tipKey;
  return {
    root,
    set(pct, text) {
      fill.style.width = Math.max(0, Math.min(100, pct || 0)) + '%';
      label.textContent = text;
    },
  };
}
