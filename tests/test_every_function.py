"""Tests for the functions no other test reaches: rarely used operations and references,
the history adapters, reprs, and small geometry helpers. Together with the rest of the
suite every function in the package runs at least once (see tools/function_coverage.py)."""
import math

import numpy as np
import pytest

from openfusion import Engine, topo
from openfusion.export import printable


def build(src, **ov):
    return Engine().run_source("from openfusion import *\n" + src, ov)


def ok(src, **ov):
    r = build(src, **ov)
    assert r.script_error is None, r.script_error
    bad = [x for x in r.results if x.status not in ("ok", "warning")]
    assert not bad, r.report()
    return r


def err(src, name):
    r = build(src)
    assert r.script_error is None, r.script_error
    res = next(x for x in r.results if x.name == name)
    assert res.status == "error", r.report()
    return res.message


def vol(r, name):
    return topo.volume(r.bodies[name].shape)


# ===================================================================== copy, split, set_role
def test_copy_is_independent_and_keeps_names():
    r = ok('b = box(length=10, width=20, height=5)\nc = copy(b)\nmove(c, dx=30)\nfillet(b, edges=b.edges("+Z"), r=1)')
    assert vol(r, "c") == pytest.approx(1000) and vol(r, "b") < 1000          # changing b leaves c alone
    assert sorted(r.bodies["c"].face_labels()) == ["+X", "+Y", "+Z", "-X", "-Y", "-Z"]
    assert topo.bbox(r.bodies["c"].shape)[0][0] == pytest.approx(30)
    assert r.bodies["c"].anchor("+Z").origin == pytest.approx((35, 10, 5))


def test_split_keeps_the_back_and_makes_the_front_a_new_body():
    r = ok('b = box(length=40, width=10, height=10)\nhalf = split(b, plane=world.anchor("YZ").offset(z=10))')
    assert vol(r, "b") == pytest.approx(1000) and vol(r, "half") == pytest.approx(3000)
    assert "half.cut" in r.bodies["b"].face_labels()
    assert "cut" in r.bodies["half"].face_labels()                              # named after the split itself
    assert r.bodies["half"].kind == r.bodies["b"].kind
    # a U shape cut across both arms: the front is two pieces in one body, with numbered names
    r = ok('u = box(length=30, width=10, height=20)\nslot = box(length=10, width=10, height=15, at=world.point(10, 0, 5))\n'
           'cut(u, tools=[slot])\ntops = split(u, plane=world.point(0, 0, 10))')
    assert len(topo.solids(r.bodies["tops"].shape)) == 2
    assert vol(r, "tops") == pytest.approx(2 * 10 * 10 * 10)
    assert {"cut#0", "cut#1"} <= set(r.bodies["tops"].face_labels())
    assert "does not cut" in err('b = box(length=4, width=4, height=4)\ns = split(b, plane=world.point(0, 0, 50))', "s")


def test_set_role_reference_removed_and_back():
    r = ok('a = box(length=5, width=5, height=5)\nb = box(length=5, width=5, height=5, at=world.point(10, 0, 0))\nset_role(b, "reference")')
    assert r.bodies["b"].role == "reference" and [x.id for x in printable(r.bodies.values())] == ["a"]
    r = ok('a = box(length=5, width=5, height=5)\nset_role(a, "removed")\nset_role(a, "solid")')
    assert r.bodies["a"].role == "solid"                                         # a removed body can be brought back
    assert "role must be one of" in err('a = box(length=5, width=5, height=5)\nx = set_role(a, "ghost")', "x")


def test_extrude_a_face_split_into_pieces_extrudes_every_piece():
    r = ok('b = box(length=30, width=10, height=10)\nslot = box(length=10, width=10, height=4, at=world.point(10, 0, 6))\n'
           'cut(b, tools=[slot])\nextrude(b.face("+Z"), 5)')
    assert vol(r, "b") == pytest.approx(3000 - 400 + 2 * 10 * 10 * 5)


# ===================================================================== references
def test_vertex_references_and_their_fallback():
    r = ok('b = box(length=10, width=20, height=30)\npin = cylinder(r=1, h=2, at=b.vertex("+++"))\n'
           'low = cylinder(r=1, h=2, at=b.vertex("--+").offset(z=1))')
    assert r.bodies["pin"].anchor("origin").origin == pytest.approx((10, 20, 30))
    assert r.bodies["low"].anchor("origin").origin == pytest.approx((0, 0, 31))
    # after rounding every edge the corner is gone: fall back to an anchor of the same name, with a warning
    r = ok('b = box(length=10, width=10, height=10)\nset_anchor(b, "+++", world.point(9, 9, 9))\n'
           'fillet(b, edges=b.edges(), r=2)\npin = cylinder(r=1, h=2, at=b.vertex("+++"))')
    pin = next(x for x in r.results if x.name == "pin")
    assert pin.status == "warning" and "fell back" in pin.warnings[0]
    assert r.bodies["pin"].anchor("origin").origin == pytest.approx((9, 9, 9))
    assert "no such vertex" in err('b = box(length=1, width=1, height=1)\nx = cylinder(r=1, h=1, at=b.vertex("+-*"))', "x")
    assert "no such vertex" in err('b = box(length=1, width=1, height=1)\nfillet(b, edges=b.edges(), r=0.2)\n'
                                   'x = cylinder(r=1, h=1, at=b.vertex("+++").strict())', "x")


