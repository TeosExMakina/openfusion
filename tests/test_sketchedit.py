"""Sketch editing tools: each edit gives a sketch that still solves, with the shape expected."""
import math

import numpy as np
import pytest

from openfusion import Engine, topo
from openfusion.sketchedit import EditError, apply

ENGINE = Engine()


def lit(v):
    if isinstance(v, dict) and set(v) == {"expr"}:
        return v["expr"]
    if isinstance(v, (list, tuple)):
        return "(" + ", ".join(lit(x) for x in v) + ("," if len(v) == 1 else "") + ")"
    return repr(v).replace("'", '"')


def source(m):
    pts = ", ".join(f'"{n}": {lit(v)}' for n, v in m["points"].items())
    crv = ", ".join(f'"{n}": {lit(v)}' for n, v in m["curves"].items())
    cons = ", ".join(lit(c) for c in m["cons"])
    return f"s = sketch(points={{{pts}}}, curves={{{crv}}}, constraints=[{cons}])\n"


def run(m, extra=""):
    r = ENGINE.run_source("from openfusion import *\n" + source(m) + extra)
    bad = [f"{x.name}: {x.message}" for x in r.results if x.status not in ("ok", "warning")]
    assert not r.script_error and not bad, (r.script_error, bad, source(m))
    return r


def solved(r):
    info = next(x for x in r.results if x.name == "s").info["sketch"]
    return {"points": info["points"], "curves": info["curves"]}, info


def edit(m, op, **args):
    r = run(m)
    sv, _ = solved(r)
    out = apply(op, m, sv, args)
    m2 = {"points": out["points"], "curves": out["curves"], "cons": out["cons"]}
    r2 = run(m2)
    return m2, r2, out["select"]


RECT = {"points": {"a": [0, 0], "b": [40, 0], "c": [40, 20], "d": [0, 20]},
        "curves": {"l1": ["line", "a", "b"], "l2": ["line", "b", "c"], "l3": ["line", "c", "d"], "l4": ["line", "d", "a"]},
        "cons": [["horizontal", "l1"], ["vertical", "l2"], ["horizontal", "l3"], ["vertical", "l4"],
                 ["fix", "a"], ["length", "l1", 40], ["length", "l2", 20]]}


def copy(m):
    import json
    return json.loads(json.dumps(m))


def profile_area(r):
    return sum(topo.face_area_centroid(f)[0] for f, n in r.bodies["s"].face_names if n.name == "profile")


def test_fillet_a_corner_keeps_the_dimensions():
    m, r, sel = edit(copy(RECT), "fillet", corner="c", radius=5)
    assert any(k.startswith("c:a") for k in sel)
    assert ["radius", "a1", 5] in m["cons"] and ["tangent", "a1", "l2"] in m["cons"]
    # l2's length was measured to the corner: it still is (the corner stays, held on both lines)
    assert ["distance", "c", "b", 20] in m["cons"] and ["on", "c", "l2"] in m["cons"] and "c" in m["points"]
    assert profile_area(r) == pytest.approx(800 - (25 - math.pi * 25 / 4), rel=1e-6)
    # a corner nothing else holds just goes
    m0 = copy(RECT)
    m0["cons"] = [c for c in m0["cons"] if c[0] != "length"]
    m1, _, _ = edit(m0, "fillet", corner="c", radius=5)
    assert "c" not in m1["points"]
    # fillet the other corners too: a rounded rectangle
    for corner in ("a", "b", "d"):
        m, r, _ = edit(m, "fillet", corner=corner, radius=3)
    assert profile_area(r) == pytest.approx(800 - (25 - math.pi * 25 / 4) - 3 * (9 - math.pi * 9 / 4), rel=1e-6)


def test_fillet_keeps_a_corner_something_holds():
    m = copy(RECT)
    m["cons"].append(["x", "c", 40])                   # something holds the corner itself
    m, r, _ = edit(m, "fillet", corner="c", radius=4)
    assert "c" in m["points"] and ["on", "c", "l2"] in m["cons"] and ["on", "c", "l3"] in m["cons"]
    _, info = solved(r)
    assert info["points"]["c"] == pytest.approx([40, 20])          # a virtual sharp, where it was


def test_fillet_errors():
    r = run(copy(RECT))
    sv, _ = solved(r)
    with pytest.raises(EditError, match="exactly two lines"):
        apply("fillet", copy(RECT), sv, {"corner": "zz", "radius": 2})
    with pytest.raises(EditError, match="doesn't fit"):
        apply("fillet", copy(RECT), sv, {"corner": "c", "radius": 50})
    with pytest.raises(EditError, match="above 0"):
        apply("fillet", copy(RECT), sv, {"corner": "c", "radius": -1})


CROSS = {"points": {"a": [0, 0], "b": [40, 0], "c": [20, -10], "d": [20, 10], "e": [30, -10], "f": [30, 10]},
         "curves": {"h": ["line", "a", "b"], "v1": ["line", "c", "d"], "v2": ["line", "e", "f"]},
         "cons": [["horizontal", "h"], ["vertical", "v1"], ["vertical", "v2"]]}


