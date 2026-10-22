"""The parameter table: params({...}) in the script, read and edited by the app."""
import json
import threading
import urllib.request

import pytest

from openfusion import Engine
from openfusion import scriptedit as se
from openfusion.gui.server import Session, make_handler, route

DICT = '''from openfusion import *

params({
    # outer size
    "width": 40 * mm,    # along X
    "depth": 30 * mm,
    "wall":  2 * mm,     # printed wall
})

plate = box(length=P.width, width=P.depth, height=P.wall * 2)
'''

KW = '''from openfusion import *

params(
    width = 40 * mm,
    wall = 2 * mm,
)

plate = box(length=P.width, width=10, height=P.wall)
'''


def test_entries_read_names_expressions_and_comments():
    es = se.param_entries(DICT)
    assert [(e["name"], e["expr"], e["comment"]) for e in es] == [
        ("width", "40 * mm", "along X"), ("depth", "30 * mm", ""), ("wall", "2 * mm", "printed wall")]
    assert es[0]["lead"] == ["outer size"]
    assert se.param_values(DICT) == {"width": "40 * mm", "depth": "30 * mm", "wall": "2 * mm"}
    assert Engine().run_source(DICT).params == {"width": 40.0, "depth": 30.0, "wall": 2.0}
    assert [e["name"] for e in se.param_entries(KW)] == ["width", "wall"]
    assert se.param_entries("from openfusion import *\n") == []


def test_render_lines_up_values_and_comments():
    out = se.render_params([{"name": "a", "expr": "1", "comment": "first", "lead": ["group"]},
                            {"name": "longer", "expr": "2 * mm", "comment": "", "lead": []}])
    assert out == 'params({\n    # group\n    "a":      1,       # first\n    "longer": 2 * mm,\n})'


def test_set_adds_and_changes_in_dict_style():
    t = se.set_param(DICT, "wall", "3 * mm")
    assert '"wall":  3 * mm,' in t and "# printed wall" in t
    t = se.set_param(t, "lid", "1.5 * mm")
    assert '"lid":   1.5 * mm,' in t
    assert Engine().run_source(t).params["lid"] == 1.5
    # a script without a block gets one (dict style) after the imports
    t = se.set_param("from openfusion import *\n\nb = box(length=1, width=1, height=1)\n", "k", "2")
    assert t == 'from openfusion import *\n\nparams({\n    "k": 2,\n})\n\nb = box(length=1, width=1, height=1)\n'
    assert "P.box" not in se.set_param(DICT, "box", "1")           # op names are fine as P.box
    for bad, msg in (("class", "not a valid parameter name"), ("2x", "not a valid parameter name")):
        with pytest.raises(se.EditError, match=msg):
            se.set_param(DICT, bad, "1")
    with pytest.raises(se.EditError, match="not a valid value"):
        se.set_param(DICT, "wall", "2 *")


def test_rename_updates_every_use():
    t = se.rename_param(DICT, "wall", "shell")
    assert '"shell": 2 * mm,' in t and "P.shell * 2" in t and "P.wall" not in t
    assert Engine().run_source(t).ok
    assert se.rename_param(DICT, "wall", "wall") == DICT
    with pytest.raises(se.EditError, match="already a parameter"):
        se.rename_param(DICT, "wall", "depth")
    with pytest.raises(se.EditError, match="no parameter"):
        se.rename_param(DICT, "nope", "x")
    # a keyword block is rewritten as a dict
    t = se.rename_param(KW, "wall", "thick")
    assert 'params({\n    "width": 40 * mm,\n    "thick": 2 * mm,\n})' in t and "height=P.thick" in t


def test_comments_set_clear_and_stay_on_one_line():
    t = se.set_param_comment(DICT, "depth", "along Y # really")
    assert "# along Y really\n" in t
    t = se.set_param_comment(t, "width", "")
    assert '"width": 40 * mm,\n' in t
    with pytest.raises(se.EditError):
        se.set_param_comment(DICT, "nope", "x")


def test_delete_only_unused_parameters():
    with pytest.raises(se.EditError, match="still used by plate"):
        se.delete_param(DICT, "wall")
    t = se.delete_param(DICT.replace("width=P.depth", "width=10"), "depth")
    assert '"depth"' not in t and '"wall"' in t
    t = se.delete_param(KW.replace("P.width", "5"), "width")
    assert "width" not in t.split("plate")[0]
    with pytest.raises(se.EditError, match="no parameter"):
        se.delete_param(DICT, "zzz")


def test_info_lists_users_for_the_panel():
    info = {e["name"]: e["used_by"] for e in se.param_info(DICT)}
    assert info == {"width": ["plate"], "depth": ["plate"], "wall": ["plate"]}
    assert se.param_users("not python (", "x") == []
    assert se.describe(DICT)["param_info"][2]["comment"] == "printed wall"


@pytest.fixture
def sess(tmp_path):
    f = tmp_path / "p.ofs"
    f.write_text(DICT)
    return Session(str(f))


def test_server_routes_change_rename_comment_delete_and_undo(sess):
    st = route(sess, "/api/param", {"name": "wall", "value": "3 * mm"})["state"]
    assert st["scene"]["params"]["wall"] == 3
    st = route(sess, "/api/param", {"name": "wall", "rename": "shell"})["state"]
    assert [e["name"] for e in st["desc"]["param_info"]] == ["width", "depth", "shell"]
    st = route(sess, "/api/param", {"name": "shell", "comment": "outer wall"})["state"]
    assert st["desc"]["param_info"][2]["comment"] == "outer wall"
    route(sess, "/api/param", {"name": "spare", "value": "1"})
    st = route(sess, "/api/param", {"name": "spare", "delete": True})["state"]
    assert "spare" not in st["scene"]["params"]
    route(sess, "/api/undo", {})
    assert "spare" in sess.state()["scene"]["params"]
    with pytest.raises(se.EditError):
        route(sess, "/api/param", {"name": "shell", "delete": True})


def test_param_errors_over_http(sess):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sess))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        def post(body):
            req = urllib.request.Request(base + "/api/param", data=json.dumps(body).encode(), method="POST")
            return json.loads(urllib.request.urlopen(req).read())
        assert post({"name": "depth", "comment": "Y"})["state"]["desc"]["param_info"][1]["comment"] == "Y"
        with pytest.raises(urllib.error.HTTPError) as e:
            post({"name": "depth", "rename": "width"})
        assert e.value.code == 400 and "already a parameter" in json.loads(e.value.read())["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli_overrides_reach_dict_parameters(tmp_path, capsys):
    from openfusion.cli import main
    f = tmp_path / "p.ofs"
    f.write_text(DICT)
    out = tmp_path / "p.stl"
    assert main(["build", str(f), "--set", "wall=1.5", "--set", "width=2*cm", "-o", str(out), "--quiet"]) == 0
    r = Engine().run_file(str(f), {"wall": 1.5, "width": 20})
    assert r.params["width"] == 20 and out.stat().st_size > 0
