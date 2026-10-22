"""CLI and file exports."""
import collections
import pathlib
import subprocess
import sys
import zipfile

import numpy as np

from openfusion import Engine, topo
from openfusion.export import body_mesh, export

EX = pathlib.Path(__file__).resolve().parent.parent / "examples"


def cli(*args):
    return subprocess.run([sys.executable, "-m", "openfusion.cli", *args], capture_output=True, text=True)


def test_build_exports_and_overrides(tmp_path):
    out3, outs, outt = tmp_path / "e.3mf", tmp_path / "e.step", tmp_path / "e.stl"
    p = cli("build", str(EX / "enclosure.ofs"), "--set", "inner_l=90*mm", "-o", str(out3), "-o", str(outs),
            "-o", str(outt), "--quiet")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "21 ok" in p.stdout
    with zipfile.ZipFile(out3) as z:
        model = z.read("3D/3dmodel.model").decode()
    assert 'unit="millimeter"' in model and 'name="case"' in model and 'name="lid"' in model
    assert outs.stat().st_size > 10000 and outt.stat().st_size > 10000


def test_build_failure_exit_code(tmp_path):
    s = tmp_path / "bad.ofs"
    s.write_text("from openfusion import *\na = box(length=4, width=4, height=4)\nfillet(a, edges=['+X+Z'], r=9)\n")
    p = cli("build", str(s), "-o", str(tmp_path / "x.stl"))
    assert p.returncode == 1 and "fillet" in p.stdout and not (tmp_path / "x.stl").exists()


def test_new_and_view(tmp_path):
    s = tmp_path / "p.ofs"
    assert cli("new", str(s)).returncode == 0
    h = tmp_path / "p.html"
    p = cli("view", str(s), "-o", str(h))
    assert p.returncode == 0, p.stdout + p.stderr
    html = h.read_text()
    assert "OF_BOOT" in html and "THREE" in html and '"base"' in html


def test_meshes_are_watertight_and_exclude_reference():
    r = Engine().run_file(str(EX / "showcase.ofs"))
    for b in r.solids():
        v, t = body_mesh(b.shape)
        edges = collections.Counter(tuple(sorted(e)) for a, bb, c in t for e in ((a, bb), (bb, c), (c, a)))
        assert all(n == 2 for n in edges.values()), b.id
        vol = sum(np.dot(v[a], np.cross(v[bb], v[c])) for a, bb, c in t) / 6
        assert abs(vol - topo.volume(b.shape)) / topo.volume(b.shape) < 0.01, b.id


def test_reference_and_removed_never_exported(tmp_path):
    r = Engine().run_file(str(EX / "showcase.ofs"))
    n = export(r.bodies.values(), tmp_path / "s.3mf")
    assert n == len(r.solids())
    names = zipfile.ZipFile(tmp_path / "s.3mf").read("3D/3dmodel.model").decode()
    assert 'name="guide"' not in names and 'name="notch"' not in names


def test_build_exports_chosen_bodies_each(tmp_path):
    src = tmp_path / "p.ofs"
    src.write_text("from openfusion import *\nplate = box(length=10, width=10, height=2)\n"
                   "post = cylinder(r=2, h=5, at=plate.anchor('+Z'))\njig = box(length=3, width=3, height=3, role='reference')\n")
    r = cli("build", str(src), "-o", str(tmp_path / "out.stl"), "--body", "post", "--body", "jig", "--each", "--quiet")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out-post.stl").exists() and (tmp_path / "out-jig.stl").exists()
    assert not (tmp_path / "out.stl").exists()
    r = cli("build", str(src), "-o", str(tmp_path / "x.stl"), "--body", "nope")
    assert r.returncode == 1 and "No body named nope" in r.stderr
