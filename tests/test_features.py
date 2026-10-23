"""Profiles, revolve, loft, sweep, shell and draft."""
import math

import numpy as np
import pytest

from openfusion import Engine, topo
from openfusion.export import printable, scene


ENGINE = Engine()          # shared: its cache keeps parts built by one test for the next


def build(src):
    return ENGINE.run_source("from openfusion import *\n" + src)


def ok(src):
    r = build(src)
    assert r.script_error is None, r.script_error
    bad = [x for x in r.results if x.status != "ok"]
    assert not bad, r.report()
    return r


def err(src, name):
    r = build(src)
    res = next(x for x in r.results if x.name == name)
    assert res.status == "error", r.report()
    return res.message


def vol(r, name):
    return topo.volume(r.bodies[name].shape)


# ----------------------------------------------------------------- profiles
def test_profiles_are_reference_sheets_never_exported():
    r = ok('p = profile(rect=(10, 5))\nc = profile(r=3, at=world.point(0, 20, 0))\n'
           'path = profile(points=[(0, 0), (30, 0), (30, 20)], closed=False)\nb = box(length=1, width=1, height=1)')
    for n in ("p", "c", "path"):
        assert r.bodies[n].role == "reference" and r.bodies[n].kind == "profile"
    assert [b.id for b in printable(r.bodies.values())] == ["b"]
    assert abs(topo.face_area_centroid(topo.faces(r.bodies["p"].shape)[0])[0] - 50) < 1e-9
    sc = {b["id"]: b for b in scene(r)["bodies"]}
    assert len(sc["path"]["edges"]) == 2 and not sc["path"]["faces"]       # paths are drawn as lines
    assert {a["name"] for a in sc["path"]["anchors"]} >= {"start", "end"}
    s = r.bodies["path"].anchor("start")
    assert np.allclose(s.origin, (0, 0, 0)) and np.allclose(s.z, (1, 0, 0))   # Z follows the path


def test_profile_input_errors():
    assert "exactly one" in err("p = profile(rect=(1, 1), r=2)", "p")
    assert "at least 3" in err("p = profile(points=[(0, 0), (1, 0)])", "p")


# ----------------------------------------------------------------- revolve
def test_revolve_about_profile_axis_world_axis_and_edges():
    # rectangle 4 x 6 with its left edge on the profile's Y axis: a ring of mean radius 2
    r = ok('p = profile(rect=(4, 6))\nring = revolve(p, axis="Y")')
    assert abs(vol(r, "ring") - 2 * math.pi * 2 * 24) < 1e-6
    # drawn on the XZ plane, spun about world Z
    r = ok('p = profile(points=[(10, 0), (14, 0), (14, 6), (10, 6)], at="XZ")\nring = revolve(p, axis="XY")')
    assert abs(vol(r, "ring") - 2 * math.pi * 12 * 24) < 1e-6
    # a half turn about a box's vertical edge, the other way round
    r = ok('post = box(length=2, width=2, height=20)\np = profile(points=[(6, 0), (10, 0), (10, 10), (6, 10)], at="XZ")\n'
           'half = revolve(p, axis=post.edge("-X-Y"), angle=-180)')
    assert abs(vol(r, "half") - math.pi * 8 * 40) < 1e-6
    assert {"start", "end"} <= set(r.bodies["half"].face_labels())


def test_revolve_cut_groove_into_its_own_body():
    r = ok('rod = cylinder(r=10, h=30)\n'
           'g = revolve(profile(rect=(2, 2), at="XZ" if False else rod.anchor("origin")), axis="Y") if False else None\n'
           'sec = profile(points=[(9, 14), (10, 14), (10, 16), (9, 16)], at="XZ")\n'
           'groove = revolve(sec, axis="XY", op="cut", target=rod)')
    expect = math.pi * 100 * 30 - 2 * math.pi * 9.5 * 2
    assert abs(vol(r, "rod") - expect) < 1e-6


def test_revolve_axis_must_lie_in_the_plane():
    assert "lie in the profile's plane" in err('p = profile(rect=(4, 6), at=world.point(10, 0, 0))\nbad = revolve(p, axis="XY")', "bad")


