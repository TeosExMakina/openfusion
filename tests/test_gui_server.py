"""GUI session: every action is one script edit, undoable, and rebuilt."""
import json
import pathlib
import threading
import time
import urllib.request

import pytest

from openfusion import scriptedit as se
from openfusion.gui.server import Session, make_handler, route

START = '''from openfusion import *

params(w = 40 * mm)

plate = box(length=60, width=P.w, height=4)
'''


@pytest.fixture
def sess(tmp_path):
    f = tmp_path / "part.ofs"
    f.write_text(START)
    return Session(str(f))


def disk(s):
    return pathlib.Path(s.path).read_text()


def test_apply_writes_one_statement_and_builds(sess):
    out = route(sess, "/api/apply", {"op": "cylinder", "args": {"r": "5", "h": "10", "at": 'plate.anchor("+Z")'}})
    assert out["name"] == "body1"
    assert disk(sess) == START + 'body1 = cylinder(r=5, h=10, at=plate.anchor("+Z"))\n'
    names = [b["id"] for b in out["state"]["scene"]["bodies"]]
    assert names == ["plate", "body1"]


def test_preview_does_not_write(sess):
    pv = route(sess, "/api/preview", {"op": "sphere", "args": {"r": "3"}})
    assert pv["result"]["status"] == "ok" and pv["statement"] == "body1 = sphere(r=3)"
    assert disk(sess) == START
    bad = route(sess, "/api/preview", {"op": "sphere", "args": {"r": "-3"}})
    assert bad["result"]["status"] == "error" and "positive" in bad["result"]["message"]


def test_edit_undo_redo(sess):
    route(sess, "/api/apply", {"op": "plate", "edit": "plate", "args": {"length": "80", "width": "P.w", "height": "4"}})
    assert "length=80" in disk(sess)
    u = route(sess, "/api/undo", {})
    assert u["label"] == "edit plate" and disk(sess) == START
    route(sess, "/api/redo", {})
    assert "length=80" in disk(sess)


def test_delete_modes(sess):
    route(sess, "/api/apply", {"op": "sphere", "args": {"r": "2", "at": 'plate.anchor("+Z")'}})
    with pytest.raises(se.EditError, match="used by"):
        route(sess, "/api/delete", {"name": "plate"})
    route(sess, "/api/delete", {"name": "plate", "mode": "remove"})
    assert "remove1 = remove(body=plate)" in disk(sess)
    st = sess.state()
    roles = {b["id"]: b["role"] for b in st["scene"]["bodies"]}
    assert roles["plate"] == "removed" and roles["body1"] == "solid"
    route(sess, "/api/delete", {"name": "plate", "mode": "cascade"})
    assert "plate" not in disk(sess).split("params")[1]


def test_rename_param_marker(sess):
    route(sess, "/api/apply", {"op": "sphere", "args": {"r": "2", "at": 'plate.anchor("+Z")'}})
    route(sess, "/api/rename", {"old": "body1", "new": "knob"})
    assert "knob = sphere(" in disk(sess)
    route(sess, "/api/param", {"name": "w", "value": "50 * mm"})
    route(sess, "/api/param", {"name": "gap", "value": "0.3 * mm"})
    assert "w = 50 * mm" in disk(sess) and "gap = 0.3 * mm" in disk(sess)
    out = route(sess, "/api/marker", {"name": "plate"})
    assert [b["id"] for b in out["state"]["scene"]["bodies"]] == ["plate"]
    # new features go after the marker, and the marker follows them
    out = route(sess, "/api/apply", {"op": "box", "args": {"length": "1", "width": "1", "height": "1"}})
    lines = disk(sess).splitlines()
    assert lines[lines.index("plate = box(length=60, width=P.w, height=4)") + 1].startswith("body1 = box(")
    assert sess.marker == "body1"


def test_outside_edit_is_picked_up_and_undoable(sess):
    time.sleep(0.01)
    pathlib.Path(sess.path).write_text(START + "s = sphere(r=1)\n")
    assert sess.check_disk()
    assert [b["id"] for b in sess.scene["bodies"]] == ["plate", "s"]
    route(sess, "/api/undo", {})
    assert disk(sess) == START


def test_script_save_conflict(sess):
    st = sess.state()
    route(sess, "/api/script", {"text": START + "b = box(length=1, width=1, height=1)\n", "base": st["hash"]})
    with pytest.raises(se.EditError, match="changed"):
        route(sess, "/api/script", {"text": START, "base": st["hash"]})


