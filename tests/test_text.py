"""Text: lettering profiles, embossed and engraved."""
import pytest

from openfusion import Engine, topo
from openfusion.text import BUNDLED, FONT_DIR, cap_height, layout

ENGINE = Engine()
LID = "from openfusion import *\nlid = box(length=60, width=30, height=4, origin=\"center_xy\")\n"


def run(src):
    return ENGINE.run_source(LID + src)


def status(r, name):
    res = next(x for x in r.results if x.name == name)
    return res.status, res.message


def test_layout_sizes_and_aligns_by_capital_height():
    faces, (x0, y0, x1, y1), dy = layout("HEH", 10, halign="left", valign="baseline")
    lo, hi = topo.bbox(topo.compound(faces))
    assert hi[1] - lo[1] == pytest.approx(10, abs=0.05)                 # capitals 10 mm tall
    assert lo[1] == pytest.approx(0, abs=0.05) and dy == 0
    faces, (x0, y0, x1, y1), dy = layout("HEH", 10, halign="center", valign="center")
    lo, hi = topo.bbox(topo.compound(faces))
    assert (lo[0] + hi[0]) / 2 == pytest.approx(0, abs=0.6) and (lo[1] + hi[1]) / 2 == pytest.approx(0, abs=0.05)
    two, (x0, y0, x1, y1), _ = layout("AB\nCD", 5, valign="top")
    lo, hi = topo.bbox(topo.compound(two))
    assert hi[1] == pytest.approx(0, abs=0.05) and lo[1] < -9               # two lines, top at 0
    assert 0.6 < cap_height() < 0.8 and cap_height(bold=True) > 0.6
    for bad in (dict(halign="middle"), dict(valign="mid"), dict(spacing=0)):
        with pytest.raises(ValueError):
            layout("A", 5, **bad)
    with pytest.raises(ValueError):
        layout("   ", 5)
    with pytest.raises(ValueError):
        layout("A", 0)


def test_emboss_and_engrave_on_a_face():
    r = run('t = text("OF", size=8, at=lid.face("+Z"))\n'
            'up = extrude(t, 0.8)\n'
            'b = text("08", size=6, at=lid.face("-Z"), bold=True)\n'
            'down = extrude(b, -0.6)\n')
    for n in ("t", "up", "b", "down"):
        assert status(r, n)[0] == "ok", status(r, n)
    lid = r.bodies["lid"]
    assert topo.is_valid(lid.shape) and len(topo.solids(lid.shape)) == 1
    lo, hi = topo.bbox(lid.shape)
    assert hi[2] == pytest.approx(4.8, abs=1e-6) and lo[2] == pytest.approx(0, abs=1e-6)
    assert any(x.startswith("up.side(text)") for x in lid.face_labels())
    assert any(x.startswith("down.side(text)") for x in lid.face_labels())
    info = next(x for x in r.results if x.name == "t").info["text"]
    assert info["faces"] == 2 and 8 < info["width"] < 16
    # the text is a reference profile with anchors
    assert r.bodies["t"].role == "reference" and r.bodies["t"].kind == "profile"
    r = run('t = text("A", size=5, at=lid.face("+Z"))\nset_anchor(lid, "label", at=t.anchor("center"))\n'
            'u = text("B", size=5, at=t.anchor("baseline").offset(x=10), halign="left", valign="baseline")\n')
    assert [x.status for x in r.results] == ["ok"] * 4


def test_fonts_and_errors():
    import os
    path = os.path.join(FONT_DIR, BUNDLED[True])
    r = run(f't = text("Hi", size=5, font={path!r})\n')
    assert status(r, "t")[0] == "ok"
    assert "not found" in status(run('t = text("Hi", font="/no/such.ttf")\n'), "t")[1]
    assert "no installed font" in status(run('t = text("Hi", font="Surely Not A Font 123")\n'), "t")[1]
    assert "some characters" in status(run('t = text("")\n'), "t")[1]
    # text on the bed has no body under it: extrude makes a new body
    r = run('t = text("Hi", size=5)\nsign = extrude(t, 2)\n')
    # an installed font by name (which ones exist depends on the computer: one of these will)
    found = [status(run(f't = text("Hi", font="{n}")\n'), "t")[0] for n in ("Arial", "Helvetica", "DejaVu Sans", "Liberation Sans")]
    assert "ok" in found
    assert status(r, "sign")[0] == "ok" and "sign" in r.bodies
