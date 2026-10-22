"""Holes (simple, clearance, counterbore, countersink, tapped) and modelled threads."""
import math

import pytest

from openfusion import Engine, topo
from openfusion.holes import (CLEARANCE, H_BASIC, minor_internal, nearest_size, parse_size)

ENGINE = Engine()
PLATE = "from openfusion import *\nplate = box(length=60, width=40, height=10, origin=\"center_xy\")\n"
FULL = 60 * 40 * 10


def run(src):
    r = ENGINE.run_source(PLATE + src)
    return r


def ok(r, name):
    res = next(x for x in r.results if x.name == name)
    assert res.status == "ok", (res.status, res.message)
    return res


def err(r, name):
    res = next(x for x in r.results if x.name == name)
    assert res.status == "error", res.status
    return res.message


def removed(r, body="plate", full=FULL):
    b = r.bodies[body]
    assert topo.is_valid(b.shape)
    return full - topo.volume(b.shape)


def labels(r, body="plate"):
    return set(r.bodies[body].face_labels())


def test_sizes_and_tables():
    assert parse_size("M6") == (6.0, 1.0) and parse_size("m8x1") == (8.0, 1.0) and parse_size("M2.5") == (2.5, 0.45)
    for bad in ("6", "M7", "M6x4", "bolt"):
        with pytest.raises(ValueError):
            parse_size(bad)
    assert minor_internal(6, 1.0) == pytest.approx(6 - 2 * H_BASIC)          # 4.917, the M6 tap drill
    assert nearest_size(8.0, internal=False) == (8.0, 1.25)
    assert nearest_size(4.9, internal=True) == (6.0, 1.0)
    assert nearest_size(7.3, internal=False) is None


def test_simple_through_and_blind_holes():
    r = run('h1 = hole(plate.anchor("+Z").offset(x=10), d=5)\n'
            'h2 = hole(plate.anchor("+Z").offset(x=-10), d=4, depth=6)\n'
            'h3 = hole(plate.anchor("+Z").offset(y=10), d=4, depth=6, tip=118)\n')
    for n in ("h1", "h2", "h3"):
        ok(r, n)
    cone = math.pi * 4 / 3 * (2 / math.tan(math.radians(59)))
    assert removed(r) == pytest.approx(math.pi * 6.25 * 10 + 2 * math.pi * 4 * 6 + cone, rel=1e-6)
    assert {"h1.wall", "h2.wall", "h2.bottom", "h3.tip"} <= labels(r)
    # a hole's faces are found through the hole feature, though they belong to the plate
    r = run('h1 = hole(plate.anchor("+Z"), d=5)\nset_anchor(plate, "axis", at=h1.face("wall").anchor())\n')
    assert [x.status for x in r.results] == ["ok"] * 3


def test_several_positions_in_one_hole():
    r = run('h1 = hole([plate.anchor("+Z").offset(x=x, y=y) for x in (-20, 20) for y in (-12, 12)], size="M4")\n')
    ok(r, "h1")
    d = CLEARANCE[4][1]
    assert removed(r) == pytest.approx(4 * math.pi * d * d / 4 * 10, rel=1e-6)
    assert {f"h1.wall#{k}" for k in range(4)} <= labels(r)


def test_clearance_fits_and_allowance():
    for fit, i in (("close", 0), ("clearance", 1), ("loose", 2)):
        r = run(f'h1 = hole(plate.anchor("+Z"), size="M3", fit="{fit}", allowance=0.2)\n')
        d = CLEARANCE[3][i] + 0.2
        assert removed(r) == pytest.approx(math.pi * d * d / 4 * 10, rel=1e-6), fit
    r = run('h1 = hole(plate.anchor("+Z"), size="M3", fit="tap_drill")\n')
    d = minor_internal(3, 0.5)
    assert removed(r) == pytest.approx(math.pi * d * d / 4 * 10, rel=1e-6)


def test_counterbore_and_countersink_for_screws():
    r = run('h1 = hole(plate.anchor("+Z"), size="M4", head="counterbore")\n')
    ok(r, "h1")
    v = math.pi * (4 ** 2 * 4.4 + 2.25 ** 2 * (10 - 4.4))           # ISO 4762: 8 x 4.4 over a 4.5 hole
    assert removed(r) == pytest.approx(v, rel=1e-6)
    assert {"h1.cb_wall", "h1.cb_floor", "h1.wall"} <= labels(r)
    r = run('h1 = hole(plate.anchor("+Z"), size="M4", head="countersink")\n')
    R, rr = 8.96 / 2, 2.25
    drop = R - rr                                                         # 90 degrees
    cone = math.pi * drop / 3 * (R * R + R * rr + rr * rr)
    assert removed(r) == pytest.approx(cone + math.pi * rr * rr * (10 - drop), rel=1e-6)
    assert "h1.countersink" in labels(r)
    r = run('h1 = hole(plate.anchor("+Z"), d=3, head="counterbore", head_d=6, head_depth=2)\n')
    assert removed(r) == pytest.approx(math.pi * (9 * 2 + 2.25 * 8), rel=1e-6)


def _thread_removed(nd, p, length, allowance=0.0):
    """What an ISO internal thread removes beyond its tap drill hole: the grooves fill 7/16 of
    the annulus between the minor and major diameters (basic profile)."""
    R, r1 = (nd + allowance) / 2, minor_internal(nd, p) / 2 + allowance / 2
    return 7 / 16 * math.pi * (R * R - r1 * r1) * length


