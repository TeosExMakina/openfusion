"""Core behavior: names, anchors, placement, operations, roles, fallbacks."""
import math

import numpy as np
import pytest

from openfusion import Engine, topo

ENGINE = Engine()

HEADER = "from openfusion import *\n"


def build(src, **overrides):
    r = ENGINE.run_source(HEADER + src, overrides)
    return r


def ok(src, **overrides):
    r = build(src, **overrides)
    assert r.script_error is None, r.script_error
    bad = [x for x in r.results if x.status not in ("ok",)]
    assert not bad, r.report()
    return r


def close(a, b, tol=1e-6):
    return np.allclose(np.asarray(a, float), np.asarray(b, float), atol=tol)


# ----------------------------------------------------------------- primitives
def test_box_names_and_anchors():
    r = ok("b = box(length=10, width=20, height=30)")
    b = r.bodies["b"]
    assert sorted(b.face_labels()) == sorted(["+X", "-X", "+Y", "-Y", "+Z", "-Z"])
    top = b.anchor("+Z")
    assert close(top.origin, (5, 10, 30)) and close(top.z, (0, 0, 1)) and close(top.x, (1, 0, 0))
    front = b.anchor("-Y")
    assert close(front.origin, (5, 0, 15)) and close(front.y, (0, 0, 1)), "side faces: Y up"
    right = b.anchor("+X")
    assert close(right.x, (0, 1, 0)) and close(right.y, (0, 0, 1))
    assert close(b.anchor("+X+Z").origin, (10, 10, 30))
    assert close(b.anchor("+-+").origin, (10, 0, 30))
    assert len(b.edges_named("+X+Z")) == 1
    assert close(b.vertex_named("+++"), (10, 20, 30))


def test_box_origins():
    r = ok("a = box(length=10, width=20, height=30, origin='center')\n"
           "c = box(length=10, width=20, height=30, origin='center_xy')")
    assert close(r.bodies["a"].anchor("+Z").origin, (0, 0, 15))
    assert close(r.bodies["c"].anchor("-Z").origin, (0, 0, 0))


def test_cylinder_angle_zero_and_side():
    r = ok("c = cylinder(r=5, h=10, spin=90)")
    c = r.bodies["c"]
    assert sorted(c.face_labels()) == ["+Z", "-Z", "side"]
    s0 = c.anchor("side")
    assert close(s0.origin, (0, 5, 5)), "spin=90 rotates angle 0 to world +Y"
    s90 = c.anchor("side", angle=90)
    assert close(s90.origin, (-5, 0, 5)), "angles counterclockwise about +Z"
    assert close(s0.y, (0, 0, 1)), "side anchor: Y up the axis"
    assert len(c.edges_named("rim+Z")) == 1


def test_cone_sphere_torus():
    r = ok("k = cone(r1=5, r2=2, h=10)\ns = sphere(r=4)\nt = torus(R=10, r=2)")
    k, s, t = r.bodies["k"], r.bodies["s"], r.bodies["t"]
    assert close(k.anchor("rim+Z").origin, (2, 0, 10))
    assert close(s.anchor("surface", lon=90, lat=0).origin, (0, 4, 0))
    assert close(s.anchor("+Z").origin, (0, 0, 4))
    assert close(t.anchor("tube", theta=90).origin, (0, 10, 0))
    assert close(t.anchor("surface", theta=0, phi=90).origin, (10, 0, 2))
    assert abs(topo.volume(t.shape) - 2 * math.pi ** 2 * 10 * 4) < 1e-3


def test_polygon_modes():
    r = ok("p = polygon(n=6, r=10, h=5)\nq = polygon(n=6, a=10, h=5)")
    p, q = r.bodies["p"], r.bodies["q"]
    assert close(p.anchor("v0").origin, (10, 0, 0)), "circumradius mode: v0 at angle 0"
    assert close(p.anchor("v1").origin, (10 * math.cos(math.pi / 3), 10 * math.sin(math.pi / 3), 0))
    assert close(q.anchor("s0").origin, (10, 0, 2.5)), "apothem mode: s0 midpoint at angle 0"
    assert set(p.face_labels()) == {"+Z", "-Z", "s0", "s1", "s2", "s3", "s4", "s5"}
    # side face s0 really lies between v0 and v1
    f = p.faces_named("s0")[0]
    c = topo.face_area_centroid(f)[1]
    mid = (p.anchor("v0").origin + p.anchor("v1").origin) / 2
    assert close(c[:2], mid[:2], 1e-6)


