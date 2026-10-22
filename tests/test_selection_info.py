"""The selection readout (lengths, radii, areas, distances) and mesh clean-up for exports."""
import math

import numpy as np
import pytest

from openfusion import Engine
from openfusion.gui.measure import describe_selection

SRC = """from openfusion import *
b = box(length=40, width=30, height=20)
fillet(b, edges=["+X+Z"], r=3)
c = cylinder(r=5, h=10, at=b.anchor("+Z").offset(x=-30))
s = sphere(r=4, at=world.point(80, 0, 0))
k = cone(r1=6, r2=2, h=8, at=world.point(0, 60, 0))
t = torus(R=10, r=2, at=world.point(0, -60, 0))
l = loft([profile(rect=(10, 10), origin="center", at=world.point(60, 60, 0)), profile(r=3, at=world.point(60, 60, 20))])
"""


@pytest.fixture(scope="module")
def res():
    return Engine().run_source(SRC)


def rows(info):
    return {k: v for k, v, _ in info["rows"]}


def test_edges_report_length_radius_and_arcs(res):
    line = describe_selection(res, [{"kind": "edge", "body": "b", "label": "+Y+Z"}])["items"][0]
    assert line["what"] == "Straight edge" and rows(line)["Length"] == pytest.approx(37)
    rim = describe_selection(res, [{"kind": "edge", "body": "c", "label": "rim+Z"}])["items"][0]
    assert rim["what"] == "Circle" and rows(rim)["Radius"] == pytest.approx(5) and rows(rim)["Diameter"] == pytest.approx(10)
    arc = describe_selection(res, [{"kind": "edge", "body": "b", "label": "+Y|fillet1(+X+Z)"}])["items"][0]
    assert arc["what"] == "Arc" and rows(arc)["Angle"] == pytest.approx(90) and rows(arc)["Arc length"] == pytest.approx(1.5 * math.pi)
    # several edges share a label: the clicked point picks the right one
    near = describe_selection(res, [{"kind": "edge", "body": "b", "label": "+X|fillet1(+X+Z)", "point": [40, 15, 17]}])["items"][0]
    assert near["what"] == "Straight edge"


def test_faces_report_area_and_kind(res):
    def face(body, label):
        return describe_selection(res, [{"kind": "face", "body": body, "label": label}])["items"][0]
    assert rows(face("b", "-Z"))["Area"] == pytest.approx(1200) and face("b", "-Z")["what"] == "Flat face"
    assert rows(face("c", "side"))["Radius"] == pytest.approx(5)
    assert rows(face("s", "surface"))["Radius"] == pytest.approx(4)
    assert face("k", "side")["what"] == "Conical face" and rows(face("k", "side"))["Half angle"] == pytest.approx(math.degrees(math.atan(0.5)))
    assert rows(face("t", "surface"))["Minor radius"] == pytest.approx(2)
    assert face("l", "side#0")["what"] == "Free-form face"
    assert describe_selection(res, [{"kind": "face", "body": "b", "label": "nope"}])["items"][0]["what"] == "?"


def test_bodies_report_volume_area_and_size(res):
    info = describe_selection(res, [{"kind": "body", "body": "b"}])["items"][0]
    r = rows(info)
    assert r["Volume"] == pytest.approx(24000 - (9 - 9 * math.pi / 4) * 30) and r["Size"] == "40.00 × 30.00 × 20.00"


def test_two_selections_give_distance_angle_and_centres(res):
    pair = describe_selection(res, [{"kind": "face", "body": "b", "label": "+Z"}, {"kind": "face", "body": "b", "label": "-Z"}])["pair"]
    r = rows(pair)
    assert r["Minimum distance"] == pytest.approx(20) and r["Angle"] == pytest.approx(0)
    pair = describe_selection(res, [{"kind": "face", "body": "b", "label": "-Z"}, {"kind": "face", "body": "b", "label": "+X"}])["pair"]
    assert rows(pair)["Angle"] == pytest.approx(90) and "ΔX, ΔY, ΔZ" not in rows(pair)        # they touch
    pair = describe_selection(res, [{"kind": "edge", "body": "c", "label": "rim+Z"}, {"kind": "edge", "body": "c", "label": "rim-Z"}])["pair"]
    assert rows(pair)["Center to center"] == pytest.approx(10)
    pair = describe_selection(res, [{"kind": "edge", "body": "b", "label": "+Y+Z"}, {"kind": "face", "body": "b", "label": "-Z"}])["pair"]
    assert rows(pair)["Angle"] == pytest.approx(0)            # a line parallel to a plane


def test_many_selections_add_up(res):
    out = describe_selection(res, [{"kind": "face", "body": "b", "label": n} for n in ("+Z", "-Z", "-X")])
    assert rows(out["pair"])["Total area"] == pytest.approx(1200 + 1200 - 3 * 30 + 600)
    out = describe_selection(res, [{"kind": "edge", "body": "b", "label": n} for n in ("+Y+Z", "-Y+Z", "-X-Y")])
    assert rows(out["pair"])["Total length"] == pytest.approx(37 + 37 + 20)
    assert describe_selection(res, [{"kind": "body", "body": "zzz"}])["items"][0]["what"] == "?"


def test_selinfo_route(tmp_path):
    from openfusion.gui.server import Session, route
    f = tmp_path / "p.ofs"
    f.write_text(SRC)
    s = Session(str(f))
    out = route(s, "/api/selinfo", {"items": [{"kind": "body", "body": "c"}]})
    assert rows(out["items"][0])["Volume"] == pytest.approx(math.pi * 250)
    s.result = None
    assert route(s, "/api/selinfo", {"items": []}) == {"items": [], "pair": None}


def test_export_meshes_close_hairline_holes():
    from openfusion.export import _close_holes, _ear_clip
    # a cube missing its top: the hole is closed, wound against its neighbours
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
    tris = [(0, 2, 1), (0, 3, 2), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    fill = _close_holes(v, tris)
    assert len(fill) == 2
    from collections import Counter
    e = Counter()
    for a, b, c in tris + fill:
        for x, y in ((a, b), (b, c), (c, a)):
            e[(x, y)] += 1
    assert all(e[(y, x)] == 1 for (x, y) in e)                 # closed and consistently wound
    assert _close_holes(v, tris + fill) == []
    assert len(_ear_clip([(0, 0), (2, 0), (2, 2), (1, 1), (0, 2)])) == 3       # a concave polygon
