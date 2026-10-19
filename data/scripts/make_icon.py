r"""Turn a baked game icon (PNG) into a Windows .ico (the PNG is embedded as is; Windows Vista and later accept that).
Usage: python scripts/make_icon.py [png] [ico]   (defaults: inv_misc_gear_01 -> ~\.vanilla-agents\assets\vanilla-agents.ico, outside git)"""
import struct
import sys

import os

ASSETS = os.path.expanduser(os.path.join("~", ".vanilla-agents", "assets"))
SRC = os.path.join(ASSETS, "interface", "icons", "inv_misc_gear_01.png")
DST = os.path.join(ASSETS, "vanilla-agents.ico")


def png_size(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    return struct.unpack(">II", data[16:24])


def png_to_ico(data):
    w, h = png_size(data)
    if w > 256 or h > 256:
        raise ValueError("icon larger than 256x256")
    entry = struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(data), 22)
    return struct.pack("<HHH", 0, 1, 1) + entry + data


if __name__ == "__main__":
    src, dst = (sys.argv[1:3] + [SRC, DST][len(sys.argv[1:3]):])[:2]
    with open(src, "rb") as f:
        ico = png_to_ico(f.read())
    with open(dst, "wb") as f:
        f.write(ico)
    print("wrote", dst)