def test_coil():
    r = ok("c = coil(R=10, turns=3, pitch=6, size=3)")
    c = r.bodies["c"]
    assert set(c.face_labels()) == {"start", "end", "surface"}
    assert close(c.anchor("start").origin, (10, 0, 0))
    assert close(c.anchor("end").origin, (10, 0, 18), 1e-6)
    assert topo.is_valid(c.shape)
    vol = math.pi * 1.5 ** 2 * 3 * math.hypot(2 * math.pi * 10, 6)
    assert abs(topo.volume(c.shape) - vol) / vol < 0.01


def test_coil_rejects_self_intersection():
    r = build("c = coil(R=10, turns=3, pitch=2, size=3)")
    assert r.results[0].status == "error" and "self-intersects" in r.results[0].message


# ----------------------------------------------------------------- placement
def test_place_mate_flush_gap_angle():
    r = ok("a = box(length=20, width=20, height=10, origin='center_xy')\n"
           "b = cylinder(r=3, h=8)\n"
           "place(b.anchor('-Z'), onto=a.anchor('+Z'), gap=1, offset=(2, 3), angle=90)")
    b = r.bodies["b"]
    assert close(b.anchor("-Z").origin, (2, 3, 11))
    assert close(b.anchor("+Z").z, (0, 0, 1))
    assert close(b.anchor("side").origin, (2, 3 + 3, 15)), "angle=90 spins angle 0 to +Y"
    r = ok("a = box(length=20, width=20, height=10)\nb = box(length=2, width=2, height=4)\n"
           "place(b.anchor('+Z'), onto=a.anchor('+Z'), mode='flush')")
    assert close(r.bodies["b"].anchor("+Z").origin, r.bodies["a"].anchor("+Z").origin)
    assert close(r.bodies["b"].anchor("-Z").origin[2], 6)


def test_place_follows_parameters():
    src = ("params(h=10)\na = box(length=20, width=20, height=P.h)\nb = sphere(r=2)\n"
           "place(b.anchor('-Z'), onto=a.anchor('+Z'))")
    for h in (5.0, 10.0, 33.0):
        r = ok(src, h=h)
        assert close(r.bodies["b"].anchor("-Z").origin, (10, 10, h))


def test_place_on_bed_and_world_moves():
    r = ok("b = box(length=10, width=10, height=10, at=world.point(5, 5, 50))\n"
           "rotate(b, 90, axis='X')\nplace_on_bed(b, face='+Y')")
    b = r.bodies["b"]
    lo, hi = topo.bbox(b.shape)
    assert abs(lo[2]) < 1e-6, "face is on the bed"
    assert close(b.anchor("+Y").origin, (0, 0, 0))
    r = ok("b = box(length=1, width=1, height=1)\nrotate(b, 90)\nmove(b, dx=5)\nmove(b, dx=5, frame='world')")
    assert close(r.bodies["b"].anchor("origin").origin, (5, 5, 0)), "local dx follows the rotated body X"
    r = ok("b = box(length=1, width=1, height=1)\nmove(b, to=(10, 20, 30), point=b.anchor('center'))")
    assert close(r.bodies["b"].anchor("center").origin, (10, 20, 30))


# ----------------------------------------------------------------- booleans and naming
def test_names_survive_cut_and_split():
    r = ok("a = box(length=30, width=10, height=10)\n"
           "s = box(length=4, width=20, height=20, at=world.point(13, -5, 5))\n"
           "cut(a, tools=[s])")
    a = r.bodies["a"]
    labels = a.face_labels()
    assert "+Z#0" in labels and "+Z#1" in labels, labels
    assert len(a.faces_named("+Z")) == 2, "bare name returns every piece"
    # pieces ordered along local X
    c0 = topo.face_area_centroid(a.faces_named("+Z#0")[0])[1]
    c1 = topo.face_area_centroid(a.faces_named("+Z#1")[0])[1]
    assert c0[0] < c1[0]
    assert r.bodies["s"].role == "removed"
    # design anchor unaffected by the split
    assert close(a.anchor("+Z").origin, (15, 5, 10))


