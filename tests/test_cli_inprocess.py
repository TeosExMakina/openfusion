"""The command line, the watch viewer and the server entry points, run in this process
(test_cli_export.py runs the real `python -m openfusion.cli` in a subprocess)."""
import json
import os
import pathlib
import threading
import time
import urllib.request

import pytest

from openfusion import cli

PART = '''from openfusion import *
params({"w": 20 * mm})
plate = box(length=P.w, width=10, height=2)
post = cylinder(r=2, h=5, at=plate.anchor("+Z"))
guide = box(length=1, width=1, height=1, role="reference")
'''


@pytest.fixture
def part(tmp_path):
    f = tmp_path / "part.ofs"
    f.write_text(PART)
    return f


def run(*args):
    return cli.main([str(a) for a in args])


def test_build_reports_and_exports_every_format(part, tmp_path, capsys):
    outs = [tmp_path / f"o.{x}" for x in ("stl", "3mf", "step")]
    assert run("build", part, "--set", "w=3*cm", *sum((["-o", o] for o in outs), [])) == 0
    text = capsys.readouterr().out
    assert "plate" in text and "Wrote" in text and "triangles" in text
    assert all(o.stat().st_size > 100 for o in outs)
    assert outs[2].read_text().startswith("ISO-10303-21")


def test_build_quiet_each_and_chosen_bodies(part, tmp_path, capsys):
    assert run("build", part, "--quiet") == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("3 features")
    assert run("build", part, "-o", tmp_path / "x.stl", "--each") == 0
    assert sorted(p.name for p in tmp_path.glob("x-*.stl")) == ["x-plate.stl", "x-post.stl"]
    assert run("build", part, "-o", tmp_path / "g.3mf", "--each", "--body", "guide") == 0
    assert (tmp_path / "g-guide.3mf").exists()                         # reference bodies can be asked for
    with pytest.raises(SystemExit, match="must end in"):
        run("build", part, "-o", tmp_path / "noext")
    assert run("build", part, "--body", "nope") == 1
    assert "No body named nope" in capsys.readouterr().err


def test_build_failures_and_stale_bodies(tmp_path, capsys):
    f = tmp_path / "bad.ofs"
    f.write_text("from openfusion import *\nb = box(length=5, width=5, height=5)\nfillet(b, edges=b.edges(), r=9)\n")
    assert run("build", f, "-o", tmp_path / "b.stl") == 1
    assert "nothing exported" in capsys.readouterr().err
    assert run("build", f, "--body", "b", "-o", tmp_path / "b.stl") == 1
    assert "Out of date" in capsys.readouterr().err


def test_bad_overrides_and_missing_files(part):
    with pytest.raises(SystemExit, match="name=value"):
        run("build", part, "--set", "w")
    with pytest.raises(SystemExit, match="cannot read"):
        run("build", part, "--set", "w=wide")
    with pytest.raises(SystemExit, match="no such file"):
        run("build", "nowhere.ofs")
    assert cli._overrides(["a = 2*inch", "b=1*cm"]) == {"a": 50.8, "b": 10.0}


def test_new_and_view(tmp_path, capsys):
    f = tmp_path / "n.ofs"
    assert run("new", f) == 0 and "params({" in f.read_text()
    with pytest.raises(SystemExit, match="already exists"):
        run("new", f)
    out = tmp_path / "n.html"
    assert run("view", f, "-o", out, "--set", "width=50") == 0
    html = out.read_text()
    assert html.startswith("<!doctype html>") and '"mode":"static"' in html and "THREE" in html


def test_gui_watch_and_app_hand_over_to_their_servers(part, monkeypatch):
    seen = {}
    monkeypatch.setattr("openfusion.gui.server.serve", lambda path, port, open_browser: seen.update(gui=(path, port, open_browser)))
    monkeypatch.setattr("openfusion.viewer.serve", lambda path, port, ov, open_browser: seen.update(watch=(path, port, ov, open_browser)))
    monkeypatch.setattr("openfusion.app.main", lambda argv: seen.update(app=argv))
    assert run("gui", part, "--port", 9000, "--no-browser") == 0
    assert run("watch", part, "--set", "w=5") == 0
    assert run("app") == 0 and run("app", part) == 0
    assert seen["gui"] == (str(part), 9000, False)
    assert seen["watch"] == (str(part), 8765, {"w": 5.0}, True)
    assert seen["app"] == [str(part)]


