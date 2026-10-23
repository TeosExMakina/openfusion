"""The desktop shell, with a stand-in for pywebview (no window system here)."""
import json
import pathlib
import sys
import types
import urllib.request

import pytest

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"


class FakeWindow:
    def __init__(self, title, url, **kw):
        self.title, self.url, self.kw = title, url, kw
        self.dialog_answers = []
        self.js = []

    def create_file_dialog(self, kind, directory="", allow_multiple=False, save_filename="", file_types=()):
        self.last_dialog = (kind, directory, save_filename, file_types)
        return self.dialog_answers.pop(0) if self.dialog_answers else None

    def set_title(self, t):
        self.title = t

    def evaluate_js(self, code):
        self.js.append(code)


@pytest.fixture
def fake_webview(monkeypatch):
    wv = types.ModuleType("webview")
    menu = types.ModuleType("webview.menu")

    class Menu:
        def __init__(self, title, items=()):
            self.title, self.items = title, list(items)

    class MenuAction:
        def __init__(self, title, function):
            self.title, self.function = title, function

    class MenuSeparator:
        pass
    menu.Menu, menu.MenuAction, menu.MenuSeparator = Menu, MenuAction, MenuSeparator
    wv.FileDialog = types.SimpleNamespace(OPEN=10, FOLDER=20, SAVE=30)
    wv.menu = menu
    state = {}

    def create_window(title, url, **kw):
        state["window"] = FakeWindow(title, url, **kw)
        return state["window"]

    def start(menu=(), private_mode=True, storage_path=None, **kw):
        state.update(menu=menu, private_mode=private_mode, storage_path=storage_path)
        state["run"](state)             # the test drives the "running app" here
    wv.create_window, wv.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", wv)
    monkeypatch.setitem(sys.modules, "webview.menu", menu)
    return state


def test_app_starts_on_the_start_screen_and_opens_parts(fake_webview, tmp_path, monkeypatch):
    from openfusion import app
    monkeypatch.setenv("OPENFUSION_SUPPORT", str(tmp_path / "support"))
    monkeypatch.setenv("OPENFUSION_EXAMPLES", str(EXAMPLES))
    monkeypatch.setattr("openfusion.gui.workspace.default_parts_dir", lambda: tmp_path / "Parts")
    seen = {}

    def run(state):
        w = state["window"]
        base = w.url.rstrip("/")
        seen["start"] = json.loads(urllib.request.urlopen(base + "/api/state").read())
        # File > Open Example > Showcase, from the native menu
        file_menu = state["menu"][0]
        example_menu = next(i for i in file_menu.items if getattr(i, "title", "") == "Open Example")
        next(a for a in example_menu.items if a.title == "Showcase").function()
        seen["opened"] = json.loads(urllib.request.urlopen(base + "/api/state").read())
        seen["title"] = w.title
        # Export through the native Save dialog
        dest = tmp_path / "showcase.3mf"
        w.dialog_answers = [str(tmp_path / "showcase")]      # user typed a name without the extension
        req = urllib.request.Request(base + "/api/file/export", data=json.dumps({"fmt": "3mf"}).encode(), method="POST")
        seen["export"] = json.loads(urllib.request.urlopen(req).read())
        seen["export_file"] = dest.exists()
        # New part via the menu: Save dialog, empty part opens
        w.dialog_answers = [str(tmp_path / "Parts" / "widget.ofs")]
        next(a for a in file_menu.items if getattr(a, "title", "").startswith("New Part")).function()
        seen["new"] = json.loads(urllib.request.urlopen(base + "/api/state").read())
        # Documents… opens the panel in the page; Save a Copy As… asks where, then works in the copy
        next(a for a in file_menu.items if getattr(a, "title", "").startswith("Documents")).function()
        seen["docs_js"] = w.js[-1]
        w.dialog_answers = [str(tmp_path / "Parts" / "widget v2.ofs")]
        next(a for a in file_menu.items if getattr(a, "title", "").startswith("Save a Copy")).function()
        seen["copy"] = json.loads(urllib.request.urlopen(base + "/api/state").read())["file"]

    fake_webview["run"] = run
    ws = app.main([], exit_process=False)
    assert seen["start"]["start"] and seen["start"]["app"]
    assert {e["name"] for e in seen["start"]["examples"]} >= {"showcase", "plate_post"}
    assert seen["opened"]["file"].endswith("showcase.ofs") and seen["title"] == "showcase.ofs — OpenFusion"
    assert seen["export"]["path"].endswith("showcase.3mf") and seen["export_file"]
    assert seen["new"]["file"].endswith("widget.ofs") and seen["new"]["scene"]["bodies"] == []
    assert fake_webview["private_mode"] is False and "webview" in fake_webview["storage_path"]
    assert "openDocs" in seen["docs_js"] and seen["copy"].endswith("widget v2.ofs")
    assert [r["name"] for r in ws.recent()] == ["widget v2", "widget", "showcase"]
    assert fake_webview["window"].kw["min_size"] == (960, 640)