# ----------------------------------------------------------------- loft
def test_loft_between_a_face_and_a_profile_new_and_join():
    r = ok('base = box(length=20, width=20, height=5, origin="center_xy")\ntop = profile(r=4, at=world.point(0, 0, 20))\n'
           'neck = loft([base.face("+Z"), top])\nneck2 = loft([base.face("+Z"), top], op="join")')
    v = vol(r, "neck")
    assert 4 * 4 * math.pi * 15 < v < 400 * 15               # between the small and the large prism
    assert abs(vol(r, "base") - (2000 + v)) < 1e-3
    assert {"start", "end"} <= set(r.bodies["neck"].face_labels())


def test_loft_ruled_square_to_square_is_a_frustum():
    r = ok('a = profile(rect=(20, 20), origin="center")\nb = profile(rect=(10, 10), origin="center", at=world.point(0, 0, 12))\n'
           'f = loft([a, b], ruled=True)')
    A1, A2, h = 400, 100, 12
    assert abs(vol(r, "f") - h / 3 * (A1 + A2 + math.sqrt(A1 * A2))) < 1e-6


# ----------------------------------------------------------------- sweep
def test_sweep_along_a_drawn_path_from_either_end():
    for at in ("start", "end"):
        r = ok(f'path = profile(points=[(0, 0), (30, 0), (30, 20)], closed=False)\n'
               f'pipe = sweep(profile(r=2, at=path.anchor("{at}")), path)')
        assert abs(vol(r, "pipe") - math.pi * 4 * 50) < 1e-6
        assert topo.is_valid(r.bodies["pipe"].shape)


def test_sweep_along_solid_edges_and_loops():
    src = 'b = box(length=40, width=30, height=10)\n'
    r = ok(src + 'rail = sweep(profile(r=1, at=world.anchor("YZ").offset(y=10)), [b.edge("+X+Z"), b.edge("-Y+Z")])')
    assert abs(vol(r, "rail") - math.pi * 70) < 1e-3
    r = ok(src + 'frame = sweep(profile(rect=(2, 2), origin="center", at=world.anchor("YZ").offset(y=10)), b.face("+Z").edges())')
    assert abs(vol(r, "frame") - 4 * 140) < 1e-6           # mitred corners
    r = ok(src + 'g = sweep(profile(r=1, at=world.anchor("YZ").offset(y=10)), b.face("+Z").edges(), op="cut", target=b)')
    assert 12000 - vol(r, "b") == pytest.approx(math.pi / 4 * 140, rel=0.03)


