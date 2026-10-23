"""Constrained sketches: the solver, every constraint and dimension, regions, and what is built
from them."""
import math

import numpy as np
import pytest

from openfusion import Engine, topo
from openfusion.sketch import Model, SketchError, solve

ENGINE = Engine()


def build(src):
    return ENGINE.run_source("from openfusion import *\n" + src)


def result(src, name="s"):
    r = build(src)
    assert r.script_error is None, r.script_error
    return r, next(x for x in r.results if x.name == name)


def ok(src, name="s"):
    r, res = result(src, name)
    assert res.status == "ok", r.report()
    return r, res.info.get("sketch", {})


def solved(points, curves, cons):
    m = Model(points, curves, cons)
    st = solve(m)
    return m, st


RECT = '''plate = box(length=80, width=60, height=5)
s = sketch(at=plate.face("+Z"),
    points={"a": (0, 0), "b": (41, 1), "c": (39, 26), "d": (1, 24), "o": (20, 12)},
    curves={"bottom": ("line", "a", "b"), "right": ("line", "b", "c"),
            "top": ("line", "c", "d"), "left": ("line", "d", "a"), "hole": ("circle", "o", 3)},
    constraints=[("horizontal", "bottom"), ("vertical", "right"), ("horizontal", "top"),
                 ("vertical", "left"), ("fix", "a"), ("length", "bottom", P.l),
                 ("length", "right", 25), ("radius", "hole", 4),
                 ("distance", "o", "bottom", 12), ("distance", "o", "left", 20)])
'''


def test_a_dimensioned_outline_with_a_hole_solves_exactly_and_names_its_regions():
    r, info = ok("params(l=40 * mm)\n" + RECT)
    assert info["dof"] == 0
    assert info["points"]["c"] == pytest.approx([40, 25]) and info["points"]["o"] == pytest.approx([20, 12])
    b = r.bodies["s"]
    assert set(b.face_labels()) == {"bottom+hole+left+right+top", "profile", "hole"}
    assert topo.face_area_centroid(b.faces_named("profile")[0])[0] == pytest.approx(1000 - 16 * math.pi)
    assert b.role == "reference" and b.kind == "profile"
    assert b.anchor("c").origin == pytest.approx([40 + 40, 30 + 25, 5])     # points are anchors (from the top's centre)


def test_changing_a_parameter_moves_the_sketch_and_what_is_built_from_it():
    for length in (40, 55):
        r, _ = ok(f"params(l={length} * mm)\n" + RECT + "extrude(s, 10)\n")
        plate = r.bodies["plate"]
        assert topo.volume(plate.shape) == pytest.approx(80 * 60 * 5 + (length * 25 - 16 * math.pi) * 10, rel=1e-6)
        assert "extrude1.side(hole)" in plate.face_labels()


def test_extrude_a_region_cut_and_symmetric():
    r, _ = ok("params(l=40 * mm)\n" + RECT + 'extrude(s.face("hole"), -3, op="cut")\n')
    assert topo.volume(r.bodies["plate"].shape) == pytest.approx(24000 - 16 * math.pi * 3, rel=1e-6)
    r, _ = ok("params(l=40 * mm)\n" + RECT + 'e = extrude(s, 6, op="new", symmetric=True)\n', "e")
    e = r.bodies["e"]
    lo, hi = topo.bbox(e.shape)
    assert lo[2] == pytest.approx(2) and hi[2] == pytest.approx(8)             # 3 below and above the top (z=5)
    assert {"side(top)", "side(hole)", "start", "end"} <= set(e.face_labels())
    # off any body, join becomes a new body; cut needs a target
    r, _ = ok('s = sketch(points={"o": (0, 0)}, curves={"c": ("circle", "o", 5)})\ne = extrude(s, 4)\n', "e")
    assert topo.volume(r.bodies["e"].shape) == pytest.approx(math.pi * 25 * 4, rel=1e-6)
    r, res = result('s = sketch(points={"o": (0, 0)}, curves={"c": ("circle", "o", 5)})\ne = extrude(s, 4, op="cut")\n', "e")
    assert res.status == "error" and "target" in res.message


