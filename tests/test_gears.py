"""Gears (involute spur, helical, herringbone, ring gears, racks; in sketches and as bodies) and
thread profiles for coils."""
import math

import numpy as np
import pytest

from openfusion import Engine, gears, topo

ENGINE = Engine()


def build(src):
    return ENGINE.run_source("from openfusion import *\n" + src)


def ok(src):
    r = build(src)
    bad = [f"{x.name}: {x.message}" for x in r.results if x.status not in ("ok", "warning")]
    assert not r.script_error and not bad, (r.script_error, bad)
    return r


def err(src, name):
    r = build(src)
    res = next(x for x in r.results if x.name == name)
    assert res.status == "error", r.report()
    return res.message


def area(segs):
    pts = np.array(gears.sample(segs, per_arc_deg=0.5))
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def test_involute_profile_geometry():
    m, z = 2, 20
    segs = gears.spur(m, z)
    pts = np.array(gears.sample(segs))
    rho = np.hypot(pts[:, 0], pts[:, 1])
    assert rho.max() == pytest.approx(m * z / 2 + m, abs=1e-9)                # tip circle
    assert rho.min() == pytest.approx(m * z / 2 - 1.25 * m, abs=1e-9)         # root circle
    # the segments join end to end and close
    ends = []
    for s in segs:
        if s[0] == "line":
            ends.append((s[1], s[2]))
        elif s[0] == "curve":
            ends.append((s[1][0], s[1][-1]))
        else:
            r, a0, a1 = s[1:]
            ends.append(((r * math.cos(a0), r * math.sin(a0)), (r * math.cos(a1), r * math.sin(a1))))
    for (_, q), (p, _) in zip(ends, ends[1:] + ends[:1]):
        assert np.allclose(p, q, atol=1e-9)
    # tooth thickness at the pitch circle is half the circular pitch: the outline crosses the
    # pitch circle 2z times, and the arcs of tooth and space there are equal
    r = m * z / 2
    th = gears.spur(m, z)
    flank = [s for s in th if s[0] == "curve"][0][1]
    # (the involute on the pitch circle sits at angle psi = pi / 2z from the tooth's middle)
    t = np.array(flank)
    k = int(np.argmin(abs(np.hypot(t[:, 0], t[:, 1]) - r)))
    assert abs(math.atan2(t[k][1], t[k][0])) == pytest.approx(math.pi / (2 * z), abs=0.02)
    # backlash makes teeth thinner (a gear) and spaces wider (a ring gear)
    assert area(gears.spur(m, z, backlash=0.2)) < area(gears.spur(m, z))
    assert area(gears.ring(m, 40, backlash=0.2)) > area(gears.ring(m, 40))
    # a rack: whole teeth every pi * m, then the pitch line to the end
    rk = gears.rack(2, 20)
    xs = [s[2][0] for s in rk]
    assert xs[-1] == pytest.approx(20) and max(s[2][1] for s in rk) == pytest.approx(2) and min(s[2][1] for s in rk) == pytest.approx(-2.5)
    for bad, msg in ((lambda: gears.spur(0, 20), "module"), (lambda: gears.spur(1, 2), "at least 3"),
                     (lambda: gears.spur(1, 20.5), "whole number"), (lambda: gears.spur(1, 20, 50), "pressure angle"),
                     (lambda: gears.spur(1, 20, backlash=-1), "backlash"), (lambda: gears.rack(2, 3), "at least"),
                     (lambda: gears.rack(2, 0), "length")):
        with pytest.raises(ValueError, match=msg):
            bad()