def test_keep_tools_reference():
    r = ok("a = box(length=10, width=10, height=10)\nt = sphere(r=3, at=a.anchor('+Z'))\n"
           "cut(a, tools=[t], keep_tools='reference')")
    assert r.bodies["t"].role == "reference"
    assert "t.surface" in r.bodies["a"].face_labels()


def test_intersect_and_join():
    r = ok("a = box(length=10, width=10, height=10, origin='center')\ns = sphere(r=6)\nintersect(a, tools=[s])")
    v = topo.volume(r.bodies["a"].shape)
    assert 0 < v < 1000
    r = ok("a = box(length=10, width=10, height=10)\nb = box(length=10, width=10, height=10, at=world.point(5,0,0))\n"
           "join(a, tools=[b])")
    assert abs(topo.volume(r.bodies["a"].shape) - 1500) < 1e-6


def test_fillet_and_chamfer_names():
    r = ok("a = box(length=20, width=20, height=10)\nfillet(a, edges=['+X+Z', '-X+Z'], r=2)\n"
           "chamfer(a, edges=[a.edge('+Y+Z')], d=1)")
    labels = r.bodies["a"].face_labels()
    assert "fillet1(+X+Z)" in labels and "fillet1(-X+Z)" in labels and "chamfer1(+Y+Z)" in labels, labels


def test_fillet_failure_is_reported_and_downstream_skipped():
    r = build("a = box(length=4, width=4, height=4)\nfillet(a, edges=['+X+Z'], r=10)\nmove(a, dx=1)\n"
              "b = box(length=1, width=1, height=1)")
    st = {x.name: x.status for x in r.results}
    assert st["fillet1"] == "error" and st["move1"] == "skipped" and st["b"] == "ok"


def test_extrude_face():
    r = ok("a = box(length=10, width=10, height=10)\next1 = extrude(a.face('+Z'), 5)\n"
           "ext2 = extrude(a.face('-X'), -2)")
    a = r.bodies["a"]
    # coplanar faces are merged after the join, so -X is the whole 10 x 15 side
    assert abs(topo.volume(a.shape) - (1500 - 2 * 10 * 15)) < 1e-6
    assert a.faces_named("ext1.end")
    r = ok("a = box(length=10, width=10, height=10)\next1 = extrude(a.face('+Z'), 5, op='new')")
    assert close(r.bodies["ext1"].anchor("end").origin, (5, 5, 15))


# ----------------------------------------------------------------- patterns / mirror / scale
def test_patterns():
    r = ok("p = cylinder(r=1, h=2)\ng = pattern_rect(p, x=(3, 5), y=(2, -4))")
    ids = sorted(k for k in r.bodies if k.startswith("g_"))
    assert len(ids) == 5
    assert close(r.bodies["g_5"].anchor("origin").origin, (10, -4, 0))
    r = ok("p = cylinder(r=1, h=2, at=world.point(10, 0, 0))\ng = pattern_circ(p, count=4)")
    assert close(r.bodies["g_1"].anchor("origin").origin, (0, 10, 0))
    r = ok("p = cylinder(r=1, h=2, at=world.point(10, 0, 0))\ng = pattern_circ(p, count=3, angle=90)")
    assert close(r.bodies["g_2"].anchor("origin").origin, (0, 10, 0))


BLOCK = """block = box(length=60, width=40, height=10, origin="center_xy")
turn = rotate(block, 30)
hole = cylinder(r=1.5, h=-6, at=block.anchor("+Z").offset(x=-24, y=-14))
pin = cylinder(r=4, h=6, at=block.anchor("+Z").offset(x=12, y=6))
"""


def _block_offsets(r, group, seed, n):
    fr = r.bodies["block"].frame
    s0 = fr.to_local(r.bodies[seed].frame.origin)
    return [np.round(fr.to_local(r.bodies[f"{group}_{k}"].frame.origin) - s0, 6) for k in range(1, n + 1)]


