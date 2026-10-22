"""Gate 1: rebuild the example parts across many parameter sets.

For every set: every feature must succeed with no fallback warnings, every
solid must be valid, the set of face names must not change, and the design
intent (post centered, holes inside the plate, bosses under the lid holes)
must hold.
"""
import math
import pathlib
import random

import numpy as np
import pytest

from openfusion import Engine, topo

EX = pathlib.Path(__file__).resolve().parent.parent / "examples"
ENGINE = Engine()


def run(name, **overrides):
    r = ENGINE.run_file(str(EX / name), overrides)
    assert r.script_error is None, r.script_error
    bad = [x for x in r.results if x.status != "ok"]
    assert not bad, f"{overrides}\n{r.report()}"
    for b in r.bodies.values():
        if b.role != "removed":
            assert topo.is_valid(b.shape), f"{b.id} invalid for {overrides}"
    return r


def labels(r, body):
    return sorted(r.bodies[body].face_labels())


def sweep(n, seed, ranges):
    rnd = random.Random(seed)
    for _ in range(n):
        yield {k: round(rnd.uniform(lo, hi), 3) for k, (lo, hi) in ranges.items()}


PLATE_RANGES = {"plate_l": (50, 140), "plate_w": (30, 90), "plate_t": (3, 12), "post_r": (4, 12),
                "post_h": (10, 60), "hole_r": (1.5, 3.5), "margin": (6, 12)}


@pytest.mark.parametrize("ov", list(sweep(25, 1, PLATE_RANGES)))
def test_plate_post_sweep(ov):
    base = labels(run("plate_post.ofs"), "plate")
    r = run("plate_post.ofs", **ov)
    assert labels(r, "plate") == base
    plate = r.bodies["plate"]
    # the post's top face is centered over the plate and at the right height
    top = plate.faces_named("post.+Z")[0]
    c = topo.face_area_centroid(top)[1]
    assert np.allclose(c, (0, 0, ov["plate_t"] + ov["post_h"]), atol=1e-6)
    # four holes on the top face, each `margin` from two edges
    f = plate.faces_named("+Z")[0]
    from openfusion.refs import face_points, face_anchor_frame
    pts = face_points(plate, f, face_anchor_frame(plate, [f], "+Z"))
    assert len(pts["hole"]) == 4 and len(pts["boss"]) == 1
    assert np.allclose(pts["boss"][0][:2], (0, 0), atol=1e-6)
    for h in pts["hole"]:
        assert math.isclose(abs(h[0]), ov["plate_l"] / 2 - ov["margin"], abs_tol=1e-6)
        assert math.isclose(abs(h[1]), ov["plate_w"] / 2 - ov["margin"], abs_tol=1e-6)


ENC_RANGES = {"inner_l": (40, 120), "inner_w": (30, 80), "inner_h": (12, 50), "wall": (1.6, 3.2),
              "floor": (1.2, 3), "corner_r": (1, 4), "boss_r": (3, 4.5)}


@pytest.mark.parametrize("ov", list(sweep(20, 2, ENC_RANGES)))
def test_enclosure_sweep(ov):
    base_case, base_lid = labels(run("enclosure.ofs"), "case"), labels(run("enclosure.ofs"), "lid")
    ov["boss_inset"] = ov["boss_r"] + 1.0
    r = run("enclosure.ofs", **ov)
    assert labels(r, "case") == base_case
    assert labels(r, "lid") == base_lid
    case, lid = r.bodies["case"], r.bodies["lid"]
    # bosses: pilot holes line up with lid holes in X/Y (lid is offset only in Y)
    pil = sorted(tuple(np.round(topo.face_area_centroid(f)[1][:2], 6))
                 for f in case.faces_named("pilot.side") + [g for k in (1, 2, 3) for g in case.faces_named(f"pilots_{k}.side")])
    lh = sorted(tuple(np.round(topo.face_area_centroid(f)[1][:2], 6))
                for f in lid.faces_named("hole.side") + [g for k in (1, 2, 3) for g in lid.faces_named(f"holes_{k}.side")])
    assert len(pil) == 4 and len(lh) == 4
    dy = ov["inner_w"] + 2 * ov["wall"] + 10
    for a, b in zip(sorted(pil), sorted((x, y - dy) for x, y in lh)):
        assert np.allclose(a, b, atol=1e-6)
    # the lid lies on the bed, the box too
    assert abs(topo.bbox(lid.shape)[0][2]) < 1e-6 and abs(topo.bbox(case.shape)[0][2]) < 1e-6


def test_showcase_builds_and_mirror_matches():
    r = run("showcase.ofs")
    b, t = r.bodies["bracket"], r.bodies["twin"]
    assert math.isclose(topo.volume(b.shape), topo.volume(t.shape), rel_tol=1e-9)
    assert r.bodies["guide"].role == "reference"


def test_incremental_rebuild_uses_cache():
    e = Engine()
    e.run_file(str(EX / "enclosure.ofs"))
    r = e.run_file(str(EX / "enclosure.ofs"), {"lid_gap": 20.0})
    cached = [x.name for x in r.results if x.cached]
    assert "hollow" in cached and "drill" in cached, "box features before the lid are reused"
    assert not any(x.cached for x in r.results if x.name.startswith("lid") and x.name != "lid")