def test_slot_from_tangent_arcs():
    src = '''s = sketch(points={"c1": (0, 0), "c2": (30, 1), "a": (0, -5), "b": (31, -4), "c": (30, 6), "d": (-1, 5)},
        curves={"l1": ("line", "a", "b"), "r1": ("arc", "c2", "b", "c"), "l2": ("line", "c", "d"), "r2": ("arc", "c1", "d", "a")},
        constraints=[("tangent", "l1", "r1"), ("tangent", "l2", "r1"), ("tangent", "l1", "r2"), ("tangent", "l2", "r2"),
                     ("horizontal", "c1", "c2"), ("fix", "c1"), ("distance", "c1", "c2", 30), ("radius", "r1", 5), ("equal", "r1", "r2")])
    '''
    r, info = ok(src)
    assert info["dof"] == 0
    assert topo.face_area_centroid(r.bodies["s"].faces_named("profile")[0])[0] == pytest.approx(300 + 25 * math.pi)


def test_every_constraint_and_dimension():
    m, st = solved(
        {"a": (0, 0), "b": (20, 1), "c": (27, 12), "d": (6, 10), "m": (10, 1), "y1": (0, 30), "y2": (0, -30),
         "p": (-5, 3), "q": (6, 4), "k": (3, 3), "o1": (40, 0), "o2": (41, 1), "e": (50, 5), "f": (60, 5)},
        {"ab": ("line", "a", "b"), "bc": ("line", "b", "c"), "cd": ("line", "c", "d"), "da": ("line", "d", "a"),
         "ax": ("line", "y1", "y2", "construction"), "c1": ("circle", "o1", 3), "c2": ("circle", "o2", 2),
         "ef": ("line", "e", "f")},
        [("fix", "a"), ("horizontal", "ab"), ("parallel", "ab", "cd"), ("parallel", "bc", "da"), ("length", "ab", 20),
         ("length", "da", 10), ("angle", "ab", "da", 60), ("midpoint", "m", "ab"), ("vertical", "ax"), ("x", "y1", 0),
         ("symmetric", "p", "q", "ax"), ("y", "p", 3), ("x", "q", 6), ("on", "k", "da"), ("y", "k", 4),
         ("concentric", "c1", "c2"), ("diameter", "c1", 10), ("radius", "c2", 2), ("x", "o1", 40), ("y", "o1", 0),
         ("tangent", "ef", "c1"), ("hdistance", "e", "f", 10), ("vdistance", "o1", "e", 5), ("x", "e", 50)])
    assert st["solved"], st
    P = {k: np.array(v) for k, v in m.points.items()}
    assert P["b"] == pytest.approx([20, 0])
    assert np.linalg.norm(P["d"] - P["a"]) == pytest.approx(10)
    assert P["m"] == pytest.approx([10, 0])
    assert P["p"] == pytest.approx([-6, 3])
    k = P["k"]
    u, w = P["a"] - P["d"], k - P["d"]
    assert abs(u[0] * w[1] - u[1] * w[0]) < 1e-6 and k[1] == pytest.approx(4)
    assert P["o2"] == pytest.approx(P["o1"]) and m.curves["c1"].radius == pytest.approx(5)
    assert P["e"][1] == pytest.approx(5) and P["f"] - P["e"] == pytest.approx([10, 0])    # tangent to c1 at its top


def test_collinear_perpendicular_coincident_and_on_a_circle():
    m, st = solved({"a": (0, 0), "b": (10, 1), "c": (14, 2), "d": (20, 3), "e": (10, 9), "o": (30, 0), "g": (34, 1)},
                   {"l1": ("line", "a", "b"), "l2": ("line", "c", "d"), "l3": ("line", "b", "e"), "ring": ("circle", "o", 4)},
                   [("fix", "a"), ("horizontal", "l1"), ("collinear", "l1", "l2"), ("perpendicular", "l1", "l3"),
                    ("coincident", "l2.start", "l1.end"), ("on", "g", "ring"), ("fix", "ring")])
    assert st["solved"]
    P = {k: np.array(v) for k, v in m.points.items()}
    assert P["c"] == pytest.approx(P["b"]) and P["d"][1] == pytest.approx(0)
    assert P["e"][0] == pytest.approx(P["b"][0])
    assert np.linalg.norm(P["g"] - P["o"]) == pytest.approx(4)


