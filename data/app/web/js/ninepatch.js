// Builds a 3x3 border image from the game's 8-piece edge strip (left,right,top,bottom,TL,TR,BL,BR) so CSS border-image can 9-slice it.
function loadImage(url) {
  return new Promise((ok, bad) => {
    const i = new Image();
    i.onload = () => ok(i);
    i.onerror = () => bad(new Error('missing image ' + url));
    i.src = url;
  });
}

export async function ninepatch(url, cell) {
  const img = await loadImage(url);
  const c = document.createElement('canvas');
  c.width = c.height = cell * 3;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;
  const piece = (n, dx, dy) => g.drawImage(img, n * cell, 0, cell, cell, dx * cell, dy * cell, cell, cell);
  piece(4, 0, 0); piece(2, 1, 0); piece(5, 2, 0);
  piece(0, 0, 1);                 piece(1, 2, 1);
  piece(6, 0, 2); piece(3, 1, 2); piece(7, 2, 2);
  return c.toDataURL('image/png');
}
