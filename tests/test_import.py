"""Importing parts: STEP solids exactly, STL and 3MF meshes as solids with flat faces merged."""
import math
import zipfile

import numpy as np
import pytest

from openfusion import Engine, topo
from openfusion.export import export_step, export_stl
from openfusion.importer import _3mf_triangles, _stl_triangles, mesh_to_solid

PART = '''from openfusion import *
plate = box(length=60, width=40, height=10, origin="center_xy")
boss = cylinder(r=8, h=12, at=plate.anchor("+Z").offset(x=15))
j = join(plate, [boss])
h = hole(plate.face("boss.+Z").anchor(), d=5)
'''


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("imp")
    r = Engine().run_source(PART)
    b = r.bodies["plate"]
    export_step([b], str(d / "part.step"))
    export_stl([b], str(d / "part.stl"))
    return d, topo.volume(b.shape)


def run(d, src):
    p = d / "use.ofs"
    p.write_text("from openfusion import *\n" + src)
    return Engine().run_file(str(p))


def test_step_comes_in_exact_with_names_and_bbox_anchors(files):
    d, vol = files
    r = run(d, 'a = import_file("part.step")\n'
               'c = cylinder(r=2, h=30, at=a.face("+Z#1").anchor().offset(x=-15).flipped())\n'
               'k = cut(a, [c])\n'
               'f = fillet(a, edges=a.edges("-Z", kind="outer"), r=1)\n')
    assert [x.status for x in r.results] == ["ok"] * 4, r.report()
    first = r.results[0]
    assert first.info["import"]["format"] == "step" and first.info["import"]["solids"] == 1
    labels = set(r.bodies["a"].face_labels())
    assert {"+X", "-X", "+Y", "-Y", "-Z", "+Z#0", "+Z#1", "cylinder#0", "cylinder#1"} <= labels and any("c.side" in x for x in labels)
    # +Z#0 is the boss's top (the outermost), +Z#1 the plate's
    r0 = run(d, 'a = import_file("part.step")\n')
    b = r0.bodies["a"]
    assert topo.volume(b.shape) == pytest.approx(vol, rel=1e-6)
    assert b.anchor("+Z").origin[2] == pytest.approx(22)              # the bounding box's top
    assert b.anchor("center").origin == pytest.approx([0, 0, 11])
    top = {n.label("a"): topo.face_area_centroid(f)[1][2] for f, n in b.face_names}
    assert top["+Z#0"] == pytest.approx(22) and top["+Z#1"] == pytest.approx(10)


def test_import_follows_the_file_and_places_scales(files, tmp_path):
    d, vol = files
    r = run(d, f'a = import_file("{d / "part.step"}", at=world.point(100, 0, 0), scale=2)\n')
    b = r.bodies["a"]
    assert topo.volume(b.shape) == pytest.approx(8 * vol, rel=1e-6)
    lo, hi = topo.bbox(b.shape)
    assert lo[0] == pytest.approx(40)
    # the engine notices a changed file (cache keyed on its time stamp)
    eng = Engine()
    p = tmp_path / "use.ofs"
    (tmp_path / "x.step").write_bytes((d / "part.step").read_bytes())
    p.write_text('from openfusion import *\na = import_file("x.step")\n')
    v1 = topo.volume(eng.run_file(str(p)).bodies["a"].shape)
    r2 = Engine().run_source('from openfusion import *\nb = box(length=5, width=5, height=5)\n')
    export_step([r2.bodies["b"]], str(tmp_path / "x.step"))
    import os
    import time
    os.utime(tmp_path / "x.step", ns=(time.time_ns(), time.time_ns() + 10**9))
    v2 = topo.volume(eng.run_file(str(p)).bodies["a"].shape)
    assert v1 == pytest.approx(vol) and v2 == pytest.approx(125)


def test_stl_becomes_a_solid_with_flat_faces_merged(files):
    d, vol = files
    r = run(d, 'm = import_file("part.stl")\nf = fillet(m, edges=m.edges("+Z#1", kind="outer"), r=1)\n')
    assert [x.status for x in r.results] == ["ok", "ok"], r.report()
    info = r.results[0].info["import"]
    assert info["format"] == "stl" and info["triangles"] > 100
    m = run(d, 'm = import_file("part.stl")\n').bodies["m"]
    assert topo.is_valid(m.shape)
    assert topo.volume(m.shape) == pytest.approx(vol, rel=0.01)           # facets on the round parts
    labels = set(m.face_labels())
    assert {"-Z", "+Y", "-Y", "+Z#0", "+Z#1"} <= labels                     # the flat sides came back whole
    big = [f for f, n in m.face_names if n.name == "-Z"][0]
    assert topo.face_area_centroid(big)[0] == pytest.approx(60 * 40 - math.pi * 2.5 ** 2, rel=0.01)


def test_mesh_details():
    # a cube's 12 triangles become 6 faces; inside-out comes out right; open meshes are refused
    v = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float) * 10
    q = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = np.array([[v[a], v[b], v[c]] for a, b, c, dd in q] + [[v[a], v[c], v[dd]] for a, b, c, dd in q])
    s, n = mesh_to_solid(tris, "cube")
    assert n == 12 and len(topo.faces(s)) == 6 and topo.volume(s) == pytest.approx(1000)
    s, _ = mesh_to_solid(tris[:, ::-1], "cube")
    assert topo.volume(s) == pytest.approx(1000)
    mixed = tris.copy()
    mixed[3] = mixed[3][::-1]                       # one triangle wound the wrong way
    s, _ = mesh_to_solid(mixed, "cube")
    assert topo.volume(s) == pytest.approx(1000) and topo.is_valid(s)
    with pytest.raises(ValueError, match="close up|join up"):
        mesh_to_solid(tris[:-1], "open")


def test_stl_ascii_and_3mf_readers(tmp_path):
    t = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]], float)
    p = tmp_path / "a.stl"
    p.write_text("solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid x\n")
    assert np.allclose(_stl_triangles(str(p)), t)
    p = tmp_path / "a.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", '<model unit="centimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
                   '<resources><object id="1"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>'
                   '<vertex x="0" y="1" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>'
                   '</resources></model>')
    assert np.allclose(_3mf_triangles(str(p)), t * 10)


def test_3mf_from_our_own_export_round_trips(files, tmp_path):
    from openfusion.export import export_3mf
    d, vol = files
    r = Engine().run_source(PART)
    export_3mf([r.bodies["plate"]], str(tmp_path / "p.3mf"))
    r = run(tmp_path, 'm = import_file("p.3mf")\n')
    assert r.results[0].status == "ok", r.report()
    assert topo.volume(r.bodies["m"].shape) == pytest.approx(vol, rel=0.01)


def test_import_errors_say_what_to_do(files, tmp_path):
    def err(src):
        res = run(tmp_path, src).results[0]
        assert res.status == "error"
        return res.message
    assert "no such file" in err('a = import_file("nothing.step")\n')
    (tmp_path / "x.obj").write_text("v 0 0 0\n")
    assert "imports .step, .stp, .stl and .3mf" in err('a = import_file("x.obj")\n')
    (tmp_path / "bad.step").write_text("not a step file")
    msg = err('a = import_file("bad.step")\n')
    assert "couldn't read" in msg or "no shapes" in msg
    assert "scale must be positive" in err(f'a = import_file("{files[0] / "part.step"}", scale=-1)\n')