def test_conflicts_are_named_and_freedom_is_counted():
    r, res = result('''s = sketch(points={"a": (0, 0), "b": (10, 0)}, curves={"l": ("line", "a", "b")},
        constraints=[("fix", "a"), ("length", "l", 10), ("horizontal", "l"), ("hdistance", "a", "b", 12)])''')
    assert res.status == "error" and "length l 10" in res.message and "hdistance a b 12" in res.message
    _, info = ok('''s = sketch(points={"a": (0, 0), "b": (10, 0), "c": (5, 8)},
        curves={"l1": ("line", "a", "b"), "l2": ("line", "b", "c"), "l3": ("line", "c", "a")},
        constraints=[("horizontal", "l1")])''')
    assert info["dof"] == 5 and info["points"]["c"] == pytest.approx([5, 8])    # stays as drawn


def test_projected_edges_anchor_points_splines_and_construction():
    src = '''plate = box(length=40, width=30, height=5)
    s = sketch(at=plate.face("+Z"), points={"o": plate.anchor("+Z"), "p": (1, 1), "q": (8, 2), "r": (12, 9), "t": (0, 12), "u": (30, 0)},
        curves={"e": ("project", plate.edge("+X+Z")), "guide": ("line", "u", "t", "construction"),
                "sp": ("spline", "p", "q", "r", "t"), "back": ("line", "t", "p")},
        constraints=[("coincident", "p", "o"), ("on", "u", "e")])
    '''.replace("\n    ", "\n")
    r, info = ok(src)
    assert info["points"]["e.start"] == pytest.approx([20, -15]) or info["points"]["e.start"] == pytest.approx([20, 15])
    assert info["points"]["u"][0] == pytest.approx(20)                       # on the projected edge
    assert "e.start" in info["fixed"] and "o" in info["fixed"]
    assert r.bodies["s"].face_labels() == ["back+sp", "profile"]              # construction lines bound nothing
    # a projected circle (the top rim of a cylinder) is a fixed circle to constrain against
    r, info = ok('''cyl = cylinder(r=6, h=10)
s = sketch(at=cyl.face("+Z"), points={"k": (9, 1), "o": (0, 0)}, curves={"rim": ("project", cyl.edge("+Z|side")), "l": ("line", "o", "k")},
    constraints=[("on", "k", "rim"), ("fix", "o"), ("horizontal", "l")])''')
    assert info["points"]["k"] == pytest.approx([6, 0])


def test_regions_of_overlapping_and_nested_shapes():
    # two overlapping circles: three regions; the even-odd profile leaves the lens out
    r, info = ok('''s = sketch(points={"a": (0, 0), "b": (6, 0)}, curves={"c1": ("circle", "a", 5), "c2": ("circle", "b", 5)},
        constraints=[("fix", "a"), ("fix", "b"), ("radius", "c1", 5), ("radius", "c2", 5)])''')
    b = r.bodies["s"]
    # each crescent is bounded by arcs of both circles, so all three share the name and are
    # numbered: largest first, equal ones left to right
    assert sorted(set(b.face_labels())) == ["c1+c2#1", "c1+c2#2", "c1+c2#3", "profile"]
    assert len(b.faces_named("profile")) == 2
    xs = [topo.face_area_centroid(b.faces_named(f"c1+c2#{k}")[0])[1][0] for k in (1, 2)]
    assert xs[0] < xs[1]
    # a dangling line inside a square splits nothing
    r, info = ok('''s = sketch(points={"a": (0, 0), "b": (10, 0), "c": (10, 10), "d": (0, 10), "p": (3, 3), "q": (6, 5)},
        curves={"ab": ("line", "a", "b"), "bc": ("line", "b", "c"), "cd": ("line", "c", "d"), "da": ("line", "d", "a"), "stub": ("line", "p", "q")})''')
    b = r.bodies["s"]
    assert b.face_labels() == ["ab+bc+cd+da", "profile"]
    assert topo.face_area_centroid(b.faces_named("profile")[0])[0] == pytest.approx(100)