def test_pattern_along_edges_of_a_rotated_block():
    # directions come from the block's edges, not the seed's or the world's axes
    r = ok(BLOCK + 'g = pattern_rect(hole, x=(3, 10), y=(2, 8), along=block.edge("-Y+Z"))')
    offs = _block_offsets(r, "g", "hole", 5)
    assert [list(o) for o in offs] == [[0, 8, 0], [10, 0, 0], [10, 8, 0], [20, 0, 0], [20, 8, 0]]
    # a cutter grown into the face (flipped Z) and a post on it pattern the same way: onto the face
    r = ok(BLOCK + 'g = pattern_rect(pin, x=(2, 10), y=(2, 8), along=block.edge("-Y+Z"))')
    assert [list(o) for o in _block_offsets(r, "g", "pin", 3)] == [[0, 8, 0], [10, 0, 0], [10, 8, 0]]


def test_pattern_negative_spacing_flips_each_direction():
    r = ok(BLOCK + 'g = pattern_rect(hole, x=(2, -10), y=(2, -8), along=block.edge("+Y+Z"))')
    # "+Y+Z" runs along the block's X as well: it points +X, so -10 goes -X
    assert [list(o) for o in _block_offsets(r, "g", "hole", 3)] == [[0, -8, 0], [-10, 0, 0], [-10, -8, 0]]


def test_pattern_second_edge_and_world_vs_local():
    r = ok(BLOCK + 'g = pattern_rect(hole, x=(2, 10), y=(2, 5), along=block.edge("-Y+Z"), y_along=block.edge("-X+Z"))')
    assert [list(o) for o in _block_offsets(r, "g", "hole", 3)] == [[0, 5, 0], [10, 0, 0], [10, 5, 0]]
    r = ok(BLOCK + 'g = pattern_rect(hole, x=(2, 10), frame="world")')
    d = r.bodies["g_1"].frame.origin - r.bodies["hole"].frame.origin
    assert close(d, (10, 0, 0))
    with pytest.raises(AssertionError):
        ok(BLOCK + 'g = pattern_rect(hole, x=(2, 10), along=block.edge("-Y+Z"), y_along=block.edge("+Y+Z"))')


def test_pattern_circ_about_edges_and_round_faces():
    # about the pin's axis (round face or its rim): right-hand rule about the block's +Z
    for axis in ('pin.face("side")', 'pin.edge("rim+Z")', 'pin.edge("rim-Z")'):
        r = ok(BLOCK + f"c = pattern_circ(hole, count=4, axis={axis})")
        o = _block_offsets(r, "c", "hole", 1)[0]
        # seed is (-36, -20) from the pin in block coordinates; +90 deg turns it to (20, -36)
        assert close(o, (20 + 36, -36 + 20, 0))
    r = ok(BLOCK + 'c = pattern_circ(hole, count=4, axis=pin.face("side"), angle=-270)')
    assert close(_block_offsets(r, "c", "hole", 1)[0], (-20 + 36, 36 + 20, 0))   # -90 deg: the other way
    r = ok(BLOCK + 'c = pattern_circ(hole, count=2, axis=block.edge("+X+Y"), angle=180)')
    # about the vertical corner edge at (30, 20): seed (-24, -14) maps to (84, 54)
    assert close(_block_offsets(r, "c", "hole", 1)[0], (84 + 24, 54 + 14, 0))


def test_pattern_reference_errors():
    r = Engine().run_source("from openfusion import *\n" + BLOCK + 'c = pattern_circ(hole, count=3, axis=block.face("+Z"))')
    msg = next(x for x in r.results if x.name == "c").message
    assert "flat" in msg


def test_pattern_group_as_tools_and_index():
    r = ok("a = box(length=30, width=10, height=5)\np = cylinder(r=1, h=5, at=world.point(5, 5, 0))\n"
           "g = pattern_rect(p, x=(3, 10))\ncut(a, tools=[g])\nb = sphere(r=1, at=g[2].anchor('+Z'))")
    assert close(r.bodies["b"].anchor("center").origin, (25, 5, 5))
    assert len([l for l in r.bodies["a"].face_labels() if l.endswith("side")]) == 3


