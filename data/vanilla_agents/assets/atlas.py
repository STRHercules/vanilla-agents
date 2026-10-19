"""Composite RGBA layers onto a base image (character skin atlas: body + underwear + face). Pure bytes in, bytes out."""


def over(base, bw, layer, lw, lh, x, y):
    """Alpha-composite `layer` (RGBA bytes, lw x lh) onto `base` (bytearray RGBA, width bw) at (x, y); clipped to the base."""
    bh = len(base) // (bw * 4)
    for j in range(lh):
        if not 0 <= y + j < bh:
            continue
        for i in range(lw):
            if not 0 <= x + i < bw:
                continue
            s, d = (j * lw + i) * 4, ((y + j) * bw + x + i) * 4
            a = layer[s + 3]
            if a == 255:
                base[d:d + 4] = layer[s:s + 4]
            elif a:
                for c in range(3):
                    base[d + c] = (layer[s + c] * a + base[d + c] * (255 - a)) // 255
                base[d + 3] = max(base[d + 3], a)
    return base