def test_http_roundtrip(sess):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sess))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        html = urllib.request.urlopen(base + "/").read().decode()
        assert "OpenFusion" in html
        req = urllib.request.Request(base + "/api/apply", data=json.dumps(
            {"op": "box", "args": {"length": "2", "width": "2", "height": "2"}}).encode(), method="POST")
        assert json.loads(urllib.request.urlopen(req).read())["name"] == "body1"
        bad = urllib.request.Request(base + "/api/apply", data=json.dumps(
            {"op": "box", "args": {"nope": "2"}}).encode(), method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert "no parameter" in json.loads(e.value.read())["error"]
        data = urllib.request.urlopen(base + "/api/export?fmt=3mf").read()
        assert data[:2] == b"PK"
    finally:
        srv.shutdown()


def test_body_expressions_for_pattern_copies(sess):
    route(sess, "/api/apply", {"op": "cylinder", "args": {"r": "1", "h": "4"}})
    route(sess, "/api/apply", {"op": "pattern_rect", "args": {"body": "body1", "x": "(3, 5)"}})
    ex = sess.state()["exprs"]
    assert ex["plate"] == "plate" and ex["body1"] == "body1"
    assert ex["pattern_rect1_1"] == "pattern_rect1[1]" and ex["pattern_rect1_2"] == "pattern_rect1[2]"
    out = route(sess, "/api/apply", {"op": "sphere", "args": {"r": "1", "at": 'pattern_rect1[2].anchor("+Z")'}})
    st = out["state"]
    assert next(b for b in st["scene"]["bodies"] if b["id"] == "body1")["role"] == "solid"


def test_delete_check_mode(sess):
    route(sess, "/api/apply", {"op": "sphere", "args": {"r": "2", "at": 'plate.anchor("+Z")'}})
    chk = route(sess, "/api/delete", {"name": "plate", "mode": "check"})
    assert chk == {"dependents": ["body1"], "can_remove": True}
    route(sess, "/api/apply", {"op": "cut", "args": {"target": "plate", "tools": "[body1]"}})
    chk = route(sess, "/api/delete", {"name": "body1", "mode": "check"})
    assert chk["can_remove"] is False          # consumed by the cut already
    with pytest.raises(se.EditError, match="already removed"):
        route(sess, "/api/delete", {"name": "body1", "mode": "remove"})


def test_syntax_error_keeps_last_good_model(sess):
    pathlib.Path(sess.path).write_text(START + "oops = box(length=\n")
    time.sleep(0.01)
    assert sess.check_disk()
    st = sess.state()
    assert st["stale"] is True and st["desc"]["ok"] is False and "SyntaxError" in st["desc"]["error"]
    assert [b["id"] for b in st["scene"]["bodies"]] == ["plate"]


def test_snaps_carry_script_references(sess):
    # a through-hole: the cutter grows into the plate from its top face
    route(sess, "/api/apply", {"op": "cylinder", "args": {"r": "2", "h": "4", "at": 'plate.anchor("+Z").flipped()'}})
    route(sess, "/api/apply", {"op": "cut", "args": {"target": "plate", "tools": "[body1]"}})
    plate = next(b for b in sess.scene["bodies"] if b["id"] == "plate")
    top = next(f for f in plate["faces"] if f["label"] == "+Z")
    refs = [s["ref"] for s in top["snaps"]]
    assert refs[0] == 'plate.anchor("+Z")' and 'plate.face("+Z").hole(0)' in refs
    wall = next(f for f in plate["faces"] if f["label"] == "body1.side")
    assert wall["snaps"][0]["ref"] == 'plate.face("+Z").hole(0)'   # pointing into the hole offers its center
    assert wall["snaps"][0]["z"] == [0.0, 0.0, 1.0]


def test_apply_refuses_a_failing_step_and_writes_nothing(sess):
    before = disk(sess)
    with pytest.raises(se.EditError, match="positive"):
        route(sess, "/api/apply", {"op": "sphere", "args": {"r": "-1"}})
    with pytest.raises(se.EditError, match="too large"):
        route(sess, "/api/apply", {"op": "fillet", "args": {"body": "plate", "edges": "[plate.edges()]", "r": "50"}})
    assert disk(sess) == before


def test_statement_and_resolve(sess):
    out = route(sess, "/api/statement", {"op": "box", "args": {"length": "1", "width": "2", "height": "-3"}})
    assert out == {"name": "body1", "statement": "body1 = box(length=1, width=2, height=-3)"}
    assert disk(sess) == START
    fr = route(sess, "/api/resolve", {"expr": 'plate.anchor("+Z").offset(x=5)'})
    assert fr["o"] == [35.0, 20.0, 4.0] and fr["z"] == [0.0, 0.0, 1.0]
    with pytest.raises(se.EditError):
        route(sess, "/api/resolve", {"expr": "__import__('os')"})
    with pytest.raises(se.EditError):
        route(sess, "/api/resolve", {"expr": 'plate.anchor("nope")'})


EXPORT_SRC = '''from openfusion import *

plate = box(length=60, width=40, height=4)
post = cylinder(r=5, h=12, at=plate.anchor("+Z"))
clip = box(length=10, width=6, height=8, at=plate.anchor("+Z").offset(x=20))
jig = box(length=8, width=8, height=8, role="reference")
'''


@pytest.fixture
def xsess(tmp_path):
    f = tmp_path / "part.ofs"
    f.write_text(EXPORT_SRC)
    return Session(str(f))


def test_export_info_reports_each_body(xsess):
    info = xsess.export_info()
    by = {b["id"]: b for b in info["bodies"]}
    assert set(by) == {"plate", "post", "clip", "jig"}
    assert by["plate"]["size"] == [60.0, 40.0, 4.0] and by["plate"]["volume"] == 9600.0
    assert by["plate"]["triangles"] == 12 and by["plate"]["stl_bytes"] == 84 + 50 * 12
    assert by["jig"]["role"] == "reference" and all(b["problem"] is None for b in by.values())
    fine = {b["id"]: b for b in xsess.export_info("fine")["bodies"]}
    assert fine["post"]["triangles"] > by["post"]["triangles"]


def test_export_chosen_bodies_one_file_or_one_each(xsess):
    import io, struct, zipfile
    name, data, n = xsess.export_file("stl", ["post"])
    assert name == "part-post.stl" and n == 1
    assert struct.unpack("<I", data[80:84])[0] == xsess.export_info()["bodies"][1]["triangles"]
    name, data, n = xsess.export_file("stl", ["plate", "post"])
    assert name == "part.stl" and n == 2
    name, data, n = xsess.export_file("3mf", ["plate", "post", "jig"], each=True)
    assert name == "part-3mf.zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == ["part-jig.3mf", "part-plate.3mf", "part-post.3mf"]
    # no list: every solid body, never the reference one
    name, data, n = xsess.export_file("3mf", None)
    assert name == "part.3mf" and n == 3


def test_export_quality_is_not_sticky(xsess):
    import struct
    count = lambda q: struct.unpack("<I", xsess.export_file("stl", ["post"], quality=q)[1][80:84])[0]
    std = count("standard")
    assert count("fine") > std
    assert count("standard") == std and count("draft") < std


def test_export_refuses_out_of_date_and_unknown_bodies(xsess):
    route(xsess, "/api/apply", {"op": "fillet", "args": {"body": "plate", "edges": '[plate.face("+Z").edges()]', "r": "1"}})
    pathlib.Path(xsess.path).write_text(disk(xsess).replace("r=1", "r=30"))
    xsess.check_disk()
    info = {b["id"]: b for b in xsess.export_info()["bodies"]}
    assert "out of date" in info["plate"]["problem"] and info["post"]["problem"] is None
    with pytest.raises(se.EditError, match="out of date"):
        xsess.export_file("stl", ["plate"])
    with pytest.raises(se.EditError, match="has errors"):
        xsess.export_file("stl", None)
    with pytest.raises(se.EditError, match="no body 'nope'"):
        xsess.export_file("stl", ["nope"])
    assert xsess.export_file("stl", ["clip"])[0] == "part-clip.stl"   # unaffected bodies still export


def test_export_over_http(xsess):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(xsess))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        info = json.loads(urllib.request.urlopen(base + "/api/export_info?quality=draft").read())
        assert info["quality"] == "draft" and len(info["bodies"]) == 4
        r = urllib.request.urlopen(base + "/api/export?fmt=stl&bodies=plate,post&each=1")
        assert r.headers["X-Export-Name"] == "part-stl.zip" and r.read()[:2] == b"PK"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/export?fmt=stl&bodies=ghost")
        assert e.value.code == 409
    finally:
        srv.shutdown()