def test_face_arc_and_boss_points():
    # the top of a plate with rounded vertical edges has an arc at each corner; a joined post is a boss
    r = ok('p = box(length=40, width=20, height=4)\nfillet(p, edges=["+X+Y", "+X-Y", "-X+Y", "-X-Y"], r=3)\n'
           'post = cylinder(r=2, h=6, at=p.anchor("+Z").offset(x=-10))\njoin(p, tools=[post])\n'
           'a = sphere(r=0.5, at=p.face("+Z").arc(0))\nk = sphere(r=0.5, at=p.face("+Z").boss(0))')
    arc = r.bodies["a"].anchor("origin").origin
    assert arc[2] == pytest.approx(4) and {round(abs(arc[0] - 20), 6), round(abs(arc[1] - 10), 6)} == {17.0, 7.0}
    assert r.bodies["k"].anchor("origin").origin == pytest.approx((10, 10, 4))


def test_frame_spin_turns_the_x_axis():
    r = ok('b = box(length=10, width=2, height=2, at=world.point(0, 0, 0).spin(90))\n'
           'c = box(length=10, width=2, height=2, at=world.anchor("XY").spin(45).spin(45))')
    for n in ("b", "c"):
        lo, hi = topo.bbox(r.bodies[n].shape)
        assert hi - lo == pytest.approx((2, 10, 2))


def test_reprs_read_like_script():
    import openfusion as of
    from openfusion.document import Document
    doc = Document()
    with doc:
        of.params(w=1)
        b = of.box(length=10, width=10, height=10)
        g = of.pattern_rect(b, x=(2, 20))
        loose = of.box(length=1, width=1, height=1)
        assert repr(loose) == "<body (unnamed)>"            # until the script has run
        assert repr(of.P) == "P({'w': 1})"
    doc.assign_names({"b": b, "g": g})
    w = of.world
    assert repr(b) == "<body b>" and repr(loose) == "<body box1>"
    assert repr(g) == "<group g>" and repr(g[1]) == "<body g_1>" and g[0] is b
    assert repr(w) == "world" and repr(w.anchor("XZ")) == 'world.anchor("XZ")'
    assert repr(b.face("+Z").anchor()) == 'b.face("+Z").anchor()'
    assert repr(b.face("+Z").corner(2)) == 'b.face("+Z").corner(2)'
    assert repr(of.edge_between(b.face("+Z"), b.face("+X"))) == 'edge_between(b.face("+Z"), b.face("+X"))'
    assert repr(b.face("+Z").edges()) == 'b.face("+Z").edges("all")'
    assert repr(b.edges()) == "b.edges()" and repr(b.edges("+Z", kind="outer")) == 'b.face("+Z").edges("outer")'
    assert repr(b.vertex("+++")) == 'b.vertex("+++")' and repr(b.edge("+X+Z")) == 'b.edge("+X+Z")'
    assert repr(b.anchor("+Z", u=1)) == 'b.anchor("+Z", u=1)'
    # owners: what a reference belongs to (used to find dependencies)
    for ref in (b.anchor("+Z"), b.face("+Z").anchor(), b.face("+Z").mid(0), b.vertex("+++")):
        assert ref.owner() is b
    assert w.point(1, 2, 3).owner() is None


def test_group_handles_have_a_stable_signature():
    from openfusion.refs import sig
    r = ok('b = box(length=1, width=1, height=1)\ng = pattern_rect(b, x=(3, 2))\njoin(b, tools=[g[1], g[2]])')
    assert vol(r, "b") == pytest.approx(3)
    eng = Engine()
    src = 'from openfusion import *\nb = box(length=1, width=1, height=1)\ng = pattern_rect(b, x=(3, 2))\nc = copy(g[2])\n'
    eng.run_source(src)
    again = eng.run_source(src)
    assert all(x.cached for x in again.results)                     # same signature on a re-run
    import openfusion.refs as R

    class F:
        name = "g"
    assert sig(R.GroupHandle(F(), None)) == "<group g>"