def test_revolve_and_loft_take_sketches():
    r, _ = ok('''s = sketch(points={"a": (10, 0), "b": (14, 0), "c": (14, 6), "d": (10, 6)},
        curves={"ab": ("line", "a", "b"), "bc": ("line", "b", "c"), "cd": ("line", "c", "d"), "da": ("line", "d", "a")})
v = revolve(s, axis="Y")''', "v")
    assert topo.volume(r.bodies["v"].shape) == pytest.approx(math.pi * (14 ** 2 - 10 ** 2) * 6, rel=1e-6)
    r, _ = ok('''a = sketch(points={"p": (0, 0)}, curves={"c": ("circle", "p", 10)})
b = sketch(at=world.point(0, 0, 30), points={"p": (0, 0)}, curves={"c": ("circle", "p", 4)})
l = loft(sections=[a, b])''', "l")
    assert topo.volume(r.bodies["l"].shape) == pytest.approx(math.pi * 30 / 3 * (100 + 40 + 16), rel=1e-6)
    r, res = result('''s = sketch(points={"a": (0, 0), "b": (6, 0)}, curves={"c1": ("circle", "a", 2), "c2": ("circle", "b", 2)})
v = revolve(s, axis="Y")''', "v")
    assert res.status == "error" and "pick one" in res.message