def test_app_opens_a_part_given_on_the_command_line(fake_webview, tmp_path, monkeypatch):
    from openfusion import app
    monkeypatch.setenv("OPENFUSION_SUPPORT", str(tmp_path / "support"))
    part = tmp_path / "p.ofs"
    part.write_text("from openfusion import *\nb = box(length=5, width=5, height=5)\n")
    fake_webview["run"] = lambda state: None
    ws = app.main([str(part)], exit_process=False)
    assert ws.session.path == str(part) and fake_webview["window"].title == "p.ofs — OpenFusion"


def test_every_menu_item_and_native_dialog(fake_webview, tmp_path, monkeypatch):
    from openfusion import app
    monkeypatch.setenv("OPENFUSION_SUPPORT", str(tmp_path / "support"))
    monkeypatch.setenv("OPENFUSION_EXAMPLES", str(EXAMPLES))
    monkeypatch.setattr("openfusion.gui.workspace.default_parts_dir", lambda: tmp_path / "Parts")
    ran = []
    monkeypatch.setattr("subprocess.run", lambda cmd, check=False: ran.append(cmd))
    part = tmp_path / "a.ofs"
    part.write_text("from openfusion import *\nb = box(length=5, width=5, height=5)\n")
    seen = {}

    def run(state):
        w = state["window"]
        menus = {m.title: m for m in state["menu"]}
        items = {getattr(i, "title", ""): i for i in menus["File"].items}
        act = lambda title: next(v for k, v in items.items() if k.startswith(title)).function()
        # Open… : the dialog answers a path (pywebview returns a tuple), then nothing (cancelled)
        w.dialog_answers = [(str(part),), None]
        act("Open")
        seen["opened"] = w.title
        act("Open")                                    # cancelled: stays on a.ofs
        seen["dialog_dir"] = w.last_dialog[1]
        act("Show in Finder")
        act("Export")
        menus["View"].items[0].function()
        menus["Help"].items[0].function()
        seen["js"] = list(w.js)
        act("Close Part")
        seen["closed"] = w.title
        # a failing action becomes a toast in the page
        monkeypatch.setattr(app.Workspace, "file_action", lambda self, p, req: (_ for _ in ()).throw(RuntimeError("no disk")))
        act("Open")
        seen["toast"] = w.js[-1]

    fake_webview["run"] = run
    app.main([], exit_process=False)
    assert seen["opened"] == "a.ofs — OpenFusion" and seen["dialog_dir"] == str(tmp_path)
    assert ran and ran[0][-1] in (str(part), str(tmp_path))
    assert any("openExport" in j for j in seen["js"]) and any("toggleEditor" in j for j in seen["js"])
    assert any("toggleHelp(true)" in j for j in seen["js"])
    assert seen["closed"] == "OpenFusion"
    assert "ofToast" in seen["toast"] and "no disk" in seen["toast"]


def test_host_dialog_details(tmp_path, monkeypatch, fake_webview):
    from openfusion import app
    from openfusion.gui.workspace import Workspace
    monkeypatch.setattr("openfusion.app.default_parts_dir", lambda: tmp_path / "Parts")
    ws = Workspace(support_dir=tmp_path)
    host = app.WebviewHost(ws)
    app._js(host, "ignored")                           # no window yet: nothing happens
    host.document_changed("x.ofs")
    host.window = FakeWindow("t", "u")
    assert host.open_dialog() is None                  # cancelled
    assert (tmp_path / "Parts").is_dir()               # first dialog starts in the Parts folder
    host.window.dialog_answers = ["/some/where/part.ofs", ["/else/x.stl"], None]
    assert host.open_dialog() == "/some/where/part.ofs" and host.last_dir == "/some/where"
    assert host.save_dialog("x.stl", "stl") == "/else/x.stl"
    assert host.save_dialog("x.stl", "stl") is None
    ran = []
    monkeypatch.setattr("subprocess.run", lambda cmd, check=False: ran.append(cmd))
    for plat, cmd in (("darwin", ["open", "-R", "/a/b.ofs"]), ("linux", ["xdg-open", "/a"])):
        monkeypatch.setattr(app.sys, "platform", plat)
        host.reveal("/a/b.ofs")
        assert ran[-1] == cmd

    class Broken(FakeWindow):
        def evaluate_js(self, code):
            raise RuntimeError("window closed")

        def set_title(self, t):
            raise RuntimeError("window closed")
    host.window = Broken("t", "u")
    app._js(host, "x")                                 # swallowed
    host.document_changed(None)