def test_sweep_round_a_closed_loop_starts_where_the_section_is():
    """A section drawn anywhere across a circle, an ellipse or a rectangle (not only at the loop's
    seam or a corner) sweeps a whole ring: the loop is started where the section cuts it."""
    ring = ('c = sketch(points={"o": (0, 0)}, curves={"c1": ("circle", "o", 20)})\n'
            's = sketch(at=world.anchor("YZ"), points={"a": (20, 0), "b": (22.5, 1.5), "d": (18, 4), "e": (16, 0.5),'
            ' "f": (15, -6.5), "g": (24, -3)}, curves={"l1": ("line", "a", "b"), "l2": ("line", "b", "d"),'
            ' "l3": ("line", "d", "e"), "l4": ("line", "e", "f"), "l5": ("line", "f", "g"), "l6": ("line", "g", "a")})\n')
    P = np.array([(20, 0), (22.5, 1.5), (18, 4), (16, 0.5), (15, -6.5), (24, -3)])
    x, y = P[:, 0], P[:, 1]
    cr = x * np.roll(y, -1) - np.roll(x, -1) * y
    pappus = abs(cr.sum() / 2) * 2 * math.pi * abs(np.sum((x + np.roll(x, -1)) * cr) / (3 * cr.sum()))
    for path in ('[c.edge("c1")]', "c"):
        r = ok(ring + f"body1 = sweep(profile=s, path={path})")
        assert vol(r, "body1") == pytest.approx(pappus, rel=1e-9)
        assert topo.is_valid(r.bodies["body1"].shape) and len(r.bodies["body1"].face_labels()) == 6
    a, b = 30, 15
    h = ((a - b) / (a + b)) ** 2
    tube = math.pi * 4 * math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))
    el = 'el = sketch(points={"o": (0, 0)}, curves={"e": ("ellipse", "o", 30, 15)})\n'
    for plane, pt in (("YZ", "(15, 0)"), ("XZ", "(30, 0)"), ("YZ", "(-15, 0)")):
        r = ok(el + f'sec = sketch(at=world.anchor("{plane}"), points={{"o": {pt}}}, curves={{"c": ("circle", "o", 2)}})\n'
                    'ring = sweep(profile=sec, path=el)')
        assert vol(r, "ring") == pytest.approx(tube, rel=1e-5), (plane, pt)
    rect = ('r = sketch(points={"a": (-30, -20), "b": (30, -20), "c": (30, 20), "d": (-30, 20)}, curves={"l1": ("line", "a", "b"),'
            ' "l2": ("line", "b", "c"), "l3": ("line", "c", "d"), "l4": ("line", "d", "a")})\n')
    for off in (0, 10, -27):
        r = build(rect + f'sec = sketch(at=world.anchor("YZ").offset(z={off}), points={{"o": (-20, 0)}}, curves={{"c": ("circle", "o", 2)}})\n'
                         'frame = sweep(profile=sec, path=r)')
        res = r.results[-1]
        assert res.status == "ok" and not res.warnings, r.report()
        assert vol(r, "frame") == pytest.approx(math.pi * 4 * 200, rel=1e-6)


def test_sweep_section_along_the_path_is_explained():
    msg = err('b = box(length=40, width=30, height=10)\nrail = sweep(profile(r=1, at=world.point(0, 0, 10)), [b.edge("-Y+Z")])', "rail")
    assert "across the path" in msg


# ----------------------------------------------------------------- shell
@pytest.mark.parametrize("direction,expect", [("inside", 24000 - 36 * 26 * 18), ("outside", 44 * 34 * 22 - 24000)])
def test_shell_open_box(direction, expect):
    r = ok(f'b = box(length=40, width=30, height=20)\nshell(b, 2, faces=[b.face("+Z")], direction="{direction}")')
    assert abs(vol(r, "b") - expect) < 1e-6
    labels = set(r.bodies["b"].face_labels())
    assert {"+X", "-Z", "shell1.inner(+X)", "shell1.inner(-Z)"} <= labels
    # the outer +X face is still the outside one
    fx = r.bodies["b"].faces_named("+X")[0]
    x = topo.face_area_centroid(fx)[1][0]
    assert x == pytest.approx(40 if direction == "inside" else 42)


def test_shell_closed_and_filleted():
    r = ok('b = box(length=40, width=30, height=20)\nshell(b, 2)')
    assert abs(vol(r, "b") - (24000 - 36 * 26 * 16)) < 1e-6
    r = ok('cup = cylinder(r=10, h=20)\nfillet(cup, edges=["rim+Z"], r=3)\nshell(cup, 1.2, faces=[cup.face("-Z")])')
    assert topo.is_valid(r.bodies["cup"].shape) and 1000 < vol(r, "cup") < 2500
    # later steps can use the inner faces by name
    ok('b = box(length=40, width=30, height=20)\nshell(b, 2, faces=["+Z"])\n'
       'boss = cylinder(r=3, h=5, at=b.face("shell1.inner(-Z)").anchor())')


def test_shell_too_thick_fails_cleanly():
    for t in (5, 6):
        assert "walls would meet" in err(f'b = box(length=10, width=10, height=10)\ns = shell(b, {t}, faces=["+Z"])', "s")
    ok('b = box(length=10, width=10, height=10)\ns = shell(b, 4.9, faces=["+Z"])')
    # outside has no such limit
    ok('b = box(length=10, width=10, height=10)\ns = shell(b, 6, faces=["+Z"], direction="outside")')


