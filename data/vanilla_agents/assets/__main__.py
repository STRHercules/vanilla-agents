"""python -m vanilla_agents.assets  bake [steps...] | ls <pattern> | extract <game path> <out file>"""
import argparse
import sys

from ..app.config import load_all
from . import bake
from .mpq import DataDir


def main(argv=None):
    cfg = load_all()
    ap = argparse.ArgumentParser(prog="python -m vanilla_agents.assets", description=__doc__)
    ap.add_argument("--data-dir", default=cfg["app"].get("data_dir", ""), help="WoW 1.12 Data folder (default: app.json / vanilla.local.json data_dir)")
    ap.add_argument("--out", default=cfg["app"]["assets_dir"], help="assets folder (default: app.json assets_dir)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bake", help="bake game art into the assets folder")
    b.add_argument("steps", nargs="*", choices=list(bake.STEPS) + ["all"], default=["all"])
    b.add_argument("--count", type=int, default=30, help="workers in the pool")
    b.add_argument("--seed", type=int, default=7, help="worker pool seed (same seed = same workers)")
    b.add_argument("--only", default="", help="buildings step: bake only these comma-separated keys (the rest stay as baked)")
    ls = sub.add_parser("ls", help="list game files matching a pattern, e.g. 'sound\\music\\*.mp3'")
    ls.add_argument("pattern")
    ex = sub.add_parser("extract", help="copy one game file out")
    ex.add_argument("path")
    ex.add_argument("dest")
    a = ap.parse_args(argv)
    if a.cmd == "bake":
        steps = bake.STEPS if "all" in a.steps else a.steps
        return bake.run(steps, a.data_dir, a.out, cfg, a.count, a.seed, tuple(k for k in a.only.split(",") if k))
    with DataDir(a.data_dir) as dd:
        if a.cmd == "ls":
            for n in dd.names(a.pattern):
                print(n)
            return 0
        data = dd.read(a.path)
        if data is None:
            print("not found:", a.path)
            return 1
        with open(a.dest, "wb") as f:
            f.write(data)
        return 0


if __name__ == "__main__":
    sys.exit(main())
