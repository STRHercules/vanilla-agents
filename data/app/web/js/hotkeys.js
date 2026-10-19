// Keys from hotkeys.json open and close panels; Escape closes the topmost thing or opens the menu.
export function initHotkeys(ctx) {
  const byKey = {};
  for (const [name, key] of Object.entries(ctx.cfg.hotkeys)) byKey[key] = name;
  const panelFor = {};
  for (const [pname, p] of Object.entries(ctx.cfg.panels)) if (p.hotkey) panelFor[p.hotkey] = pname;
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    // typing in a box, or a button / list that has keyboard focus (Space / Enter / letters belong to it), never fires a hotkey; Esc always works
    const typing = (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(e.target.tagName) || e.target.isContentEditable) && e.key !== 'Escape';
    const name = byKey[e.key.toLowerCase()];
    if (typing || !name) return;
    e.preventDefault();
    if (name === 'esc_menu') ctx.escape();
    else if (name === 'jukebox' || name === 'jukebox_next') { if (ctx.jukebox) ctx.jukebox[name === 'jukebox' ? 'toggle' : 'next'](); }
    else if (ctx.actions && ctx.actions[name]) ctx.actions[name]();
    else if (ctx.panels[panelFor[name]]) ctx.panels[panelFor[name]].toggle();
  });
  // a button clicked with the mouse gives its focus back, so the hotkeys keep working after a click (keyboard focus via Tab stays)
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('button');
    if (b && e.detail > 0) setTimeout(() => { if (document.activeElement === b) b.blur(); }, 0);
  }, true);
}