def test_quiet_occt_survives_a_missing_messenger(monkeypatch):
    import OCP.Message
    monkeypatch.setattr(OCP.Message.Message, "DefaultMessenger_s", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    cli._quiet_occt()                                   # never raises


# ===================================================================== the watch viewer
def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read(), r.headers


def test_viewer_pages():
    from openfusion import viewer
    for mode, needle in (("inline", "<script>"), ("vendor", 'src="vendor/three.min.js"'), ("cdn", "cdn.jsdelivr.net")):
        body = viewer.page_body({"mode": "live", "x": "</script>"}, mode)
        assert needle in body and "<\\/script>" in body                 # boot data can't close the tag
    assert viewer.full_page("<p>hi</p>").endswith("<p>hi</p></body></html>\n")


def test_watch_server_serves_rebuilds_and_tries_parameters(part, capsys):
    from openfusion import viewer
    srv, w = viewer.make_server(str(part), 0, {"w": 30})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert b"OF_BOOT" in get(base + "/")[1]
        assert get(base + "/vendor/three.min.js")[0] == 200
        v0 = json.loads(get(base + "/api/version")[1])["version"]
        sc = json.loads(get(base + "/api/scene")[1])
        assert sc["params"]["w"] == 30
        st, data, hd = get(base + "/api/export?fmt=stl")
        assert data[:5] != b"solid" or len(data) > 100
        assert 'filename="part.stl"' in hd["Content-Disposition"]
        for bad in ("/api/export?fmt=obj", "/nothing", "/vendor/missing.js"):
            with pytest.raises(urllib.error.HTTPError):
                get(base + bad)
        req = urllib.request.Request(base + "/api/params", data=b'{"w": 12}', method="POST")
        urllib.request.urlopen(req).read()
        assert json.loads(get(base + "/api/scene")[1])["params"]["w"] == 12
        assert json.loads(get(base + "/api/version")[1])["version"] == v0 + 1
        for path, body in (("/api/params", b"not json"), ("/api/other", b"{}")):
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(urllib.request.Request(base + path, data=body, method="POST"))
        # an edit on disk is picked up by the polling loop
        threading.Thread(target=w.loop, args=(0.05,), daemon=True).start()
        time.sleep(0.1)
        part.write_text(PART.replace("20 * mm", "25 * mm") + "oops = box(length=0, width=1, height=1)\n")
        os.utime(part, ns=(time.time_ns(), time.time_ns() + 10**9))
        for _ in range(100):
            if w.version > v0 + 1:
                break
            time.sleep(0.05)
        assert w.version > v0 + 1 and w.result.params["w"] == 12        # the trial value still applies
        # a broken script is reported, not fatal
        part.write_text("from openfusion import *\nthis is not python\n")
        os.utime(part, ns=(time.time_ns(), time.time_ns() + 2 * 10**9))
        for _ in range(100):                     # the polling loop or this poll picks it up, whichever is first
            if w.poll() or w.result.script_error:
                break
            time.sleep(0.05)
        assert w.result.script_error
        part.unlink()
        assert w.poll() is False
    finally:
        srv.shutdown()
        srv.server_close()
    log = capsys.readouterr().out
    assert "trying w=12" in log and "error: oops" in log and "script error" in log


def test_watcher_keeps_going_when_a_rebuild_throws(part, monkeypatch, capsys):
    from openfusion.viewer import Watcher
    w = Watcher(str(part))
    monkeypatch.setattr(w, "rebuild", lambda reason="": (_ for _ in ()).throw(RuntimeError("boom")))
    assert w.poll() is False and "rebuild failed: boom" in capsys.readouterr().out


def test_serve_functions_start_and_stop(part, monkeypatch, capsys):
    from http.server import ThreadingHTTPServer

    from openfusion import viewer
    from openfusion.gui import server
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", lambda self, *a: (_ for _ in ()).throw(KeyboardInterrupt))
    server.serve(str(part), 0)
    viewer.serve(str(part), 0, None)
    viewer.serve(str(part), 0, None, open_browser=False)
    out = capsys.readouterr().out
    assert "OpenFusion GUI: http://127.0.0.1:" in out and "is watching" in out
    assert len(opened) == 2 and all(u.startswith("http://127.0.0.1:") for u in opened)
    with pytest.raises(SystemExit, match="no such file"):
        server.serve("missing.ofs", 0)


def test_static_page_embeds_the_scene(part):
    from openfusion import Engine
    from openfusion.viewer import static_page
    html = static_page(Engine().run_file(str(part)), str(part))
    boot = html.split("window.OF_BOOT = ", 1)[1].split(";", 1)[0]
    assert {b["id"] for b in json.loads(boot.replace("<\\/", "</"))["scene"]["bodies"]} >= {"plate", "post"}
    assert pathlib.Path(part).name in html