def test_inspect_pattern_reports_directions_and_copies_without_writing(sess):
    before = disk(sess)
    out = route(sess, "/api/inspect", {"op": "pattern_rect", "args": {"body": "plate", "x": "(3, -10)", "y": "(2, 5)",
                                                                       "along": 'plate.edge("-Y+Z")'}})
    assert disk(sess) == before
    assert out["statement"].startswith("pattern_rect1 = pattern_rect(") and out["result"]["status"] == "ok"
    info = out["info"]
    assert info["kind"] == "rect" and info["seed"] == "plate"
    assert info["dirs"][0]["d"] == [1.0, 0.0, 0.0] and info["dirs"][0]["s"] == -10
    assert len(out["copies"]) == 5
    m = out["copies"][1]                      # second copy: one step along x (row-major 4x4)
    assert m[3] == -10 and m[7] == 0 and m[11] == 0
    bad = route(sess, "/api/inspect", {"op": "pattern_circ", "args": {"body": "plate", "count": "3", "axis": 'plate.face("+Z")'}})
    assert bad["result"]["status"] == "error" and "flat" in bad["result"]["message"] and not bad["copies"]


SKETCH_ARGS = {
    "at": 'plate.face("+Z")',
    "points": '{\n    "p1": (0, 0),\n    "p2": (20, 1),\n    "p3": (20, 12),\n    "p4": (0, 10),\n    "o": (0, 5)}',
    "curves": '{\n    "l1": ("line", "p1", "p2"),\n    "l2": ("line", "p2", "p3"),\n    "l3": ("line", "p3", "p4"),\n'
              '    "l4": ("line", "p4", "p1"),\n    "c1": ("circle", "o", 2),\n    "g": ("line", "p1", "p3", "construction")}',
    "constraints": '[\n    ("horizontal", "l1"),\n    ("vertical", "l2"),\n    ("horizontal", "l3"),\n    ("vertical", "l4"),\n'
                   '    ("length", "l1", P.w / 2),\n    ("x", "p1", -10)]',
}


