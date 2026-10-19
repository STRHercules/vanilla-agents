"""Cut the UI skin (app/skin/*.png) out of the owner's frame sheet (app/skin/source/frames_sheet.png).
Developer tool: needs Pillow (pip install pillow). The PNGs it writes are committed, so running the program never needs it.
Every crop box is in sheet pixels; white sheet background becomes transparent, grey placeholder fills become dark metal."""
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "app", "skin", "source", "frames_sheet.png")
OUT = os.path.join(ROOT, "app", "skin")
DARK = (24, 24, 28)


def key_white(im, soft=4, cut=255):
    """Near-white, unsaturated pixels -> transparent (soft edge keeps the frames' drop shadow as a dark halo).
    Pixels lighter than `cut` go fully transparent (the banner's pale inside)."""
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if max(r, g, b) - min(r, g, b) < 28:
                lo = min(r, g, b)
                px[x, y] = (r, g, b, 0 if lo >= cut else max(0, min(a, (255 - lo) * soft)))
    return im


def key_grey(im, sat=26, erode=1):
    """Parchment pieces: every grey/white pixel (the sheet's background and its soft shadow) -> transparent, then the
    alpha edge is pulled in by `erode` px so no light fringe is left; the panel gets a clean CSS drop shadow instead."""
    from PIL import ImageFilter
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            s = max(r, g, b) - min(r, g, b)
            if s < sat:
                px[x, y] = (r, g, b, 0 if s < sat * 0.6 else int(a * (s - sat * 0.6) / (sat * 0.4)))
    if erode:
        alpha = im.getchannel("A").filter(ImageFilter.MinFilter(2 * erode + 1))
        im.putalpha(alpha)
    return im


def darken_grey(im, lo=120, span=18):
    """Light grey placeholder fills (inside frames) -> dark metal, keeping their shading a little."""
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a and max(r, g, b) - min(r, g, b) < 22 and min(r, g, b) > lo:
                k = (min(r, g, b) - lo) / (255 - lo)
                px[x, y] = (int(DARK[0] + span * k), int(DARK[1] + span * k), int(DARK[2] + span * 1.1 * k), a)
    return im


def hole(im, cx, cy, r):
    """Transparent disc (where the portrait goes)."""
    m = Image.new("L", im.size, 255)
    ImageDraw.Draw(m).ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)
    a = im.getchannel("A")
    im.putalpha(Image.composite(a, m, m))
    return im


def keep_only(im, cx, cy, r, plate):
    """Clear everything outside the ring (radius r) and its level plate: the sheet's sample bars and buttons beside it."""
    m = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(m)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    d.rounded_rectangle(plate, radius=5, fill=255)
    im.putalpha(Image.composite(im.getchannel("A"), m, m))
    return im


def crop(sheet, box):
    return sheet.crop(box).convert("RGBA")


def main():
    sheet = Image.open(SRC).convert("RGBA")
    save = lambda im, n: im.save(os.path.join(OUT, n + ".png"))
    # celebrate banner: gold frame only, white inside and outside become transparent
    save(key_white(crop(sheet, (26, 42, 450, 160)), soft=6, cut=176), "banner")
    # portrait ring with its level plate; the parchment inside is cut out for the portrait
    ring = key_grey(key_white(crop(sheet, (777, 42, 898, 172))), erode=0)
    ring = hole(ring, 837 - 777, 106 - 42, 40)
    save(keep_only(ring, 837 - 777, 106 - 42, 58, (44, 104, 78, 130)), "portrait_ring")
    # small round button (timeline step buttons)
    btn = key_white(crop(sheet, (787, 181, 828, 222)))
    save(btn, "round_button")
    # metal 9-slice panel frame (corners 14 px); the grey inside is not drawn (border-image without fill)
    save(darken_grey(key_white(crop(sheet, (472, 44, 748, 179))), lo=90, span=46), "frame_metal")   # light bevel -> dark steel: no white rim
    # inventory slot: metal square, transparent middle
    save(key_white(crop(sheet, (309, 565, 344, 600)), soft=6), "slot")
    # chat scroll: top roll, parchment body (below the sample dropdown), bottom roll
    save(key_grey(crop(sheet, (741, 499, 973, 533))), "scroll_top")
    save(key_grey(crop(sheet, (741, 552, 973, 674))), "scroll_body")
    save(key_grey(crop(sheet, (741, 676, 973, 717))), "scroll_bottom")
    # action bar: left orb cap, a rail tile (between the sample slots), right orb cap
    save(darken_grey(key_white(crop(sheet, (525, 518, 578, 604)))), "orb_left")
    save(darken_grey(key_white(crop(sheet, (625, 518, 643, 604)))), "orb_rail")
    save(darken_grey(key_white(crop(sheet, (682, 518, 731, 604)))), "orb_right")
    print("skin written to", OUT)


if __name__ == "__main__":
    main()