# ----------------------------------------------------------------- draft
def test_draft_narrows_along_the_pull_direction_and_keeps_names():
    r = ok('b = box(length=40, width=30, height=20)\ndraft(b, faces=["+X", "-X", "+Y", "-Y"], angle=5, plane=b.face("-Z"))')
    b = r.bodies["b"]
    lo, hi = topo.bbox(b.shape)
    assert np.allclose(hi - lo, (40, 30, 20), atol=1e-6)        # the bottom keeps its size
    top = topo.face_area_centroid(b.faces_named("+Z")[0])[0]
    t = 20 * math.tan(math.radians(5))
    assert top == pytest.approx((40 - 2 * t) * (30 - 2 * t), rel=1e-6)
    assert sorted(b.face_labels()) == ["+X", "+Y", "+Z", "-X", "-Y", "-Z"]
    r = ok('b = box(length=40, width=30, height=20)\ndraft(b, faces=["+X"], angle=-5, plane=b.face("-Z"))')
    lo, hi = topo.bbox(r.bodies["b"].shape)
    assert hi[0] - lo[0] == pytest.approx(40 + 20 * math.tan(math.radians(5)))


def test_draft_rejects_faces_square_to_the_pull():
    assert "can't tilt" in err('b = box(length=40, width=30, height=20)\nd = draft(b, faces=["+Z"], angle=5, plane=b.face("-Z"))', "d")


# ----------------------------------------------------------------- shell on joined, lofted parts (a funnel)
FUNNEL = """cyl = cylinder(r=8, h=10)
top = box(length=60, width=40, height=5, origin="center_xy", at=world.point(0, 0, 50))
fillet(top, edges=["+X+Y", "+X-Y", "-X+Y", "-X-Y"], r=4)
{more}
neck = loft(sections=[cyl.face("+Z"), top.face("-Z")])
join(cyl, tools=[neck, top])
"""