def test_trim_lines_at_the_curves_that_cross_them():
    # the middle piece of h (between the two verticals) goes: h is cut in two, held collinear
    m, r, _ = edit(copy(CROSS), "trim", curve="h", at=[25, 0])
    assert set(m["curves"]) == {"h", "v1", "v2", "l1"}
    _, info = solved(r)
    h = [info["points"][p] for p in info["curves"]["h"]["pts"]]
    l1 = [info["points"][p] for p in info["curves"]["l1"]["pts"]]
    assert sorted(x for x, _ in h) == pytest.approx([0, 20]) and sorted(x for x, _ in l1) == pytest.approx([30, 40])
    assert ["collinear", "h", "l1"] in m["cons"]
    on = [c for c in m["cons"] if c[0] == "on"]
    assert {c[2] for c in on} == {"v1", "v2"}
    # an end piece: the line just gets shorter
    m, r, _ = edit(copy(CROSS), "trim", curve="h", at=[5, 0])
    _, info = solved(r)
    assert sorted(info["points"][p][0] for p in info["curves"]["h"]["pts"]) == pytest.approx([20, 40])
    assert "a" not in m["points"]                         # nothing else used it
    # a line crossed by nothing: trimming it deletes it
    m, r, _ = edit(copy(CROSS), "trim", curve="v1", at=[20, 5])
    assert "v1" in m["curves"]                            # v1 is crossed by h: only its top piece goes
    m2, r2, _ = edit({"points": {"a": [0, 0], "b": [1, 0]}, "curves": {"l": ["line", "a", "b"]}, "cons": []}, "trim", curve="l", at=[0.5, 0])
    assert m2["curves"] == {}


def test_trim_circles_and_arcs():
    m = {"points": {"o": [0, 0], "a": [-20, 0], "b": [20, 0]},
         "curves": {"c": ["circle", "o", 10], "l": ["line", "a", "b"]}, "cons": [["horizontal", "l"], ["diameter", "c", 20]]}
    # the top half of the circle goes: an arc is left, its ends on the line
    m, r, _ = edit(m, "trim", curve="c", at=[0, 10])
    assert m["curves"]["c"][0] == "arc"
    _, info = solved(r)
    g = info["curves"]["c"]
    pts = [info["points"][p] for p in g["pts"][1:]]
    assert sorted(x for x, _ in pts) == pytest.approx([-10, 10]) and all(abs(y) < 1e-6 for _, y in pts)
    # then the line's ends outside the half circle: a closed D shape
    m, r, _ = edit(m, "trim", curve="l", at=[-15, 0])
    m, r, _ = edit(m, "trim", curve="l", at=[15, 0])
    assert profile_area(r) == pytest.approx(math.pi * 100 / 2, rel=1e-4)
    # an arc's middle piece: two arcs on the same centre, kept equal
    m = {"points": {"o": [0, 0], "s": [10, 0], "e": [-10, 0], "a": [-5, -5], "b": [-5, 20], "c": [5, -5], "d": [5, 20]},
         "curves": {"arc": ["arc", "o", "s", "e"], "l": ["line", "a", "b"], "k": ["line", "c", "d"]},
         "cons": [["vertical", "l"], ["vertical", "k"]]}
    m, r, _ = edit(m, "trim", curve="arc", at=[0, 10])
    assert ["equal", "arc", "a1"] in m["cons"] and m["curves"]["a1"][1] == "o"


def test_offset_a_closed_outline_outward_and_inward():
    base = copy(RECT)
    m, r, sel = edit(base, "offset", curves=["l1", "l2", "l3", "l4"], distance=3)
    assert len(sel) == 8                                   # 4 corner points, 4 lines
    assert sum(1 for c in m["cons"] if c[0] == "distance") == 4
    regions = sorted(topo.face_area_centroid(f)[0] for f, n in r.bodies["s"].face_names if n.name != "profile")
    assert regions == pytest.approx([46 * 26 - 800, 800])                  # the band between, and the inside
    m, r, _ = edit(copy(RECT), "offset", curves=["l1", "l2", "l3", "l4"], distance=-2)
    areas = sorted(topo.face_area_centroid(f)[0] for f, n in r.bodies["s"].face_names if n.name != "profile")
    assert areas == pytest.approx([800 - 36 * 16, 36 * 16])
    # dimensions drive the copy: make the rectangle longer and the offset follows
    m["cons"] = [["length", "l1", 60] if c[:2] == ["length", "l1"] else c for c in m["cons"]]
    r = run(m)
    areas = sorted(topo.face_area_centroid(f)[0] for f, n in r.bodies["s"].face_names if n.name != "profile")
    assert areas == pytest.approx([1200 - 56 * 16, 56 * 16])