def test_mirror_named_as_if_from_scratch():
    r = ok("a = box(length=10, width=20, height=30)\nm = mirror(a, plane='YZ')")
    a, m = r.bodies["a"], r.bodies["m"]
    # geometry is reflected
    lo, hi = topo.bbox(m.shape)
    assert close(lo, (-10, 0, 0)) and close(hi, (0, 20, 30))
    # frame is right-handed
    f = m.frame
    assert np.linalg.det(np.vstack([f.x, f.y, f.z])) > 0
    # +X and +Z names correspond to the reflected originals
    assert close(m.anchor("+X").origin, (-10, 10, 15))
    assert close(m.anchor("+Z").origin, (-5, 10, 30))
    # Y names swap: mirror's +Y is the reflection of the original's -Y
    assert close(m.anchor("+Y").origin, (-5, 0, 15)) and close(a.anchor("-Y").origin, (5, 0, 15))
    # face names agree with anchors
    face = m.faces_named("+Y")[0]
    assert close(topo.face_area_centroid(face)[1], m.anchor("+Y").origin)
    for name in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
        n = topo.plane_normal(m.faces_named(name)[0])
        assert close(n, m.anchor(name).z), name


def test_mirror_angles_and_polygon_indices():
    r = ok("c = cylinder(r=5, h=2)\nmc = mirror(c, plane='XZ')\np = polygon(n=5, r=10, h=2)\n"
           "mp = mirror(p, plane='XZ')")
    mc = r.bodies["mc"]
    # mirroring across XZ maps angle 0 (+X) to +X; angle 90 (+Y) of the original lands at 270
    assert close(mc.anchor("side", angle=0).origin, (5, 0, 1))
    o270 = r.bodies["c"].anchor("side", angle=270).origin
    assert close(mc.anchor("side", angle=90).origin, o270 * np.array([1, -1, 1]))
    mp = r.bodies["mp"]
    for i in range(5):
        f = mp.faces_named(f"s{i}")[0]
        assert close(topo.face_area_centroid(f)[1], mp.anchor(f"s{i}").origin), f"s{i}"


def test_scale():
    r = ok("a = box(length=10, width=10, height=10)\nscale(a, 2)")
    assert abs(topo.volume(r.bodies["a"].shape) - 8000) < 1e-6
    assert close(r.bodies["a"].anchor("+Z").origin, (10, 10, 20))
    r = ok("a = box(length=10, width=10, height=10, origin='center')\nscale(a, (2, 1, 1), about=a.anchor('center'))")
    assert close(r.bodies["a"].anchor("+X").origin, (10, 0, 0))


# ----------------------------------------------------------------- anchors, roles, fallback
def test_set_anchor_reassigns_from_that_step():
    r = ok("a = box(length=10, width=10, height=10)\nb = sphere(r=1)\nc = sphere(r=1)\n"
           "place(b.anchor('center'), onto=a.anchor('+Z'), mode='flush')\n"
           "set_anchor(a, '+Z', at=a.anchor('+Z').offset(x=3))\n"
           "place(c.anchor('center'), onto=a.anchor('+Z'), mode='flush')")
    assert close(r.bodies["b"].anchor("center").origin, (5, 5, 10))
    assert close(r.bodies["c"].anchor("center").origin, (8, 5, 10))


def test_custom_anchor_moves_with_body():
    r = ok("a = box(length=10, width=10, height=10)\nset_anchor(a, 'mount', at=a.anchor('+X+Z'))\n"
           "move(a, dz=5, frame='world')")
    assert close(r.bodies["a"].anchor("mount").origin, (10, 5, 15))


def test_face_points_on_simple_faces():
    r = ok("a = box(length=20, width=10, height=5)\nh = cylinder(r=1, h=5, at=world.point(5, 5, 0))\n"
           "cut(a, tools=[h])\ns = sphere(r=0.5, at=a.face('+Z').hole(0))\nt = sphere(r=0.5, at=a.face('+Z').corner(0))\n"
           "u = sphere(r=0.5, at=a.face('+Z').mid(0))")
    assert close(r.bodies["s"].anchor("center").origin, (5, 5, 5))
    # corner(0): nearest angle 0 of the face frame (X = +X) from the face center, CCW about +Z
    assert close(r.bodies["t"].anchor("center").origin, (20, 0, 5))
    assert close(r.bodies["u"].anchor("center").origin, (20, 5, 5))