def test_mirrored_coil_keeps_its_path_parameter():
    r = ok('c = coil(R=10, turns=2, pitch=5, size=2)\nm = mirror(c, plane="YZ")\n'
           'a = sphere(r=0.1, at=c.anchor("path", t=0.25))\nb = sphere(r=0.1, at=m.anchor("path", t=0.25))')
    pa = r.bodies["a"].anchor("origin").origin
    pb = r.bodies["b"].anchor("origin").origin
    assert pb == pytest.approx((-pa[0], pa[1], pa[2]), abs=1e-6)


def test_the_generic_design_has_only_an_origin():
    from openfusion.body import Design, NameError_
    d = Design()
    assert d.names() == ["origin"]
    assert np.allclose(d.get("origin", {}).origin, 0)
    with pytest.raises(NameError_):
        d.get("+Z", {})


# ===================================================================== history adapters
def test_history_adapters_report_nothing_generated():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepTools import BRepTools_History
    from openfusion.body import _HistoryAdapter
    from openfusion.features import _ModifyHistory, _Same
    s = BRepPrimAPI_MakeBox(1, 1, 1).Shape()
    f = topo.faces(s)[0]
    for h in (_HistoryAdapter(BRepTools_History()), _Same(), _ModifyHistory(object())):
        assert h.Generated(f).Size() == 0
        assert not h.IsDeleted(f)
        assert h.Modified(f).Size() == 0                # _ModifyHistory swallows a failing kernel call


# ===================================================================== small things
def test_feature_and_result_helpers():
    r = ok("params(w=3)\nb = box(length=P.w, width=1, height=1)")
    assert r.body("b") is r.bodies["b"]
    from openfusion.document import Document
    doc = Document()
    with doc:
        from openfusion import box
        box(length=1, width=1, height=1)
    doc.assign_names({})
    assert repr(doc.features[0]) == "<feature box1: box>"


def test_geometry_helpers():
    from openfusion.geom import Frame, any_perpendicular
    for z in ((0, 0, 1), (1, 0, 0), (0.95, 0.1, 0.3), (0, -1, 0)):
        p = any_perpendicular(z)
        assert abs(np.linalg.norm(p) - 1) < 1e-12 and abs(p @ (np.array(z) / np.linalg.norm(z))) < 1e-12
    assert repr(Frame.make((1, 2, 3))) == "Frame(o=(1, 2, 3), x=(1, 0, 0), z=(0, 0, 1))"


def test_topology_helpers():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    a = BRepPrimAPI_MakeBox(2, 2, 2).Shape()
    b = BRepPrimAPI_MakeBox(gp_Pnt(10, 0, 0), 2, 2, 2).Shape()
    assert topo.center_of_mass(a) == pytest.approx((1, 1, 1))
    c = topo.compound([a, b])
    assert len(topo.solids(c)) == 2 and topo.volume(c) == pytest.approx(16)
    d = topo.ShapeDict()
    f = topo.faces(a)[0]
    d[f] = "first"
    assert d[f] == "first" and f in d and topo.faces(b)[0] not in d
    with pytest.raises(KeyError):
        d[topo.faces(b)[0]]


def test_step_export_in_process(tmp_path):
    from openfusion.export import export
    r = ok("a = box(length=2, width=2, height=2)\nb = sphere(r=1, at=world.point(5, 0, 0))\nref = box(length=1, width=1, height=1, role='reference')")
    out = tmp_path / "x.step"
    assert export(r.bodies.values(), str(out)) == 2               # reference bodies stay out
    text = out.read_text()
    assert text.startswith("ISO-10303-21") and "MANIFOLD_SOLID_BREP" in text


def test_workspace_host_defaults_do_nothing():
    from openfusion.gui.workspace import Host
    h = Host()
    assert h.open_dialog() is None and h.save_dialog("a.ofs", "ofs") is None
    assert h.reveal("/x") is None and h.document_changed(None) is None


def test_params_dicts_keywords_and_overrides():
    r = ok('params({"w": 4, "h": 2}, {"d": 1}, h=3)\nb = box(length=P.w, width=P.d, height=P.h)', w=5)
    assert r.params == {"w": 5, "h": 3, "d": 1} and vol(r, "b") == pytest.approx(15)
    r = build('params(["w", 1])')
    assert "takes a dict" in r.script_error
    r = build('params({"two words": 1})')
    assert "plain name" in r.script_error
    r = build('b = box(length=P.missing, width=1, height=1)')
    assert "no parameter 'missing'" in r.script_error


def test_math_of_the_frustum_still_holds_for_lofts_over_profiles():
    # keeps a simple numeric guard on loft (used through the GUI's Loft tool)
    r = ok('a = profile(r=5)\nb = profile(r=5, at=world.point(0, 0, 10))\nl = loft([a, b])')
    assert vol(r, "l") == pytest.approx(math.pi * 25 * 10, rel=1e-6)