def test_sketch_mode_solves_writes_and_reads_back(sess):
    """What sketch mode does: solve on every change without writing, finish with one
    statement, and read it back apart for editing (expressions kept as written)."""
    before = disk(sess)
    out = route(sess, "/api/inspect", {"op": "sketch", "args": SKETCH_ARGS})
    assert disk(sess) == before and out["result"]["status"] == "ok"
    sk = out["info"]["sketch"]
    assert sk["points"]["p2"] == [10.0, 0.0] or abs(sk["points"]["p2"][1] - sk["points"]["p1"][1]) < 1e-9
    assert abs(sk["points"]["p2"][0] - sk["points"]["p1"][0] - 20) < 1e-6          # P.w / 2
    assert sk["curves"]["g"]["construction"] and sk["curves"]["c1"]["radius"] == 2
    assert sk["dof"] > 0 and [r["profile"] for r in sk["regions"]].count(True) == 1
    assert sk["frame"]["z"] == [0.0, 0.0, 1.0] and sk["frame"]["o"][2] == 4
    # a conflict is an error, not an exception
    bad = dict(SKETCH_ARGS, constraints=SKETCH_ARGS["constraints"][:-1] + ', ("vertical", "l1")]')
    res = route(sess, "/api/inspect", {"op": "sketch", "args": bad})["result"]
    assert res["status"] == "error" and "can't all be met" in res["message"]
    name = route(sess, "/api/apply", {"op": "sketch", "args": SKETCH_ARGS})["name"]
    assert name == "sketch1" and "sketch1 = sketch(" in disk(sess)
    src = route(sess, "/api/sketch_source", {"name": name})
    assert src["at"] == 'plate.face("+Z")' and src["points"]["p2"] == [20, 1]
    assert src["curves"]["g"] == ["line", "p1", "p3", "construction"] and src["curves"]["c1"] == ["circle", "o", 2]
    assert src["constraints"][4] == ["length", "l1", {"expr": "P.w / 2"}]
    with pytest.raises(se.EditError):
        route(sess, "/api/sketch_source", {"name": "plate"})
    # editing: a new dimension rewrites the statement in place
    edited = dict(SKETCH_ARGS, constraints=SKETCH_ARGS["constraints"][:-1] + ', ("length", "l2", 7)]')
    route(sess, "/api/apply", {"op": "sketch", "args": edited, "edit": name})
    assert disk(sess).count("sketch(") == 1 and '("length", "l2", 7)' in disk(sess)
    # and the sketch extrudes
    out = route(sess, "/api/apply", {"op": "extrude", "args": {"face": "sketch1", "distance": "3", "op": '"join"'}})
    assert [b["id"] for b in out["state"]["scene"]["bodies"] if b["role"] == "solid"] == ["plate"]


def test_sketch_args_parse_the_written_statement():
    from openfusion.gui.sketchsrc import sketch_args
    a = sketch_args({"at": "plate.anchor('+Z')", "points": '{"a": (0, -2.5), "b": plate.anchor("c")}',
                     "curves": '{"s": ("spline", "a", "b", "closed"), "e": ("project", plate.edge("+X+Z"))}',
                     "constraints": '[("x", "a", -1), ("length", "l", 10 * mm)]'})
    assert a["points"] == {"a": [0, -2.5], "b": {"expr": 'plate.anchor("c")'}}
    assert a["curves"]["e"] == ["project", {"expr": 'plate.edge("+X+Z")'}]
    assert a["constraints"] == [["x", "a", -1], ["length", "l", {"expr": "10 * mm"}]]
    assert sketch_args({"at": None})["points"] == {}
    for bad in ({"points": "make_points()"}, {"constraints": "rules"}):
        with pytest.raises(ValueError):
            sketch_args(bad)
