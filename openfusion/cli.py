"""openfusion command line.

  openfusion build part.ofs [--set name=value ...] [-o part.3mf -o part.step] [--body NAME ...] [--each] [--quiet]
  openfusion watch part.ofs [--port 8765] [--set name=value ...]
  openfusion view  part.ofs -o part.html
  openfusion gui   part.ofs [--port 8770]
  openfusion new   part.ofs
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

TEMPLATE = '''# OpenFusion part. Edit, save, and `openfusion watch` rebuilds it.
from openfusion import *

# the part's parameters: edit them here or in the app (Parameters, Ctrl+Shift+V)
params({
    "width":  40 * mm,   # plate size along X
    "depth":  30 * mm,   # plate size along Y
    "height": 6 * mm,    # plate thickness
    "peg_r":  4 * mm,    # peg radius
    "peg_h":  12 * mm,   # peg height
})

base = box(length=P.width, width=P.depth, height=P.height, origin="center_xy")
peg  = cylinder(r=P.peg_r, h=P.peg_h)
place(peg.anchor("-Z"), onto=base.anchor("+Z"))   # peg stands on the center of the top face
join(base, tools=[peg])
fillet(base, edges=base.edges("+Z", kind="outer"), r=1 * mm)
'''


def _overrides(pairs):
    from . import cm, deg, inch, mm
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--set expects name=value, got '{p}'")
        k, v = p.split("=", 1)
        try:
            out[k.strip()] = float(eval(v, {"__builtins__": {}}, {"mm": mm, "cm": cm, "inch": inch, "deg": deg}))
        except Exception:
            raise SystemExit(f"--set {k}: cannot read '{v}' as a number (units mm, cm, inch allowed)")
    return out


def _quiet_occt():
    """Silence OCCT's console chatter (STEP writer statistics)."""
    try:
        from OCP.Message import Message
        msgr = Message.DefaultMessenger_s()
        printers = msgr.Printers()
        for p in list(printers):
            msgr.RemovePrinter(p)
    except Exception:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="openfusion", description="Parametric, anchor-based modeling for 3D printing.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a script, print the timeline report, export files")
    b.add_argument("script")
    b.add_argument("--set", action="append", metavar="NAME=VALUE", help="override a parameter (repeatable)")
    b.add_argument("-o", "--output", action="append", help="export: .3mf, .stl or .step (repeatable)")
    b.add_argument("--tolerance", type=float, default=0.01, help="mesh deviation for STL/3MF in mm (default 0.01)")
    b.add_argument("--body", action="append", metavar="NAME",
                   help="export only this body (repeatable; picks reference bodies too). Default: every solid body")
    b.add_argument("--each", action="store_true", help="one file per body: part.stl becomes part-plate.stl, part-post.stl, ...")
    b.add_argument("--quiet", action="store_true", help="only print problems and the summary")
    w = sub.add_parser("watch", help="live viewer that rebuilds on every save")
    w.add_argument("script")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--set", action="append", metavar="NAME=VALUE")
    w.add_argument("--no-browser", action="store_true")
    v = sub.add_parser("view", help="write a self-contained HTML viewer of the model")
    v.add_argument("script")
    v.add_argument("-o", "--output", required=True)
    v.add_argument("--set", action="append", metavar="NAME=VALUE")
    g = sub.add_parser("gui", help="the modeling app: build by clicking, the script is written for you")
    g.add_argument("script")
    g.add_argument("--port", type=int, default=8770)
    g.add_argument("--no-browser", action="store_true")
    ap_ = sub.add_parser("app", help="the modeling app in its own window (needs: pip install 'openfusion[app]')")
    ap_.add_argument("script", nargs="?")
    n = sub.add_parser("new", help="create a starter script")
    n.add_argument("script")
    a = ap.parse_args(argv)
    _quiet_occt()

    if a.cmd == "app":
        from .app import main as app_main
        app_main([a.script] if a.script else [])
        return 0

    if a.cmd == "new":
        p = pathlib.Path(a.script)
        if p.exists():
            raise SystemExit(f"{p} already exists")
        p.write_text(TEMPLATE)
        print(f"Created {p}. Next: openfusion gui {p}")
        return 0

    if not os.path.exists(a.script):
        raise SystemExit(f"no such file: {a.script}")
    ov = _overrides(getattr(a, "set", None))
    bad = [o for o in (getattr(a, "output", None) or []) if a.cmd == "build"
           and not o.lower().endswith((".stl", ".3mf", ".step", ".stp"))]
    if bad:
        raise SystemExit(f"-o {bad[0]}: the file name must end in .stl, .3mf or .step")

    if a.cmd == "gui":
        from .gui.server import serve as serve_gui
        serve_gui(a.script, a.port, open_browser=not a.no_browser)
        return 0

    if a.cmd == "watch":
        from .viewer import serve
        serve(a.script, a.port, ov, open_browser=not a.no_browser)
        return 0

    from .document import Engine
    res = Engine().run_file(a.script, ov)

    if a.cmd == "view":
        from .viewer import static_page
        pathlib.Path(a.output).write_text(static_page(res, os.path.abspath(a.script)), encoding="utf-8")
        print(res.summary())
        print(f"Wrote {a.output}")
        return 0 if res.ok else 1

    if a.quiet:
        lines = [l for l in res.report().splitlines() if l.startswith((" X", " -", " !", "      ", "SCRIPT"))]
        print("\n".join(lines + [res.summary()]))
    else:
        print(res.report())
    from .export import export
    if a.body:
        missing = [n for n in a.body if n not in res.bodies or res.bodies[n].role == "removed"]
        if missing:
            print(f"No body named {', '.join(missing)}. Bodies: {', '.join(res.bodies)}", file=sys.stderr)
            return 1
        stale = [f"{n} ({res.stale[n]} failed)" for n in a.body if n in res.stale]
        if stale:
            print(f"Out of date, nothing exported: {', '.join(stale)}", file=sys.stderr)
            return 1
        chosen = [res.bodies[n] for n in a.body]
    else:
        if not res.ok:
            print("Build has errors; nothing exported." if a.output else "", file=sys.stderr)
            return 1
        chosen = res.solids()
    for out in a.output or []:
        targets = [(out, [b]) for b in chosen] if a.each else [(out, chosen)]
        for path, bs in targets:
            if a.each:
                stem, _, ext = out.rpartition(".")
                path = f"{stem}-{bs[0].id}.{ext}"
            k = export(bs, path, a.tolerance, only_solid=not a.body)
            print(f"Wrote {path} ({k} {'triangles' if path.lower().endswith('.stl') else 'bodies'})")
    return 0 if res.ok or a.body else 1


if __name__ == "__main__":
    sys.exit(main())