def test_gear_bodies_mesh_without_touching():
    r = ok("a = gear(module=2, teeth=20, thickness=8, bore=5)\n"
           "b = gear(module=2, teeth=11, thickness=8, at=world.point(31, 0, 0))\n"           # odd teeth: no turn needed
           "c = gear(module=2, teeth=12, thickness=8, at=world.point(0, 32, 0), spin=-90 + 15)\n"
           "ring = gear(module=2, teeth=40, thickness=6, internal=True, rim=100, at=world.point(0, -150, 0))\n"
           "planet = gear(module=2, teeth=15, thickness=6, at=world.point(25, -150, 0))\n")
    a = r.bodies["a"]
    assert topo.is_valid(a.shape)
    assert topo.volume(a.shape) == pytest.approx(area(gears.spur(2, 20)) * 8 - math.pi * 2.5 ** 2 * 8, rel=2e-3)
    assert {"+Z", "-Z", "bore"} <= set(a.face_labels()) and "teeth#0" in a.face_labels()
    assert a.anchor("+Z").origin[2] == pytest.approx(8) and a.anchor("pitch").origin[0] == pytest.approx(20)
    ring = r.bodies["ring"]
    assert "rim" in ring.face_labels()
    assert topo.volume(ring.shape) == pytest.approx((math.pi * 50 ** 2 - area(gears.ring(2, 40))) * 6, rel=2e-3)
    for x, y in (("a", "b"), ("a", "c"), ("ring", "planet")):
        res = build(f"a = gear(module=2, teeth=20, thickness=8, bore=5)\n"
                    "b = gear(module=2, teeth=11, thickness=8, at=world.point(31, 0, 0))\n"
                    "c = gear(module=2, teeth=12, thickness=8, at=world.point(0, 32, 0), spin=-90 + 15)\n"
                    "ring = gear(module=2, teeth=40, thickness=6, internal=True, rim=100, at=world.point(0, -150, 0))\n"
                    "planet = gear(module=2, teeth=15, thickness=6, at=world.point(25, -150, 0))\n"
                    f"i = intersect({x}, [{y}], keep_tools=True)\n")
        assert "no solid" in next(z for z in res.results if z.name == "i").message, (x, y)
    # out of phase they collide: the check above means something
    res = build("a = gear(module=2, teeth=20, thickness=8)\nb = gear(module=2, teeth=11, thickness=8, at=world.point(31, 0, 0), spin=16.3636)\n"
                "i = intersect(a, [b], keep_tools=True)\n")
    assert next(z for z in res.results if z.name == "i").status in ("ok", "warning")


def test_helical_and_herringbone_gears():
    r = ok("h = gear(module=1.5, teeth=24, thickness=10, helix=20)\n"
           "hb = gear(module=1.5, teeth=24, thickness=10, helix=25, herringbone=True, at=world.point(60, 0, 0))\n")
    flat = area(gears.spur(1.5, 24)) * 10
    for n in ("h", "hb"):
        b = r.bodies[n]
        assert topo.is_valid(b.shape) and topo.volume(b.shape) == pytest.approx(flat, rel=3e-3)
    # the top is the bottom turned by thickness * tan(helix) / r
    top = [f for f, nm in r.bodies["h"].face_names if nm.name == "+Z"][0]
    pts = np.array([topo.edge_midpoint(e) for e in topo.edges(top)])
    far = pts[np.argmax(np.hypot(pts[:, 0], pts[:, 1]))]
    turn = math.degrees(math.atan2(far[1], far[0])) % (360 / 24)
    want = math.degrees(10 * math.tan(math.radians(20)) / 18) % (360 / 24)
    assert min(abs(turn - want), 360 / 24 - abs(turn - want)) < 0.5


def test_gear_errors():
    assert "teeth" in err("g = gear(module=1, teeth=2, thickness=5)\n", "g")
    assert "bore" in err("g = gear(module=1, teeth=12, thickness=5, bore=20)\n", "g")
    assert "helix" in err("g = gear(module=1, teeth=12, thickness=5, helix=60)\n", "g")
    assert "rim" in err("g = gear(module=1, teeth=30, thickness=5, internal=True, rim=30)\n", "g")
    assert "thickness must be positive" in err("g = gear(module=1, teeth=30, thickness=0)\n", "g")


