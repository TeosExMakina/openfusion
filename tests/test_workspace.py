"""Desktop-app document handling: start screen, new/open/recent, examples, exports via dialogs."""
import json
import pathlib
import threading
import urllib.request

import pytest

from openfusion import scriptedit as se
from openfusion.gui.server import Session, make_handler
from openfusion.gui.workspace import NEW_PART, Host, Workspace

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"


class FakeHost(Host):
    """Answers dialogs from a queue, like a user clicking."""

    def __init__(self):
        self.answers = []
        self.saved_kinds = []
        self.titles = []
        self.revealed = []

    def open_dialog(self):
        return self.answers.pop(0) if self.answers else None

    def save_dialog(self, default_name, kind):
        self.saved_kinds.append((default_name, kind))
        return self.answers.pop(0) if self.answers else None

    def reveal(self, path):
        self.revealed.append(path)

    def document_changed(self, path):
        self.titles.append(path)


@pytest.fixture
def ws(tmp_path):
    host = FakeHost()
    return Workspace(host=host, support_dir=tmp_path / "support", examples_dir=EXAMPLES, parts_dir=tmp_path / "Parts")


def test_start_screen_lists_examples_and_no_recent(ws):
    st = ws.state()
    assert st["start"] and st["app"] and st["recent"] == []
    names = [e["name"] for e in st["examples"]]
    assert {"showcase", "plate_post", "enclosure"} <= set(names)


def test_new_open_and_recent(ws, tmp_path):
    v0 = ws.version()
    ws.host.answers = [str(tmp_path / "bracket")]          # the user types a name without .ofs
    out = ws.file_action("/api/file/new", {})
    path = tmp_path / "bracket.ofs"
    assert out["opened"] == str(path) and path.read_text() == NEW_PART
    assert ws.version() != v0 and ws.state()["file"] == str(path)
    assert ws.host.saved_kinds[-1] == ("Untitled part.ofs", "ofs")
    assert ws.host.titles[-1] == str(path)
    other = tmp_path / "other.ofs"
    other.write_text("from openfusion import *\nb = box(length=1, width=1, height=1)\n")
    ws.host.answers = [str(other)]
    ws.file_action("/api/file/open", {})
    assert [r["name"] for r in ws.recent()] == ["other", "bracket"]
    # recent survives a restart
    again = Workspace(host=FakeHost(), support_dir=ws.support_dir, examples_dir=EXAMPLES)
    assert [r["name"] for r in again.state()["recent"]] == ["other", "bracket"]
    # cancelling a dialog changes nothing
    v = ws.version()
    assert ws.file_action("/api/file/open", {}) == {"cancelled": True} and ws.version() == v


def test_open_rejects_non_parts(ws, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    with pytest.raises(se.EditError, match=".ofs"):
        ws.open(str(f))
    with pytest.raises(se.EditError, match="no such file"):
        ws.open(str(tmp_path / "missing.ofs"))


def test_examples_open_as_copies(ws):
    ws.file_action("/api/file/example", {"name": "plate_post"})
    copy = pathlib.Path(ws.session.path)
    assert copy.parent == ws.parts_dir and copy.read_bytes() == (EXAMPLES / "plate_post.ofs").read_bytes()
    assert ws.session.result.ok
    # editing the copy never touches the example; opening it again keeps the edited copy
    copy.write_text(copy.read_text() + "\n# mine\n")
    ws.file_action("/api/file/example", {"name": "plate_post"})
    assert pathlib.Path(ws.session.path).name == "plate_post-2.ofs"
    with pytest.raises(se.EditError):
        ws.file_action("/api/file/example", {"name": "../secrets"})


def test_export_saves_where_the_user_chooses(ws, tmp_path):
    ws.file_action("/api/file/example", {"name": "plate_post"})
    dest = tmp_path / "out" / "plate.stl"
    dest.parent.mkdir()
    ws.host.answers = [str(dest)]
    out = ws.file_action("/api/file/export", {"fmt": "stl", "bodies": ["plate"]})
    assert out["path"] == str(dest) and dest.stat().st_size == out["size"] > 84
    assert ws.host.saved_kinds[-1] == ("plate_post-plate.stl", "stl")
    assert ws.file_action("/api/file/export", {"fmt": "stl", "bodies": ["plate"]}) == {"cancelled": True}


def test_browser_mode_has_no_dialogs(tmp_path):
    f = tmp_path / "p.ofs"
    f.write_text(NEW_PART)
    ws = Workspace(Session(str(f)), support_dir=tmp_path / "s")
    assert ws.state()["app"] is False and "start" not in ws.state()
    with pytest.raises(se.EditError, match="needs the OpenFusion app"):
        ws.file_action("/api/file/open", {})


def test_http_start_screen_then_open(ws, tmp_path):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ws))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST")
        return json.loads(urllib.request.urlopen(req).read())
    try:
        st = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert st["start"] is True
        with pytest.raises(urllib.error.HTTPError) as e:
            post("/api/apply", {"op": "box", "args": {}})
        assert e.value.code == 409
        post("/api/file/example", {"name": "showcase"})
        st = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert st["file"].endswith("showcase.ofs") and st["scene"]["bodies"]
        v1 = json.loads(urllib.request.urlopen(base + "/api/version").read())["version"]
        post("/api/file/close")
        v2 = json.loads(urllib.request.urlopen(base + "/api/version").read())["version"]
        assert v1 != v2
    finally:
        srv.shutdown()