# ===================================================================== operations only the examples used
def test_place_on_bed_puts_a_face_down_at_the_origin():
    r = ok('b = box(length=10, width=20, height=30, at=world.point(5, 5, 5))\nplace_on_bed(b, face="+X")')
    lo, hi = topo.bbox(r.bodies["b"].shape)
    assert lo[2] == pytest.approx(0) and hi[2] - lo[2] == pytest.approx(10)    # stood on its +X face
    assert r.bodies["b"].anchor("+X").origin == pytest.approx((0, 0, 0))
    assert r.bodies["b"].anchor("+X").z == pytest.approx((0, 0, -1))
    r = ok('b = box(length=2, width=2, height=2, at=world.bed.offset(z=5))')
    assert topo.bbox(r.bodies["b"].shape)[0][2] == pytest.approx(5)


def test_scale_uniform_per_axis_and_about_a_point():
    r = ok('a = box(length=10, width=10, height=10)\nscale(a, 2)\n'
           'b = box(length=10, width=10, height=10)\nscale(b, (1, 2, 3))\n'
           'c = box(length=10, width=10, height=10)\nscale(c, 0.5, about=c.anchor("center"))')
    assert vol(r, "a") == pytest.approx(8000) and vol(r, "b") == pytest.approx(6000)
    lo, hi = topo.bbox(r.bodies["c"].shape)
    assert lo == pytest.approx((2.5, 2.5, 2.5)) and hi == pytest.approx((7.5, 7.5, 7.5))
    assert "positive" in err('a = box(length=1, width=1, height=1)\ns = scale(a, (1, 0, 1))', "s")


def test_combine_join_cut_intersect_and_keeping_tools():
    src = 'a = box(length=10, width=10, height=10)\nb = box(length=10, width=10, height=10, at=world.point(5, 0, 0))\n'
    expect = {"join": 1500, "cut": 500, "intersect": 500}
    for op, v in expect.items():
        r = ok(src + f'combine(a, tools=[b], op="{op}")')
        assert vol(r, "a") == pytest.approx(v) and r.bodies["b"].role == "removed"
    r = ok(src + 'intersect(a, tools=[b], keep_tools=True)')
    assert vol(r, "a") == pytest.approx(500) and r.bodies["b"].role == "solid"
    r = ok(src + 'cut(a, tools=[b], keep_tools="reference")')
    assert r.bodies["b"].role == "reference"
    assert "op must be" in err(src + 'x = combine(a, tools=[b], op="xor")', "x")


# ===================================================================== server routes not covered elsewhere
def test_move_recent_and_reveal_routes(tmp_path):
    from openfusion.gui.server import route
    from openfusion.gui.workspace import Host, Workspace
    from openfusion import scriptedit as se
    f = tmp_path / "m.ofs"
    f.write_text("from openfusion import *\na = box(length=1, width=1, height=1)\nb = box(length=2, width=2, height=2)\n")
    ws = Workspace(support_dir=tmp_path / "support")
    ws.file_action("/api/file/open", {"path": str(f)})
    s = ws.session
    route(s, "/api/move", {"name": "b", "after": None})
    assert [x["name"] for x in s.state()["desc"]["features"]] == ["b", "a"]
    assert ws.file_action("/api/file/recent", {})["recent"][0]["name"] == "m"
    with pytest.raises(se.EditError, match="needs the OpenFusion app"):
        ws.file_action("/api/file/reveal", {})

    class H(Host):
        shown = []

        def reveal(self, path):
            self.shown.append(path)
    ws.host = H()
    assert ws.file_action("/api/file/reveal", {}) == {"ok": True} and H.shown == [str(f)]


def test_offset_repair_turns_a_closed_skin_into_a_solid_only_when_it_is_plausible():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from openfusion.features import _as_solid

    def skin(shape):
        ex = TopExp_Explorer(shape, TopAbs_SHELL)
        return ex.Current()
    big = BRepPrimAPI_MakeBox(10, 10, 10).Shape()
    small = BRepPrimAPI_MakeBox(gp_Pnt(2, 2, 2), 6, 6, 6).Shape()
    assert _as_solid(big) is not None                                      # already a solid
    fixed = _as_solid(skin(small), inside_of=big)
    assert fixed is not None and topo.volume(fixed) == pytest.approx(216)
    assert _as_solid(skin(big), inside_of=small) is None                   # pokes outside: never repaired
    assert _as_solid(skin(small), around=big) is None                      # an outward offset must enclose
    assert topo.volume(_as_solid(skin(big), around=small)) == pytest.approx(1000)