def wall_probes(before, after, t, skip=(), n=4):
    """Check a shell by measuring it: from points on every original face (except openings), cast
    a ray inward and require the wall to end t deep, wherever the body is deep enough and not
    near another face or an opening. Returns the problems found."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepTools import BRepTools
    from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
    from OCP.TopAbs import TopAbs_IN, TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt, gp_Pnt2d
    skin = TopExp_Explorer(before.shape, TopAbs_SHELL).Current()
    holes = [f for f, nm in before.face_names if nm.label(before.id) in skip]
    ray = IntCurvesFace_ShapeIntersector()
    ray.Load(after.shape, 1e-7)
    bad, n_ok = [], 0
    for f, nm in before.face_names:
        if nm.label(before.id) in skip:
            continue
        a = BRepAdaptor_Surface(f)
        u0, u1, v0, v1 = BRepTools.UVBounds_s(f)
        for u in np.linspace(u0, u1, n + 2)[1:-1]:
            for v in np.linspace(v0, v1, n + 2)[1:-1]:
                if BRepClass_FaceClassifier(f, gp_Pnt2d(u, v), 1e-7).State() != TopAbs_IN:
                    continue
                p = topo.to_np(a.Value(u, v))
                nn = topo.face_normal_at(f, u, v)
                deep = gp_Pnt(*(p - 1.5 * t * nn))
                if BRepClass3d_SolidClassifier(before.shape, deep, 1e-7).State() != TopAbs_IN:
                    continue
                if BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(deep).Vertex(), skin).Value() < 1.2 * t:
                    continue            # near another face: the wall there is legitimately thicker
                vp = BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex()
                if any(BRepExtrema_DistShapeShape(vp, h).Value() < 2 * t for h in holes):
                    continue            # right at an opening's edge
                n_ok += 1
                ray.Perform(gp_Lin(gp_Pnt(*(p + 0.25 * t * nn)), gp_Dir(*(-nn))), 0, 3 * t)
                hits = sorted(ray.WParameter(i) - 0.25 * t for i in range(1, ray.NbPnt() + 1))
                hits = [h for h in hits if h > 0.02 * t]            # past the outer surface itself
                if not hits or abs(hits[0] - t) > 0.03 * t:
                    bad.append((nm.label(before.id), np.round(p, 2), "wall ends at", round(hits[0], 3) if hits else None))
    assert n_ok > 10, "too few probe points"
    return bad


@pytest.mark.slow
@pytest.mark.parametrize("more,opening", [
    ("", '[cyl.face("-Z")]'),
    ('fillet(top, edges=top.edges("+Z"), r=1.5)', '[cyl.face("top.+Z")]'),
    ("", '[cyl.face("-Z"), cyl.face("top.+Z")]'),
    ('fillet(top, edges=top.edges("+Z"), r=1.5)', None),
])
def test_shell_a_lofted_funnel(more, opening):
    src = FUNNEL.format(more=more) + (f"shell(cyl, 1, faces={opening})" if opening else "shell(cyl, 1)")
    r = ok(src)
    b = r.bodies["cyl"]
    assert topo.is_valid(b.shape) and len(topo.solids(b.shape)) == 1
    before = ok(FUNNEL.format(more=more)).bodies["cyl"]
    removed = [ref.split('"')[1] for ref in (opening or "").replace("[", "").replace("]", "").split(", ") if ref]
    assert not wall_probes(before, b, 1.0, removed)
    assert any(l.startswith("shell1.inner(") for l in b.face_labels())


SQUARE_TO_ROUND = """base = box(length=30, width=30, height=5, origin="center_xy")
ring = profile(r=8, at=world.point(0, 0, 35))
neck = loft(sections=[base.face("+Z"), ring])
join(base, tools=[neck])
"""


@pytest.mark.slow
@pytest.mark.parametrize("piece", [0, 2])
def test_shell_with_an_opening_in_a_curved_loft_side(piece):
    before = ok(SQUARE_TO_ROUND).bodies["base"]
    sides = sorted(l for l in before.face_labels() if l.startswith("neck.side"))
    assert len(sides) >= 4                         # the square's corners split the side
    r = ok(SQUARE_TO_ROUND + f'shell(base, 1.2, faces=[base.face("{sides[piece]}")])')
    b = r.bodies["base"]
    assert topo.is_valid(b.shape) and len(topo.solids(b.shape)) == 1
    assert not wall_probes(before, b, 1.2, [sides[piece]])


def test_shell_opening_that_would_cut_the_walls_apart_is_explained():
    msg = err(FUNNEL.format(more="") + 's = shell(cyl, 1, faces=[cyl.face("side")])', "s")
    assert "separate pieces" in msg


@pytest.mark.slow
def test_shell_a_loft_that_starts_on_a_rounded_slot():
    # the user's part: a cylinder, a slot-shaped box (r = half its width) and the loft between
    src = """cylinder1 = cylinder(r=20, h=40, at=world.point(40, 20, 0))