def test_tapped_holes_have_real_threads():
    r = run('h1 = hole(plate.anchor("+Z").offset(x=-15), size="M6", fit="tapped")\n'
            'h2 = hole(plate.anchor("+Z").offset(x=15), size="M6", fit="tapped", depth=8)\n')
    for n in ("h1", "h2"):
        ok(r, n)
    d1 = minor_internal(6, 1.0)
    drill = math.pi * d1 * d1 / 4 * (10 + 8)
    grooves = _thread_removed(6, 1.0, 10) + _thread_removed(6, 1.0, 8 - 0.5)
    assert removed(r) == pytest.approx(drill + grooves, rel=0.04)
    assert any(x.startswith("h1.thread") for x in labels(r)) and any(x.startswith("h2.thread") for x in labels(r))
    # printing allowance opens the thread; a left-hand thread removes the same
    a = run('h1 = hole(plate.anchor("+Z"), size="M6", fit="tapped", allowance=0.3)\n')
    left = run('h1 = hole(plate.anchor("+Z"), size="M6", fit="tapped", hand="left")\n')
    right = run('h1 = hole(plate.anchor("+Z"), size="M6", fit="tapped")\n')
    assert removed(a) > removed(right) + 3
    assert removed(left) == pytest.approx(removed(right), rel=1e-3)
    # under a counterbore and a countersink (it starts just below the cone)
    for head in ("counterbore", "countersink"):
        ok(run(f'h1 = hole(plate.anchor("+Z"), size="M4", fit="tapped", head="{head}")\n'), "h1")


def test_thread_on_a_shaft_and_in_a_hole():
    src = ('post = cylinder(r=4, h=20, at=world.point(0, 60, 0))\n'
           't1 = thread(post.face("side"), length=12)\n'
           'h1 = hole(plate.anchor("+Z"), size="M8", fit="tap_drill")\n'
           't2 = thread(h1.face("wall"))\n')
    r = run(src)
    for n in ("t1", "t2"):
        ok(r, n)
    shaft = math.pi * 16 * 20
    lost = removed(r, "post", shaft)
    # the bolt's grooves: 9/16 of the annulus between the root and the crest (basic profile)
    R, r1 = 4.0, 4.0 - H_BASIC * 1.25
    assert lost == pytest.approx(9 / 16 * math.pi * (R * R - r1 * r1) * 12, rel=0.04)
    assert any(x.startswith("t1.thread") for x in labels(r, "post"))
    assert any(x.startswith("t2.thread") for x in labels(r))
    info = ENGINE.run_source(PLATE + src).results[-1].info["thread"]
    assert info["size"] == "M8x1.25" and info["internal"]


def test_hole_on_a_sketch_point_goes_into_the_body_under_it():
    r = run('s = sketch(at=plate.face("+Z"), points={"a": (5, 5)}, curves={"c": ("circle", "a", 1, "construction")})\n'
            'h1 = hole(s.anchor("a"), d=3)\n')
    ok(r, "h1")
    assert removed(r) == pytest.approx(math.pi * 2.25 * 10, rel=1e-6)


def test_hole_and_thread_errors_say_what_to_do():
    assert "give d or size" in err(run('h1 = hole(plate.anchor("+Z"), d=3, size="M4")\n'), "h1")
    assert "needs size" in err(run('h1 = hole(plate.anchor("+Z"), d=3, fit="tapped")\n'), "h1")
    assert "fit must be" in err(run('h1 = hole(plate.anchor("+Z"), size="M3", fit="snug")\n'), "h1")
    assert "give a diameter" in err(run('h1 = hole(plate.anchor("+Z"))\n'), "h1")
    assert "must be less than" in err(run('h1 = hole(plate.anchor("+Z"), size="M8", head="counterbore", depth=5)\n'), "h1")
    assert "must be less than" in err(run('h1 = hole(plate.anchor("+Z"), size="M12", head="counterbore")\n'), "h1")
    assert "bigger than the hole" in err(run('h1 = hole(plate.anchor("+Z"), d=6, head="countersink", head_d=5)\n'), "h1")
    assert "too shallow" in err(run('h1 = hole(plate.anchor("+Z"), size="M10", fit="tapped", depth=1)\n'), "h1")
    assert "say which body" in err(run('h1 = hole(world.point(0, 0, 10), d=3)\n'), "h1")
    assert "head must be" in err(run('h1 = hole(plate.anchor("+Z"), d=3, head="flat")\n'), "h1")
    assert "depth must be positive" in err(run('h1 = hole(plate.anchor("+Z"), d=3, depth=-2)\n'), "h1")
    assert "no standard counterbore" in err(run('h1 = hole(plate.anchor("+Z"), size="M3.5", head="counterbore")\n'), "h1")
    r = run('h1 = hole(plate.anchor("+Z").offset(z=5), d=3, depth=2)\n')      # in the air above it
    assert any("misses the body" in w for w in next(x for x in r.results if x.name == "h1").warnings)
    assert "cylindrical face" in err(run('t = thread(plate.face("+Z"))\n'), "t")
    assert "too big for" in err(run('h1 = hole(plate.anchor("+Z"), d=8)\nt = thread(h1.face("wall"), "M6")\n'), "t")
    assert "near 7" in err(run('p = cylinder(r=3.5, h=5, at=world.point(0, 60, 0))\nt = thread(p.face("side"))\n'), "t")
    assert "length must be" in err(run('p = cylinder(r=3, h=5, at=world.point(0, 60, 0))\nt = thread(p.face("side"), length=9)\n'), "t")
    # a shell can't follow a modelled thread: it says so straight away (it used to sweep for hours)
    msg = err(run('h1 = hole(plate.anchor("+Z"), size="M4", fit="tapped")\nsh = shell(plate, 1.5, faces=[plate.face("-Z")])\n'), "sh")
    assert "modelled thread of h1" in msg and "shell the part first" in msg
