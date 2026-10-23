"""OpenFusion: parametric, anchor-based solid modeling for 3D printing.

    from openfusion import *

    params(w=40 * mm, t=3 * mm)
    plate = box(length=80, width=P.w, height=P.t, origin="center_xy")
    post = cylinder(r=6, h=20)
    place(post.anchor("-Z"), onto=plate.anchor("+Z"))
"""
from __future__ import annotations

from .document import Document, Engine, current_doc, load_script, load_source
from .ops import (box, chamfer, combine, cone, coil, copy, cut, extrude, fillet, intersect, join, mirror,
                  move, pattern_circ, pattern_rect, place, place_on_bed, polygon, remove, rotate, scale,
                  set_anchor, set_role, sphere, split, torus, cylinder,
                  profile, revolve, loft, sweep, shell, draft, sketch, hole, thread, text, plane, axis, import_file)
from .refs import edge_between, world

__version__ = "0.9.0"

# ---------------------------------------------------------------- units
mm = 1.0
cm = 10.0
m = 1000.0
inch = 25.4
deg = 1.0
rad = 57.29577951308232


# ---------------------------------------------------------------- parameters
def params(*tables, **values):
    """Declare named parameters, as a dict or as keywords:

        params({"wall": 2 * mm, "length": 60 * mm})
        params(wall=2 * mm, length=60 * mm)

    Use them as P.wall. Values given on the command line (--set name=value) override
    the script's. Returns P for convenience."""
    doc = current_doc()
    merged = {}
    for t in tables:
        if not isinstance(t, dict):
            raise TypeError("params() takes a dict of name: value, or name=value keywords")
        merged.update(t)
    merged.update(values)
    for k, v in merged.items():
        if not isinstance(k, str) or not k.isidentifier():
            raise ValueError(f"parameter name {k!r} must be a plain name (letters, digits, _)")
        if k in doc.overrides:
            v = doc.overrides[k]
        doc.params[k] = v
    return P


class _Params:
    """`P.name` reads a parameter declared with params()."""

    def __getattr__(self, name):
        doc = current_doc()
        if name in doc.params:
            return doc.params[name]
        raise AttributeError(f"no parameter '{name}' (declare it with params({name}=...))")

    def __repr__(self):
        return f"P({dict(current_doc().params)})"


P = _Params()

__all__ = [
    "params", "P", "mm", "cm", "m", "inch", "deg", "rad", "world", "edge_between",
    "box", "cylinder", "cone", "sphere", "torus", "coil", "polygon",
    "place", "place_on_bed", "move", "rotate", "copy",
    "pattern_rect", "pattern_circ", "mirror", "scale",
    "combine", "join", "cut", "intersect", "fillet", "chamfer", "extrude", "split",
    "set_anchor", "set_role", "remove",
    "profile", "revolve", "loft", "sweep", "shell", "draft", "sketch", "hole", "thread", "text", "plane", "axis", "import_file",
]