box1 = box(length=10, width=100, height=10, at=cylinder1.anchor("+Z").offset(x=-50, y=4), spin=270)
move(body=box1, dz=126)
fillet(body=box1, edges=[box1.edge("+X+Y"), box1.edge("-X+Y"), box1.edge("+X-Y"), box1.edge("-X-Y")], r=5)
loft1 = loft(sections=[box1.face("-Z"), cylinder1.face("+Z")])
join(target=cylinder1, tools=[box1, loft1])
"""
    before = ok(src).bodies["cylinder1"]
    assert sorted(l for l in before.face_labels() if "loft1" in l) == ["loft1.side"]     # one smooth face
    for t, opening in ((1.2, ["box1.+Z"]), (2, [])):
        faces = ", ".join(f'cylinder1.face("{o}")' for o in opening)
        b = ok(src + f"shell(cylinder1, {t}, faces=[{faces}])").bodies["cylinder1"]
        assert topo.is_valid(b.shape) and len(topo.solids(b.shape)) == 1
        assert not wall_probes(before, b, t, opening)


def test_shell_with_a_rounding_as_small_as_the_wall_works_or_explains():
    src = FUNNEL.format(more='fillet(top, edges=top.edges("+Z"), r=1)')
    r = build(src + 's = shell(cyl, 1, faces=[cyl.face("top.+Z")])')
    res = next(x for x in r.results if x.name == "s")
    if res.status == "error":
        assert "radius 1 mm is not bigger than the wall" in res.message
    else:
        before = ok(src).bodies["cyl"]
        assert topo.is_valid(r.bodies["cyl"].shape) and not wall_probes(before, r.bodies["cyl"], 1.0, ["top.+Z"])


@pytest.mark.slow
def test_shell_then_round_works_where_round_then_shell_cannot():
    ok(FUNNEL.format(more="") + 'shell(cyl, 1, faces=[cyl.face("-Z")])\nfillet(cyl, edges=[cyl.edge("top.+Z|top.+X")], r=0.5)')


# ----------------------------------------------------------------- smooth lofts, slivers, accurate volumes
def test_roundings_that_meet_exactly_leave_no_sliver_and_can_be_crossed():
    for r in (0.5, 0.4):
        src = f'w = box(length=20, width=1, height=10)\nfillet(w, edges=["+Y+Z", "-Y+Z"], r={r})\n'
        b = ok(src).bodies["w"]
        assert not [f for f in topo.faces(b.shape) if topo.face_area_centroid(f)[0] < 0.01]
        assert ("+Z" in b.face_labels()) == (r < 0.5)           # a full round has no flat top left
        for third in ('fillet(w, edges=[w.edge("+X+Y"), w.edge("+X-Y")], r=0.3)', 'fillet(w, edges=w.edges("+X"), r=0.2)'):
            assert topo.is_valid(ok(src + third).bodies["w"].shape)
    # the slot from the user's part: a 10 mm box with r=5 on its four vertical edges
    b = ok('s = box(length=10, width=100, height=10)\nfillet(s, edges=["+X+Y", "-X+Y", "+X-Y", "-X-Y"], r=5)').bodies["s"]
    assert len(topo.faces(b.shape)) == 8 and topo.volume(b.shape) == pytest.approx(90 * 10 * 10 + math.pi * 25 * 10, rel=1e-6)


def test_loft_through_smooth_sections_is_one_smooth_face():
    r = ok('slot = box(length=10, width=60, height=5, origin="center_xy")\n'
           'fillet(slot, edges=["+X+Y", "-X+Y", "+X-Y", "-X-Y"], r=5)\n'
           'ring = profile(r=12, at=world.point(0, 0, 60))\n'
           'l = loft(sections=[slot.face("+Z"), ring])\njoin(slot, tools=[l])')
    b = r.bodies["slot"]
    assert [x for x in b.face_labels() if x.startswith("l.side")] == ["l.side"]
    side = b.faces_named("l.side")[0]
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_C2
    a = BRepAdaptor_Surface(side)
    assert int(a.UContinuity()) >= int(GeomAbs_C2)          # no creases around the loft
    # sections with corners keep them (a square to a circle has four corner seams)
    r = ok('a = profile(rect=(20, 20), origin="center")\nb = profile(r=8, at=world.point(0, 0, 30))\nl = loft([a, b])')
    assert len([x for x in r.bodies["l"].face_labels() if x.startswith("side")]) >= 4


def test_volumes_of_free_form_and_offset_faces_match_a_fine_mesh():
    from openfusion.export import mesh_faces

    def mesh_volume(shape):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
        shape = BRepBuilderAPI_Copy(shape, True, False).Shape()     # a mesh of its own, like exports
        tot = 0.0
        for f, pos, nrm, tri in mesh_faces(shape, 0.002):
            a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
            tot += np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6
        return tot
    r = ok('slot = box(length=10, width=60, height=5, origin="center_xy")\n'
           'fillet(slot, edges=["+X+Y", "-X+Y", "+X-Y", "-X-Y"], r=5)\n'
           'ring = profile(r=12, at=world.point(0, 0, 60))\nl = loft(sections=[slot.face("+Z"), ring])\n'
           'c = copy(l)\nshell(c, 1.5)')
    for n in ("l", "c"):
        s = r.bodies[n].shape
        assert topo.volume(s) == pytest.approx(mesh_volume(s), rel=2e-3), n


USER_FUNNEL = """body1 = cylinder(r=20, h=40, at=world.point(0, 0, 0))
body2 = box(length=10, width=120, height=10, at=body1.anchor("+Z").offset(x=-40, y=5), spin=270)
extrude1 = extrude(face=body2.face("-Y"), distance=40)
move1 = move(body=body2, dz=150)
fillet1 = fillet(body=body2, edges=[body2.edge("-X|extrude1.end"), body2.edge("+X|extrude1.end"), body2.edge("-X+Y"), body2.edge("+X+Y")], r=5)
body3 = loft(sections=[body2.face("-Z"), body1.face("+Z")])
join1 = join(target=body1, tools=[body2, body3])
"""


@pytest.mark.slow
@pytest.mark.parametrize("t,opening", [(1.2, None), (1.0, "body2.+Z"), (0.8, None)])
def test_shell_the_users_funnel_at_any_thickness(t, opening):
    # 1.2 mm used to fail here while 1.0 and 1.5 worked: the hollow pieces met in rings that
    # differed by hundredths of a millimetre and the glue mishandled them at some thicknesses
    before = ok(USER_FUNNEL).bodies["body1"]
    faces = f'faces=[body1.face("{opening}")]' if opening else "faces=[]"
    b = ok(USER_FUNNEL + f"shell(body1, {t}, {faces})").bodies["body1"]
    assert topo.is_valid(b.shape) and len(topo.solids(b.shape)) == 1
    assert not wall_probes(before, b, t, [opening] if opening else [])


def test_shell_messages_tell_the_truth():
    msg = err(USER_FUNNEL + 's = shell(body1, 1, faces=[body1.face("side")])', "s")
    assert "separate pieces" in msg                     # decided from the faces, instantly
    from openfusion.features import _walls_fall_apart
    b = ok(USER_FUNNEL).bodies["body1"]
    assert not _walls_fall_apart(b, b.faces_named("body2.+Z"))


# ----------------------------------------------------------------- shells that don't depend on luck
USER_PART = """cylinder1 = cylinder(r=20, h=40, at=world.point(40, 20, 0))
box1 = box(length=10, width=100, height=10, at=cylinder1.anchor("+Z").offset(x=-30, y=5), spin=270)
move1 = move(body=box1, dz=126)
fillet1 = fillet(body=box1, edges=[box1.edge("+X+Y"), box1.edge("-X+Y"), box1.edge("+X-Y"), box1.edge("-X-Y")], r=5)
loft1 = loft(sections=[box1.face("-Z"), cylinder1.face("+Z")])
join1 = join(target=cylinder1, tools=[box1, loft1])
"""

CIRCLE_TO_SQUARE = """c = cylinder(r=20, h=20)
p = profile(rect=(20, 20), at=c.anchor("+Z").offset(z=30))
l = loft(sections=[c.face("+Z"), p])
join(c, tools=[l])
"""


def test_a_smooth_loft_side_is_light_and_meets_its_sections():
    # sections interpolated at shared parameters: a cubic of a few hundred points, not the
    # degree-14 surface of thousands that came from merging separately made curves
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    r = ok(FUNNEL.format(more="").replace("join(cyl, tools=[neck, top])\n", ""))
    side = r.bodies["neck"].faces_named("side")[0]
    s = BRepAdaptor_Surface(side).BSpline()
    assert s.UDegree() == 3 and s.NbUPoles() < 500
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    worst = 0.0
    for f in topo.faces(r.bodies["neck"].shape):
        if topo.surface_kind(f) != "plane":
            continue
        for e in topo.edges(f):
            ad = BRepAdaptor_Curve(e)
            for k in range(300):
                p = ad.Value(ad.FirstParameter() + (ad.LastParameter() - ad.FirstParameter()) * k / 299)
                v = BRepBuilderAPI_MakeVertex(p).Vertex()
                worst = max(worst, min(BRepExtrema_DistShapeShape(v, r.bodies[n].shape).Value() for n in ("cyl", "top")))
    assert worst < 5e-5                       # well inside the 2e-4 mm the joins tolerate


def test_a_lofts_inner_wall_is_a_loft_within_microns():
    from openfusion.loftshell import _loft_cavity_range
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepTools import BRepTools
    b = ok(USER_FUNNEL).bodies["body1"]
    loft = b.faces_named("body3.side")[0]
    lo, hi = topo.bbox(loft)
    cav = _loft_cavity_range(loft, 1.2, np.array([0, 0, 1.0]), lo[2] + 1.2, hi[2] - 1.2)
    side = [f for f in topo.faces(cav) if topo.surface_kind(f) == "bspline"][0]
    a = BRepAdaptor_Surface(side)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(side)
    for u in np.linspace(u0, u1, 37)[:-1]:
        for v in np.linspace(v0, v1, 9)[1:-1]:
            d = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(a.Value(u, v)).Vertex(), loft).Value()
            assert abs(d - 1.2) < 1e-3


@pytest.mark.parametrize("src,body,t,opening", [
    (CIRCLE_TO_SQUARE, "c", 1.2, None),          # failed at every thickness in 0.7.2
    pytest.param(CIRCLE_TO_SQUARE, "c", 0.8, "-Z", marks=pytest.mark.slow),
    pytest.param(USER_PART, "cylinder1", 1.2, None, marks=pytest.mark.slow),
    pytest.param(USER_PART, "cylinder1", 2.0, "box1.+Z", marks=pytest.mark.slow),
])
def test_shell_lofted_parts(src, body, t, opening):
    before = ok(src).bodies[body]
    faces = f'faces=[{body}.face("{opening}")]' if opening else "faces=[]"
    b = ok(src + f"shell({body}, {t}, {faces})").bodies[body]
    assert topo.is_valid(b.shape) and len(topo.solids(b.shape)) == 1
    assert not wall_probes(before, b, t, [opening] if opening else [])


@pytest.mark.parametrize("src,body,opening", [
    ('b = box(length=40, width=30, height=20)\n', "b", "+Z"),
    ('b = box(length=40, width=30, height=20)\nfillet(b, edges=b.edges(), r=3)\n', "b", None),   # one closed smooth skin
    ('b = box(length=40, width=30, height=20)\nc = box(length=20, width=40, height=30, at=world.point(10, -5, 10))\n'
     'cut(b, tools=[c])\n', "b", "+Z#0"),                                                      # inside corners
    pytest.param(SQUARE_TO_ROUND, "base", "neck.end", marks=pytest.mark.slow),                 # fading creases
])
def test_the_ball_sweep_fallback_on_its_own(src, body, opening):
    # what the shell falls back to when the kernel's offset can't do a body: tested directly
    import types
    from openfusion.sweepshell import sweep_shell
    before = ok(src).bodies[body]
    of = before.faces_named(opening) if opening else []
    res = sweep_shell(before.shape, 1.2, of)
    assert res is not None and topo.is_valid(res) and len(topo.solids(res)) == 1
    assert not wall_probes(before, types.SimpleNamespace(shape=res), 1.2, [opening] if opening else [])


def test_the_piece_by_piece_union_and_its_checks():
    # the shell's fallback when a union of many pieces fails all at once (it does on some
    # processors and not others, so it is tested directly)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from openfusion.sweepshell import _fuse_one, _gvol
    a = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10, 10, 10).Shape()
    b = BRepPrimAPI_MakeBox(gp_Pnt(5, 0, 0), 10, 10, 10).Shape()
    far = BRepPrimAPI_MakeBox(gp_Pnt(50, 0, 0), 1, 1, 1).Shape()
    u = _fuse_one(a, b)
    assert u is not None and _gvol(u) == pytest.approx(1500)
    u2 = _fuse_one(u, far)                      # pieces that don't touch (yet) are fine too
    assert u2 is not None and _gvol(u2) == pytest.approx(1501) and len(topo.solids(u2)) == 2