def test_gears_and_racks_in_sketches():
    r = ok('s = sketch(points={"o": (0, 0), "o2": (31, 0), "a": (-20, -30), "b": (20, -30), "c": (20, -40), "d": (-20, -40)},\n'
           '   curves={"g": ("gear", "o", 2, 20), "g2": ("gear", "o2", 2, 11, {"backlash": 0.1}), "bore": ("circle", "o", 4),\n'
           '           "r": ("rack", "a", "b", 2), "l1": ("line", "b", "c"), "l2": ("line", "c", "d"), "l3": ("line", "d", "a")},\n'
           '   constraints=[("diameter", "bore", 8), ("horizontal", "r"), ("length", "r", 40), ("concentric", "g", "bore")])\n'
           'e = extrude(s.face("bore+g"), 6)\n'
           'k = extrude(s.face("l1+l2+l3+r"), 6)\n'
           't = sketch(points={"o": (0, 0)}, curves={"rg": ("ring_gear", "o", 2, 40), "out": ("circle", "o", 50)})\n'
           'ring = extrude(t, 5)\n')
    assert sorted(set(r.bodies["s"].face_labels())) == ["bore", "bore+g", "g2", "l1+l2+l3+r", "profile"]
    assert topo.volume(r.bodies["e"].shape) == pytest.approx((area(gears.spur(2, 20)) - math.pi * 16) * 6, rel=2e-3)
    rack = r.bodies["k"]
    assert topo.is_valid(rack.shape)
    assert topo.volume(r.bodies["ring"].shape) == pytest.approx((math.pi * 2500 - area(gears.ring(2, 40))) * 5, rel=2e-3)
    # a gear's outline is many edges, all under its name; it can be a sweep path no more than a circle
    assert len(r.bodies["s"].edges_named("g")) > 100
    info = next(x for x in r.results if x.name == "s").info["sketch"]
    assert len(info["curves"]["g"]["samples"]) > 200 and info["curves"]["r"]["pts"] == ["a", "b"]
    for bad, msg in (('("gear", "o", 2, 2)', "3 or more teeth"), ('("gear", "o", 0, 20)', "module above 0"),
                     ('("gear", "o", 2, 20, {"teeth": 3})', "options are"), ('("rack", "o", "p", 2, {"angle": 3})', "options are"),
                     ('("gear", "o", 1, 20, {"pressure_angle": 60})', "pressure angle")):
        msg_ = err(f's = sketch(points={{"o": (0, 0), "p": (10, 0)}}, curves={{"g": {bad}}})\n', "s")
        assert msg in msg_, (bad, msg_)


@pytest.mark.parametrize("section", ["iso", "acme", "trapezoidal", "buttress"])
def test_coil_thread_profiles_join_a_shaft_and_a_nut(section):
    r = ok(f'shaft = cylinder(r=8, h=30)\nt = coil(R=8, turns=5, pitch=4, size=4, section="{section}", at=world.point(0, 0, 3))\n'
           'j = join(shaft, [t])\n'
           'nut = cylinder(r=15, h=16, at=world.point(0, 60, 0))\nhole = cylinder(r=8, h=16, at=world.point(0, 60, 0))\n'
           'cut(nut, [hole])\n'
           f'ti = coil(R=8, turns=2, pitch=4, size=4, section="{section}", side="inside", at=world.point(0, 60, 3))\n'
           'j2 = join(nut, [ti])\n')
    from openfusion.primitives import thread_section
    prof = thread_section(section, 4)
    x, y = np.array(prof).T
    cross = x * np.roll(y, -1) - np.roll(x, -1) * y
    a = cross.sum() / 2
    cx = np.sum((x + np.roll(x, -1)) * cross) / (6 * a)                     # the section's centre, out from R
    t = build(f't = coil(R=8, turns=5, pitch=4, size=4, section="{section}")\n').bodies["t"]
    # a section in the axial plane swept round the axis (and up): Pappus
    assert topo.volume(t.shape) == pytest.approx(abs(a) * 2 * math.pi * (8 + cx) * 5, rel=0.01)
    for n in ("shaft", "nut"):
        assert topo.is_valid(r.bodies[n].shape)
    shaft = r.bodies["shaft"]
    assert topo.volume(shaft.shape) > math.pi * 64 * 30
    lo, hi = topo.bbox(shaft.shape)
    depth = max(p[0] for p in prof)
    assert hi[0] == pytest.approx(8 + depth, abs=0.05)                       # the crests stand out by the depth
    assert topo.volume(r.bodies["nut"].shape) > math.pi * (225 - 64) * 16


def test_coil_thread_profile_errors():
    assert "pitch must be more" in err('c = coil(R=8, turns=2, pitch=3, size=4, section="iso")\n', "c")
    assert "doesn't fit inside" in err('c = coil(R=1, turns=2, pitch=4, size=4, section="acme", side="inside")\n', "c")
    assert "side must be" in err('c = coil(R=8, turns=2, pitch=4, size=4, section="iso", side="up")\n', "c")
    assert "section must be one of" in err('c = coil(R=8, turns=2, pitch=4, size=4, section="whitworth")\n', "c")