def test_fallback_is_flagged_and_strict_errors():
    src = ("a = box(length=20, width=10, height=5)\n"
           "c = cylinder(r=3, h=10, at=world.point(20, 5, -1))\ncut(a, tools=[c])\n")
    r = build(src + "s = sphere(r=0.5, at=a.face('+Z').corner(7))")
    res = {x.name: x for x in r.results}
    assert res["s"].status == "warning" and "fell back" in res["s"].warnings[0]
    assert close(r.bodies["s"].anchor("center").origin, r.bodies["a"].anchor("+Z").origin)
    r = build(src + "s = sphere(r=0.5, at=a.face('+Z').corner(7).strict())")
    assert {x.name: x for x in r.results}["s"].status == "error"


def test_removed_body_keeps_design_anchors_but_not_geometry():
    r = build("a = box(length=10, width=10, height=10)\nremove(a)\n"
              "b = sphere(r=1, at=a.anchor('+Z'))\nmove(a, dx=1)")
    st = {x.name: x.status for x in r.results}
    assert st["b"] == "ok" and st["move1"] == "error"
    assert close(r.bodies["b"].anchor("center").origin, (5, 5, 10))


def test_reference_bodies():
    r = ok("g = box(length=50, width=2, height=20, role='reference')\nb = box(length=5, width=5, height=5)\n"
           "place(b.anchor('-Y'), onto=g.anchor('+Y'))")
    assert r.bodies["g"].role == "reference"
    assert r.solids() == [r.bodies["b"]]
    assert close(r.bodies["b"].anchor("-Y").origin, (25, 2, 10))


def test_script_error_reports_line():
    r = build("a = box(length=1, width=1, height=1)\nb = nope()")
    assert r.script_error and r.script_error_line == 3


def test_errors_have_clear_messages():
    r = build("a = box(length=-1, width=1, height=1)")
    assert "positive" in r.results[0].message
    r = build("a = box(length=1, width=1, height=1)\nb = sphere(r=1, at=a.anchor('nope'))")
    assert "Available" in r.results[1].message


def test_cut_that_misses_warns():
    r = build("a = box(length=10, width=10, height=10)\nc = cylinder(r=2, h=4, at=a.anchor('-Y'))\ncut(a, tools=[c])")
    res = {x.name: x for x in r.results}["cut1"]
    assert res.status == "warning" and "flipped" in res.warnings[0]
    r = ok("a = box(length=10, width=10, height=10)\nc = cylinder(r=2, h=4, at=a.anchor('-Y').flipped())\ncut(a, tools=[c])")
    assert abs(topo.volume(r.bodies["a"].shape) - (1000 - math.pi * 4 * 4)) < 1e-6


# ----------------------------------------------------------------- fillets in any order
CUBE = "b = box(length=20, width=20, height=20)\n"
V, T, B = "['+X+Y','+X-Y','-X+Y','-X-Y']", "['+X+Z','-X+Z','+Y+Z','-Y+Z']", "['+X-Z','-X-Z','+Y-Z','-Y-Z']"


@pytest.mark.parametrize("steps", [
    "fillet(b, edges=b.edges(), r=3)",
    f"fillet(b, edges={T}, r=3)\nfillet(b, edges={V}, r=3)\nfillet(b, edges={B}, r=3)",
    f"fillet(b, edges={B}, r=3)\nfillet(b, edges={T}, r=3)\nfillet(b, edges={V}, r=3)",
    f"fillet(b, edges={V}, r=3)\nfillet(b, edges=b.edges('+Z', kind='outer'), r=3)\nfillet(b, edges=b.edges('-Z', kind='outer'), r=3)",
])
def test_fully_rounded_cube_in_any_order(steps):
    r = build(CUBE + steps)
    assert all(x.status == "ok" for x in r.results), r.report()
    sh = r.bodies["b"].shape
    assert topo.is_valid(sh) and len(topo.faces(sh)) == 26          # 6 flats, 12 rounds, 8 corner balls
    # exact volume of a 20 mm cube with r=3 rounded edges and corners
    a, rr = 20 - 6, 3
    exact = a ** 3 + 6 * a * a * rr + 3 * math.pi * rr * rr * a + 4 / 3 * math.pi * rr ** 3
    assert abs(topo.volume(sh) - exact) < 1e-3


