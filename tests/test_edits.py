"""Editing early steps: later steps keep building and keep pointing at the same things.

The classic way a parametric model breaks: change a sketch or add a step near the start, and
a fillet further down lands on a different edge (or fails). These tests make the edits a
user makes and check, by position, that the references still mean what they meant."""
import numpy as np
import pytest

from openfusion import Engine, topo

ENGINE = Engine()


def build(src):
    r = ENGINE.run_source("from openfusion import *\n" + src)
    bad = [f"{x.name}: {x.message}" for x in r.results if x.status not in ("ok", "warning")]
    assert not r.script_error and not bad, (r.script_error, bad)
    return r


def faces_of(r, body, prefix):
    b = r.bodies[body]
    return [f for f, n in b.face_names if n.label(b.id).startswith(prefix)]


def centroid(faces):
    return np.mean([topo.face_area_centroid(f)[1] for f in faces], axis=0)


def bracket(L=60, W=30, extra_hole=False, shuffle=False, construction=False, notch_r=None):
    pts = {"a": (-L / 2, -W / 2), "b": (L / 2, -W / 2), "c": (L / 2, W / 2), "d": (-L / 2, W / 2), "o": (-L / 4, 0)}
    curves = [("bottom", '("line", "a", "b")'), ("right", '("line", "b", "c")'), ("top", '("line", "c", "d")'),
              ("left", '("line", "d", "a")'), ("hole", '("circle", "o", 4)')]
    if extra_hole:
        pts["o2"] = (L / 4, 0)
        curves.append(("hole2", '("circle", "o2", 3)'))
    if construction:
        curves.append(("diag", '("line", "a", "c", "construction")'))
    if shuffle:
        curves = curves[::-1]
    cons = ['("horizontal", "bottom")', '("vertical", "right")', '("horizontal", "top")', '("vertical", "left")',
            f'("length", "bottom", {L})', f'("length", "left", {W})', f'("x", "a", {-L / 2})', f'("y", "a", {-W / 2})']
    P = ", ".join(f'"{k}": {v}' for k, v in pts.items())
    C = ", ".join(f'"{k}": {v}' for k, v in curves)
    return (f'base = box(length={L + 40}, width={W + 40}, height=5, origin="center_xy")\n'
            f's = sketch(at=base.face("+Z"), points={{{P}}}, curves={{{C}}}, constraints=[{", ".join(cons)}])\n'
            'lug = extrude(s, 8)\n'
            'f1 = fillet(base, edges=[base.edge("lug.side(right)|lug.side(top)")], r=3)\n'
            'f2 = chamfer(base, edges=base.edges("lug.end", kind="outer"), d=0.8)\n')


@pytest.mark.parametrize("edit", [dict(), dict(extra_hole=True), dict(shuffle=True), dict(construction=True),
                                  dict(L=75, W=22), dict(extra_hole=True, shuffle=True, construction=True)])
def test_sketch_edits_keep_the_fillet_on_the_same_corner(edit):
    L, W = edit.get("L", 60), edit.get("W", 30)
    r = build(bracket(**edit))
    rounded = faces_of(r, "base", "f1(")
    assert rounded, sorted(r.bodies["base"].face_labels())
    c = centroid(rounded)
    # the right-top vertical corner of the lug, at (L/2, W/2), up the lug's height
    assert c[0] == pytest.approx(L / 2 - 0.9, abs=0.6) and c[1] == pytest.approx(W / 2 - 0.9, abs=0.6)
    assert 5 < c[2] < 13
    # the region names don't depend on the order the curves were written in, or the sizes
    names = sorted(set(r.bodies["s"].face_labels()))
    assert "profile" in names and "hole" in names


def test_a_step_added_early_keeps_later_references():
    tail = ('boss = cylinder(r=6, h=10, at=plate.anchor("+Z").offset(x=15))\n'
            'j = join(plate, [boss])\n'
            'f = fillet(plate, edges=[edge_between(plate.face("boss.side"), plate.face("+Z"))], r=1.5)\n'
            'h = hole(plate.face("boss.+Z").anchor(), d=3, depth=8)\n')
    head = 'plate = box(length=80, width=50, height=6, origin="center_xy")\n'
    plain = build(head + tail)
    early = build(head + 'c0 = chamfer(plate, edges=plate.edges("+Z", kind="outer"), d=1)\n' + tail)
    for r in (plain, early):
        wall = faces_of(r, "plate", "h.wall")
        assert wall and np.allclose(centroid(wall)[:2], (15, 0), atol=1e-6)
        assert faces_of(r, "plate", "f(")
    assert any(x.startswith("c0(") for x in early.bodies["plate"].face_labels())


def test_adding_a_hole_position_does_not_renumber_the_others():
    def run(xs):
        pos = ", ".join(f'plate.anchor("+Z").offset(x={x})' for x in xs)
        return build('plate = box(length=80, width=40, height=6, origin="center_xy")\n'
                     f'h = hole([{pos}], d=3)\n'
                     'f = fillet(plate, edges=plate.edges("+Z", kind="outer"), r=1)\n'
                     's = set_anchor(plate, "first", at=h.face("wall#0").anchor())\n')
    a = run((10, -10)).bodies["plate"].anchor("first").origin
    b = run((10, -10, -25)).bodies["plate"].anchor("first").origin
    c = run((10, 30, -25, -10)).bodies["plate"].anchor("first").origin
    assert a[0] == pytest.approx(b[0]) == pytest.approx(c[0])


def test_a_split_face_keeps_its_piece_number_when_another_piece_splits():
    src = ('plate = box(length=80, width=40, height=6, origin="center_xy")\n'
           'slot = box(length=4, width=60, height=4, at=plate.anchor("+Z").offset(x={x}).flipped(), origin="center_xy")\n'
           'c = cut(plate, [slot])\n'
           'right = set_anchor(plate, "right_top", at=plate.face("+Z#1").anchor())\n'
           '{more}'
           'again = set_anchor(plate, "right_top2", at=plate.face("+Z#1").anchor())\n')
    r = build(src.format(x=0, more=""))
    first = r.bodies["plate"].anchor("right_top").origin
    assert first[0] > 0
    # now cut the left piece in two as well: '+Z#1' must still be the right-hand piece
    more = ('slot2 = box(length=4, width=60, height=4, at=plate.anchor("+Z").offset(x=-20).flipped(), origin="center_xy")\n'
            'c2 = cut(plate, [slot2])\n')
    r = build(src.format(x=0, more=more))
    assert np.allclose(r.bodies["plate"].anchor("right_top2").origin, first)
    labels = set(r.bodies["plate"].face_labels())
    assert {"+Z#0", "+Z#1", "+Z#2"} <= labels


def test_outer_edges_skip_islands_left_by_engraving():
    # engraving 'AOB' leaves the middles of A, O and B as islands of the top face; the top's
    # outline is still just its rim, so chamfering it keeps working after the engraving is added
    for extra in ("", 'tx = text("AOB", size=8, at=plate.anchor("+Z"))\ne = extrude(tx, -0.5)\n'):
        r = build('plate = box(length=80, width=40, height=6, origin="center_xy")\n' + extra +
                  'f = chamfer(plate, edges=plate.edges("+Z", kind="outer"), d=0.5)\n')
        lo, hi = topo.bbox(r.bodies["plate"].shape)
        assert hi[2] == pytest.approx(6)
        assert len([1 for f, n in r.bodies["plate"].face_names if n.name.startswith("f(")]) == 4