@pytest.mark.parametrize("points,curves,cons,msg", [
    ({"a": (0, 0)}, {"l": ("line", "a", "zz")}, [], "no point 'zz'"),
    ({"a": (0, 0), "b": (1, 0)}, {"l": ("blob", "a", "b")}, [], "start with one of"),
    ({"a": (0, 0), "b": (1, 0)}, {"l": ("line", "a", "b")}, [("hug", "l")], "unknown constraint"),
    ({"a": (0, 0), "b": (1, 0)}, {"l": ("line", "a", "b")}, [("length", "l")], "give the value last"),
    ({"a": (0, 0), "b": (1, 0)}, {"l": ("line", "a", "b")}, [("radius", "l", 3)], "this needs a circle or arc"),
    ({"a": (0, 0), "b": (1, 0)}, {"l": ("line", "a", "a")}, [], "same point twice"),
    ({"a b": (0, 0)}, {}, [], "without spaces"),
    ({"a": (0, 0)}, {"c": ("circle", "a", -1)}, [], "('circle', centre, radius)"),
])
def test_mistakes_are_explained(points, curves, cons, msg):
    with pytest.raises(SketchError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        m = Model(points, curves, cons)
        solve(m)


def run(src):
    return Engine().run_source("from openfusion import *\n" + src)


def test_ellipses_and_parametric_curves():
    import math
    r = run('s = sketch(points={"o": (0, 0)}, curves={"el": ("ellipse", "o", 20, 10, 30)})\nplate = extrude(s, 2)\n'
            'w = sketch(at=world.point(0, 50, 0), curves={"heart": ("parametric", "16*sin(t)**3", '
            '"13*cos(t) - 5*cos(2*t) - 2*cos(3*t) - cos(4*t)", 0, 2*pi)})\nh = extrude(w, 1)\n'
            'line = sketch(at=world.point(0, 100, 0), points={"a": (30, 5)}, curves={"wave": ("parametric", "t", "3*sin(t)", 0, 10), '
            '"tail": ("line", "wave.end", "a")})\n'
            f'big = sketch(curves={{"c": ("parametric", f"{{2*5}}*cos(t)", "10*sin(t)", 0, 2*pi)}}, at=world.point(0, -50, 0))\n'
            'b = extrude(big, 1)\n'.replace("2*pi", str(2 * math.pi)))
    assert all(x.status == "ok" for x in r.results), r.report()
    assert topo.volume(r.bodies["plate"].shape) == pytest.approx(math.pi * 20 * 10 * 2, rel=1e-3)
    assert topo.volume(r.bodies["b"].shape) == pytest.approx(math.pi * 100, rel=1e-3)
    info = next(x for x in r.results if x.name == "line").info["sketch"]
    assert info["curves"]["wave"]["pts"] == ["wave.start", "wave.end"] and len(info["curves"]["wave"]["samples"]) == 201
    assert info["points"]["wave.end"] == pytest.approx([10, 3 * math.sin(10)])
    el = next(x for x in r.results if x.name == "s").info["sketch"]["curves"]["el"]
    assert el["kind"] == "ellipse" and len(el["samples"]) == 97
    for bad, msg in [('("ellipse", "o", 0, 3)', "ellipse"), ('("parametric", "t", "y", 0, 1)', "unknown name"),
                     ('("parametric", "t", "__import__(1)", 0, 1)', "only the math"), ('("parametric", "t", "t.real", 0, 1)', "only arithmetic"),
                     ('("parametric", "t", "1/(t-0.5)", 0, 1)', "evaluate"), ('("parametric", "1", "1", 0, 1)', "no size"),
                     ('("parametric", "t", "t", 1, 0)', "t1 > t0"), ('("parametric", "t", "\\"a\\"", 0, 1)', "only numbers")]:
        res = run(f's = sketch(points={{"o": (0, 0)}}, curves={{"c": {bad}}})\n').results[0]
        assert res.status == "error" and msg in res.message, (bad, res.message)


def test_sketch_curves_are_named_edges_and_paths():
    import math
    r = run('path = sketch(at=world.anchor("XZ"), points={"a": (0, 0), "b": (0, 30), "o": (20, 30), "c": (20, 50)},\n'
            '    curves={"up": ("line", "a", "b"), "bend": ("arc", "o", "c", "b"), "guide": ("line", "a", "c", "construction")},\n'
            '    constraints=[("vertical", "up")])\n'
            'sec = sketch(points={"o": (0, 0)}, curves={"ring": ("circle", "o", 3)})\n'
            'tube = sweep(sec, path)\n'
            'leg = sweep(sec, path.edge("up"), op="new")\n'
            'both = sweep(sec, [path.edge("bend"), path.edge("up")])\n'
            'diag = sweep(sec, path.edge("guide"))\n'
            'a = set_anchor(sec, "m", at=path.anchor("b"))\n')
    assert all(x.status == "ok" for x in r.results), r.report()
    assert topo.volume(r.bodies["tube"].shape) == pytest.approx(math.pi * 9 * (30 + 20 * math.pi / 2), rel=1e-6)
    assert topo.volume(r.bodies["leg"].shape) == pytest.approx(math.pi * 9 * 30, rel=1e-6)
    assert topo.volume(r.bodies["both"].shape) == pytest.approx(topo.volume(r.bodies["tube"].shape), rel=1e-6)
    sk = r.bodies["path"]
    assert [n for n, _, _ in sk.curves()] == ["up", "bend", "guide"]
    assert len(sk.edges_named("bend")) == 1 and sk.edge_label(sk.edges_named("up")[0]) == "up"
    from openfusion.export import scene
    b = next(x for x in scene(r)["bodies"] if x["id"] == "path")
    assert b["sketch"] and [(e["label"], e.get("construction", False)) for e in b["edges"]] == [("up", False), ("bend", False), ("guide", True)]
    bad = run('s = sketch(points={"o": (0, 0)}, curves={"c": ("circle", "o", 3)})\nt = sweep(s, s.edge("nope"))\n').results[-1]
    assert bad.status == "error" and "no curve 'nope'" in bad.message and "(it has c)" in bad.message
    two = run('s = sketch(points={"o": (0, 0), "p": (10, 0)}, curves={"c": ("circle", "o", 3), "d": ("circle", "p", 2)})\n'
              'q = sketch(at=world.anchor("XZ"), points={"o": (0, 0)}, curves={"r": ("circle", "o", 1)})\nt = sweep(q, s)\n').results[-1]
    assert two.status == "error" and "several closed curves" in two.message
