"""Viewer page assembly and the live-reload server (`openfusion watch`)."""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = pathlib.Path(__file__).resolve().parent
VENDOR = HERE / "vendor"
CDN = ["https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js",
       "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"]


def page_body(boot: dict, scripts: str = "inline") -> str:
    """The viewer markup. scripts: 'inline' (offline, single file), 'vendor' (served), 'cdn'."""
    html = (HERE / "viewer.html").read_text(encoding="utf-8")
    if scripts == "inline":
        tags = "".join(f"<script>{(VENDOR / n).read_text(encoding='utf-8')}</script>\n"
                       for n in ("three.min.js", "OrbitControls.js"))
    elif scripts == "vendor":
        tags = '<script src="vendor/three.min.js"></script>\n<script src="vendor/OrbitControls.js"></script>\n'
    else:
        tags = "".join(f'<script src="{u}"></script>\n' for u in CDN)
    boot_js = "window.OF_BOOT = " + json.dumps(boot, separators=(",", ":")).replace("</", "<\\/") + ";"
    return html.replace("<!--OF:SCRIPTS-->", tags).replace("/*OF:BOOT*/", boot_js)


def full_page(body: str) -> str:
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            '<style>html,body{margin:0}[hidden]{display:none!important}</style></head><body>'
            + body + "</body></html>\n")


def static_page(result, path=None) -> str:
    from ..export import scene
    return full_page(page_body({"mode": "static", "scene": scene(result, path)}, "inline"))


class Watcher:
    """Rebuilds the script whenever it changes on disk (or parameters are tried in the viewer)."""

    def __init__(self, path: str, overrides: dict | None = None):
        from ..document import Engine
        self.path = os.path.abspath(path)
        self.base_overrides = dict(overrides or {})
        self.trial = {}
        self.engine = Engine()
        self.lock = threading.Lock()
        self.version = 0
        self.scene_json = b"{}"
        self.result = None
        self.mtime = None

    def overrides(self):
        return {**self.base_overrides, **self.trial}

    def rebuild(self, reason=""):
        from ..export import scene
        with self.lock:
            t = time.perf_counter()
            res = self.engine.run_file(self.path, self.overrides())
            sc = scene(res, self.path)
            self.result = res
            self.scene_json = json.dumps(sc, separators=(",", ":")).encode()
            self.version += 1
            print(f"[{time.strftime('%H:%M:%S')}] {reason or 'built'}: {res.summary()} "
                  f"(+{(time.perf_counter() - t) * 1000 - res.ms:.0f} ms meshing)", flush=True)
            if res.script_error:
                print(f"    script error, line {res.script_error_line}: {res.script_error}", flush=True)
            for r in res.results:
                if r.status in ("error", "skipped"):
                    print(f"    {r.status}: {r.name} (line {r.line}): {r.message}", flush=True)

    def poll(self) -> bool:
        """Rebuild if the file changed since the last look. True when it rebuilt."""
        try:
            m = os.stat(self.path).st_mtime_ns
            if m != self.mtime:
                self.mtime = m
                self.rebuild("changed" if self.version else "built")
                return True
        except FileNotFoundError:
            pass
        except Exception as e:  # keep watching whatever happens
            print("rebuild failed:", e, flush=True)
        return False

    def loop(self, interval=0.25):
        while True:
            self.poll()
            time.sleep(interval)


def make_server(path: str, port: int = 8765, overrides: dict | None = None):
    """The watch server and its Watcher, built once, not yet serving (port 0 picks a free port)."""
    w = Watcher(path, overrides)
    w.rebuild("built")
    w.mtime = os.stat(w.path).st_mtime_ns
    page = full_page(page_body({"mode": "live", "scene": None}, "vendor")).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                return self.send(200, page, "text/html; charset=utf-8")
            if u.path.startswith("/vendor/"):
                f = VENDOR / os.path.basename(u.path)
                if f.exists():
                    return self.send(200, f.read_bytes(), "text/javascript")
            if u.path == "/api/version":
                return self.send(200, json.dumps({"version": w.version}).encode(), "application/json")
            if u.path == "/api/scene":
                return self.send(200, w.scene_json, "application/json")
            if u.path == "/api/export":
                fmt = parse_qs(u.query).get("fmt", ["3mf"])[0]
                if fmt not in ("3mf", "stl", "step"):
                    return self.send(400, b"fmt must be 3mf, stl or step", "text/plain")
                import tempfile
                from ..export import export
                name = pathlib.Path(w.path).stem + "." + fmt
                with tempfile.TemporaryDirectory() as d:
                    out = os.path.join(d, name)
                    export(w.result.bodies.values(), out)
                    data = pathlib.Path(out).read_bytes()
                return self.send(200, data, "application/octet-stream",
                                 {"Content-Disposition": f'attachment; filename="{name}"'})
            return self.send(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path == "/api/params":
                n = int(self.headers.get("Content-Length", 0))
                try:
                    w.trial = {k: float(v) for k, v in json.loads(self.rfile.read(n) or b"{}").items()}
                except Exception:
                    return self.send(400, b"bad parameters", "text/plain")
                w.rebuild("trying " + (", ".join(f"{k}={v:g}" for k, v in w.trial.items()) or "script values"))
                return self.send(200, b"{}", "application/json")
            return self.send(404, b"not found", "text/plain")

    return ThreadingHTTPServer(("127.0.0.1", port), H), w


def serve(path: str, port: int = 8765, overrides: dict | None = None, open_browser=True):
    srv, w = make_server(path, port, overrides)
    threading.Thread(target=w.loop, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"OpenFusion is watching {w.path}\nViewer: {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