def test_fillet_one_edge_at_a_time_skips_chained_edges():
    edges = ['+X+Z', '+X+Y', '+X-Y', '-X+Y', '-X-Y', '-X+Z', '+Y+Z', '-Y+Z', '+X-Z', '-X-Z', '+Y-Z', '-Y-Z']
    r = build(CUBE + "\n".join(f"fillet(b, edges=['{e}'], r=3)" for e in edges))
    assert all(x.status in ("ok", "warning") for x in r.results), r.report()
    assert any("already rounded" in w for x in r.results for w in x.warnings)
    assert len(topo.faces(r.bodies["b"].shape)) == 26


def test_fillet_too_large_is_an_error_not_a_broken_solid():
    r = build(CUBE + "fillet(b, edges=b.edges(), r=11)")
    assert r.results[1].status == "error" and "too large" in r.results[1].message


def test_move_rotations_about_pivot_and_point_to_point():
    r = ok("b = box(length=10, width=4, height=2)\nmove(b, rz=90)")
    assert close(r.bodies["b"].anchor("+X").origin, (-2, 10, 1))   # rotated about the body origin
    r = ok("b = box(length=10, width=4, height=2)\nmove(b, rz=90, pivot=b.anchor('center'), dz=5)")
    c = r.bodies["b"].anchor("center").origin
    assert close(c, (5, 2, 6))                                     # center stays put, then lifted 5
    assert close(r.bodies["b"].anchor("+X").z, (0, 1, 0))
    r = ok("a = box(length=20, width=20, height=5)\nb = box(length=2, width=2, height=2)\n"
           "move(b, point=b.anchor('-Z'), to=a.face('+Z').corner(2))")
    assert close(r.bodies["b"].anchor("-Z").origin, (0, 20, 5))      # corner(2) of the top face is (0, 20, 5)
    r = ok("b = box(length=2, width=2, height=2)\nmove(b, to=(10, 0, 0), frame='world', rx=90)")
    assert close(r.bodies["b"].anchor("origin").origin, (10, 0, 0))


# ----------------------------------------------------------------- exact-fit fillets, negative heights
LEDGE = ("base = box(length=20, width=20, height=5, origin='center_xy')\n"
         "ledge = box(length=10, width=20, height=1, origin='center_xy', at=base.anchor('+Z').offset(x=-5))\n"
         "join(base, tools=[ledge])\n")


@pytest.mark.parametrize("steps", [
    "fillet(base, edges=['ledge.+X|ledge.+Z', '+Z|ledge.+X'], r=0.5)",
    "fillet(base, edges=['ledge.+X|ledge.+Z'], r=0.5)\nfillet(base, edges=['+Z|ledge.+X'], r=0.5)",
    "chamfer(base, edges=['ledge.+X|ledge.+Z', '+Z|ledge.+X'], d=0.5)",
])
def test_fillets_that_meet_exactly_on_a_wall(steps):
    r = build(LEDGE + steps)
    assert all(x.status == "ok" for x in r.results), r.report()
    assert topo.is_valid(r.bodies["base"].shape)


def test_negative_heights_flip_the_primitive():
    r = ok("p = box(length=40, width=40, height=10)\n"
           "a = box(length=6, width=4, height=-3, at=p.anchor('+Z').offset(x=5, y=5))\n"
           "c = cylinder(r=2, h=-10, at=p.anchor('+Z'))\n"
           "k = polygon(n=6, r=3, h=-2, at=p.anchor('+Z').offset(x=-10))")
    a, c = r.bodies["a"], r.bodies["c"]
    lo, hi = topo.bbox(a.shape)
    assert close(lo, (25, 25, 7)) and close(hi, (31, 29, 10)), "footprint stays where it was drawn, grows down"
    assert close(a.anchor("+Z").z, (0, 0, -1)), "the primitive's own +Z now points into the part"
    assert close(a.anchor("-Z").origin[2], 10), "its base sits on the face it was built on"
    lo, hi = topo.bbox(c.shape)
    assert close(lo[2], 0) and close(hi[2], 10)
    assert close(c.anchor("side").origin, (22, 20, 5)), "angle 0 still points along +X"
    assert close(topo.bbox(r.bodies["k"].shape)[0][2], 8)
    r = ok("p = box(length=40, width=40, height=10)\n"
           "h = cylinder(r=2, h=-4, at=p.anchor('+Z'))\ncut(p, tools=[h])")
    assert abs(topo.volume(r.bodies["p"].shape) - (16000 - math.pi * 4 * 4)) < 1e-6