def test_offset_circles_arcs_and_a_rounded_chain():
    m = {"points": {"o": [0, 0]}, "curves": {"c": ["circle", "o", 10]}, "cons": [["diameter", "c", 20]]}
    m, r, _ = edit(m, "offset", curves=["c"], distance=2)
    _, info = solved(r)
    assert sorted(g["radius"] for g in info["curves"].values()) == pytest.approx([10, 12])
    # a rounded rectangle offsets as one closed chain (tangent joins stay joined)
    rr = copy(RECT)
    for corner in ("a", "b", "c", "d"):
        rr, _, _ = edit(rr, "fillet", corner=corner, radius=4)
    names = list(rr["curves"])
    m, r, _ = edit(rr, "offset", curves=names, distance=2)
    band = next(topo.face_area_centroid(f)[0] for f, n in r.bodies["s"].face_names if n.name == "profile")
    # the band round a rounded rectangle: its perimeter times d, plus pi d^2 at the corners
    perim = 2 * (32 + 12) + 2 * math.pi * 4
    assert band == pytest.approx(perim * 2 + math.pi * 4, rel=1e-6)


def test_mirror_across_a_line():
    m = {"points": {"m1": [0, -30], "m2": [0, 30], "a": [5, 0], "b": [20, 0], "c": [20, 10], "o": [12, 20]},
         "curves": {"axis": ["line", "m1", "m2", "construction"], "l1": ["line", "a", "b"], "l2": ["line", "b", "c"],
                    "l3": ["line", "c", "a"], "h": ["circle", "o", 3], "ar": ["arc", "o", "b", "c"]},
         "cons": [["vertical", "axis"], ["diameter", "h", 6], ["fix", "m1"], ["fix", "m2"]]}
    m, r, sel = edit(m, "mirror", curves=["l1", "l2", "l3", "h", "ar"], line="axis")
    _, info = solved(r)
    sym = [c for c in m["cons"] if c[0] == "symmetric"]
    assert len(sym) == 4                                    # a, b, c, o (none on the axis)
    for p, q, _ in (c[1:] for c in sym):
        P, Q = info["points"][p], info["points"][q]
        assert Q == pytest.approx([-P[0], P[1]])
    circles = [c for c in info["curves"].values() if c["kind"] == "circle"]
    assert len(circles) == 2 and all(c["radius"] == pytest.approx(3) for c in circles)
    # change the original: the mirror follows
    m["cons"].append(["x", "a", 8])
    r = run(m)
    _, info = solved(r)
    q = next(c[2] for c in m["cons"] if c[0] == "symmetric" and c[1] == "a")
    assert info["points"][q][0] == pytest.approx(-8)
    with pytest.raises(EditError, match="mirror across a line"):
        apply("mirror", m, solved(r)[0], {"curves": ["l1"], "line": "h"})


def test_patterns():
    m = {"points": {"o": [0, 0], "a": [-2, -2], "b": [2, -2]},
         "curves": {"h": ["circle", "o", 1.5], "l": ["line", "a", "b"]}, "cons": [["diameter", "h", 3], ["fix", "o"]]}
    m, r, sel = edit(m, "pattern_rect", curves=["h"], nx=3, dx=10, ny=2, dy=8)
    _, info = solved(r)
    centres = sorted(tuple(np.round(info["points"][c["pts"][0]], 6)) for c in info["curves"].values() if c["kind"] == "circle")
    assert centres == [(0, 0), (0, 8), (10, 0), (10, 8), (20, 0), (20, 8)]
    # resizing the original resizes the copies (held equal)
    m["cons"] = [["diameter", "h", 4] if c[0] == "diameter" else c for c in m["cons"]]
    _, info = solved(run(m))
    assert all(c["radius"] == pytest.approx(2) for c in info["curves"].values() if c["kind"] == "circle")
    # circular: 6 copies of a small circle round the centre
    m = {"points": {"o": [0, 0], "p": [10, 0]}, "curves": {"h": ["circle", "p", 1]}, "cons": [["fix", "o"]]}
    m, r, _ = edit(m, "pattern_circ", curves=["h"], centre="o", count=6)
    _, info = solved(r)
    angs = sorted(round(math.degrees(math.atan2(info["points"][c["pts"][0]][1], info["points"][c["pts"][0]][0])) % 360, 4)
                  for c in info["curves"].values() if c["kind"] == "circle")
    assert angs == pytest.approx([0, 60, 120, 180, 240, 300], abs=1e-3)
    with pytest.raises(EditError, match="at least 2"):
        apply("pattern_circ", m, solved(r)[0], {"curves": ["h"], "centre": "o", "count": 1})


def test_gears_mirror_and_pattern_with_their_options():
    m = {"points": {"o": [0, 0], "m1": [30, -10], "m2": [30, 10]},
         "curves": {"g": ["gear", "o", 2, 12, {"expr": '{"backlash": 0.1}'}], "ax": ["line", "m1", "m2", "construction"]},
         "cons": [["vertical", "ax"], ["fix", "o"]]}
    m2, r, _ = edit(m, "mirror", curves=["g"], line="ax")
    g2 = next(c for n, c in m2["curves"].items() if n != "g" and c[0] == "gear")
    assert "backlash" in g2[4]["expr"] and "angle" in g2[4]["expr"]
    m3, r, _ = edit(m, "pattern_circ", curves=["g"], centre="m1", count=3)
    assert sum(1 for c in m3["curves"].values() if c[0] == "gear") == 3


def test_unknown_tool():
    with pytest.raises(EditError, match="unknown sketch tool"):
        apply("explode", copy(RECT), {}, {})
