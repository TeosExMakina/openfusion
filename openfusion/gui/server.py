"""`openfusion gui`: the modeling app, served locally.

The script file stays the single source of truth. Every GUI action becomes
one surgical edit of that file (see scriptedit.py), is pushed onto an undo
stack, written to disk and rebuilt. Edits made in another editor are picked
up by a file watcher and become undo steps too.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler

from ..localserver import LocalServer
from urllib.parse import parse_qs, urlparse

from .. import scriptedit as se
from ..document import Engine
from ..export import FLAT_KINDS, FORMATS, QUALITY, body_report, export_bundle, scene

STATIC = pathlib.Path(__file__).resolve().parent / "static"
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".txt": "text/plain"}


class Session:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.engine = Engine()
        self.lock = threading.RLock()
        self.text = pathlib.Path(self.path).read_text(encoding="utf-8")
        self.mtime = os.stat(self.path).st_mtime_ns
        self.undo: list[tuple[str, str]] = []   # (text, label)
        self.redo: list[tuple[str, str]] = []
        self.marker: str | None = None          # show the model up to this feature
        self.version = 0
        self.result = None
        self.scene = None
        self.desc = None
        self.last_ms = {}
        self.good = None        # last (desc, scene) that parsed, shown while the script has a syntax error
        self.stale = False
        self.rebuild()

    # ------------------------------------------------------------ building
    def _limit(self, desc) -> int | None:
        if self.marker is None or not desc.get("gui"):
            return None
        names = [f["name"] for f in desc["features"]]
        return names.index(self.marker) + 1 if self.marker in names else None

    def build(self, text: str, limit=None):
        t0 = time.perf_counter()
        res = self.engine.run_source(text, path=self.path, limit=limit)
        t1 = time.perf_counter()
        sc = scene(res, self.path)
        t2 = time.perf_counter()
        return res, sc, {"build": round((t1 - t0) * 1000, 1), "mesh": round((t2 - t1) * 1000, 1)}

    def rebuild(self):
        with self.lock:
            desc = se.describe(self.text)
            if not desc["ok"] and self.good is not None:
                # keep showing the last model that parsed; the banner explains why it is stale
                self.desc = {**self.good[0], "ok": False, "error": desc["error"]}
                self.scene, self.stale = self.good[1], True
                self.version += 1
                return
            self.desc, self.stale = desc, False
            if self.marker and self.marker not in [f["name"] for f in self.desc["features"]]:
                self.marker = None
            self.result, self.scene, self.last_ms = self.build(self.text, self._limit(self.desc))
            if desc["ok"]:
                self.good = (self.desc, self.scene)
            self.version += 1

    # ------------------------------------------------------------ writing
    def write(self, new_text: str, label: str):
        with self.lock:
            if new_text == self.text:
                return
            self.undo.append((self.text, label))
            self.undo = self.undo[-200:]
            self.redo.clear()
            self._commit(new_text)

    def _commit(self, new_text: str):
        tmp = self.path + ".tmp"
        pathlib.Path(tmp).write_text(new_text, encoding="utf-8")
        os.replace(tmp, self.path)
        self.text = new_text
        self.mtime = os.stat(self.path).st_mtime_ns
        self.rebuild()

    def undo_step(self):
        with self.lock:
            if not self.undo:
                raise se.EditError("nothing to undo")
            text, label = self.undo.pop()
            self.redo.append((self.text, label))
            self._commit(text)
            return label

    def redo_step(self):
        with self.lock:
            if not self.redo:
                raise se.EditError("nothing to redo")
            text, label = self.redo.pop()
            self.undo.append((self.text, label))
            self._commit(text)
            return label

    def check_disk(self) -> bool:
        """Pick up edits from another editor."""
        try:
            m = os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            return False
        if m == self.mtime:
            return False
        with self.lock:
            self.mtime = m
            disk = pathlib.Path(self.path).read_text(encoding="utf-8")
            if disk == self.text:
                return False
            self.undo.append((self.text, "edit in another editor"))
            self.redo.clear()
            self.text = disk
            self.rebuild()
            return True

    # ------------------------------------------------------------ GUI actions
    def _after(self):
        """Where new features go: after the marker, or at the end."""
        return self.marker

    def candidate(self, req: dict) -> tuple[str, str | None, str]:
        """Text with the tool's statement applied (not written). Returns (text, feature name, label)."""
        op, args = req["op"], {k: v for k, v in req.get("args", {}).items() if v not in (None, "")}
        if req.get("edit"):
            name = req["edit"]
            st = next(f for f in self.desc["features"] if f["name"] == name)
            changes = {k: args.get(k) for k in set(st["args"]) | set(args)}
            changes = {k: v for k, v in changes.items() if st["args"].get(k) != v}
            return se.set_args(self.text, name, changes), name, f"edit {name}"
        text, name = se.insert_feature(self.text, op, args, name=req.get("name") or None, after=self._after())
        return text, name, f"{op} {name}"

    def preview(self, req: dict) -> dict:
        with self.lock:
            text, name, _ = self.candidate(req)
            desc = se.describe(text)
            limit = None
            if self.marker and desc.get("gui"):
                names = [f["name"] for f in desc["features"]]
                limit = names.index(name) + 1 if req.get("edit") is None else self._limit(desc)
            res, sc, ms = self.build(text, limit)
            fr = next((r for r in res.results if r.name == name), None)
            return {"scene": sc, "name": name, "ms": ms, "statement": _statement(text, name),
                    "result": fr.as_dict() if fr else None, "script_error": res.script_error}

    def statement(self, req: dict) -> dict:
        """The line a tool would write, without building anything."""
        with self.lock:
            text, name, _ = self.candidate(req)
            return {"name": name, "statement": _statement(text, name)}

    UPLOAD_KINDS = (".step", ".stp", ".stl", ".3mf")

    def upload(self, name: str, data: str) -> dict:
        """A file for the part (Import): saved next to it, so the script names it by its own
        name. A different file of the same name already there is kept; this one gets -2, -3..."""
        import base64
        base = os.path.basename(name.replace("\\", "/")).strip()
        stem, ext = os.path.splitext(base)
        if ext.lower() not in self.UPLOAD_KINDS or not stem or stem.startswith("."):
            raise se.EditError("Import takes .step, .stp, .stl and .3mf files")
        try:
            blob = base64.b64decode(data, validate=True)
        except Exception:
            raise se.EditError("the file didn't arrive whole; try again") from None
        folder = os.path.dirname(self.path)
        out, k = base, 2
        while os.path.exists(os.path.join(folder, out)):
            if pathlib.Path(folder, out).read_bytes() == blob:
                break
            out = f"{stem}-{k}{ext}"
            k += 1
        dst = os.path.join(folder, out)
        if not os.path.exists(dst):
            pathlib.Path(dst).write_bytes(blob)
        return {"name": out, "renamed": out != base, "size": len(blob)}

    def sketch_source(self, name: str) -> dict:
        """A sketch statement's arguments, taken apart for editing: plain numbers as numbers,
        anything else (an anchor, a projected edge, P.width) as its source text."""
        from .sketchsrc import sketch_args
        with self.lock:
            st = next((f for f in self.desc["features"] if f["name"] == name), None)
            if st is None or st["op"] != "sketch":
                raise se.EditError(f"'{name}' is not a sketch")
            return sketch_args(st["args"])

    def inspect(self, req: dict) -> dict:
        """Run a tool's step without writing it and report what the app needs to draw it:
        the statement, its status, and for patterns the directions and one transform per
        copy (copy = transform @ seed, row-major 4x4). Bodies are not meshed."""
        import numpy as np
        with self.lock:
            text, name, _ = self.candidate(req)
            out = {"name": name, "statement": _statement(text, name), "result": None, "info": {}, "copies": []}
            res = self.engine.run_source(text, path=self.path, limit=self._limit_for(text, name))
            if res.script_error:
                out["error"] = res.script_error
                return out
            r = next((x for x in res.results if x.name == name), None)
            if r is None:
                return out
            out["result"] = r.as_dict()
            out["info"] = r.info
            seed = res.bodies.get(r.info.get("seed")) if r.info else None
            if seed is not None:
                inv = np.linalg.inv(seed.frame.matrix())
                k = 1
                while f"{name}_{k}" in res.bodies:
                    m = res.bodies[f"{name}_{k}"].frame.matrix() @ inv
                    out["copies"].append([round(float(v), 6) for v in m.ravel()])
                    k += 1
            return out

    def _limit_for(self, text, name):
        """Stop the build right after `name` (later features don't matter for its preview)."""
        try:
            d = se.describe(text)
            if not d.get("gui"):      # loops/functions: script lines don't map to features one to one
                return None
            names = [f["name"] for f in d["features"]]
            return names.index(name) + 1
        except (ValueError, KeyError):
            return None

    def resolve(self, expr: str) -> dict:
        """The frame an anchor expression stands for, at the end of the timeline (or the marker).
        Used by the app to draw previews for typed anchors without building the tool's operation."""
        import re
        with self.lock:
            if re.search(r"[\n;]|__|\bimport\b|\bexec\b|\beval\b|\bopen\b", expr):
                raise se.EditError("not an anchor expression")
            probe = "of_resolve_probe"
            text = self.text.rstrip("\n") + f"\n{probe} = box(length=1, width=1, height=1, at={expr})\n"
            res = self.engine.run_source(text, path=self.path)
            r = next((x for x in res.results if x.name == probe), None)
            if r is None or r.status not in ("ok", "warning") or probe not in res.bodies:
                raise se.EditError((r.message if r else res.script_error) or f"cannot resolve {expr}")
            fr = res.bodies[probe].frame
            return {k: [round(float(c), 6) for c in v] for k, v in fr.as_dict().items()}

    def selection_info(self, items: list) -> dict:
        """Measurements of the current selection, like Fusion's selection readout: per item
        (edge length or radius, face area and kind, body volume) and, for two items, the
        shortest distance between them (and the angle when both are flat or straight)."""
        from .measure import describe_selection
        with self.lock:
            res = self.result
            if res is None:
                return {"items": [], "pair": None}
            return describe_selection(res, items)

    def apply(self, req: dict) -> dict:
        with self.lock:
            text, name, label = self.candidate(req)
            # nothing is written unless the step builds: errors stay in the dialog
            res = self.engine.run_source(text, path=self.path)
            if res.script_error:
                raise se.EditError(res.script_error)
            r = next((x for x in res.results if x.name == name), None)
            if r is not None and r.status in ("error", "skipped"):
                raise se.EditError(r.message or f"{name} could not be built")
            self.write(text, label)
            if req.get("edit") is None and self.marker is not None:
                self.marker = name
                self.rebuild()
            return {"name": name}

    def delete(self, name: str, mode: str):
        with self.lock:
            if mode == "check":
                body = next((b for b in self.scene["bodies"] if b["id"] == name), None)
                return {"dependents": se.dependents(self.text, name),
                        "can_remove": bool(body and body["role"] != "removed")}
            if mode == "remove":
                body = next((b for b in self.scene["bodies"] if b["id"] == name), None)
                if not body or body["role"] == "removed":
                    raise se.EditError(f"'{name}' is already removed at the end of the timeline")
                text, n = se.insert_feature(self.text, "remove", {"body": name}, after=self._after())
                self.write(text, f"remove {name}")
                return {"name": n}
            text = se.delete_feature(self.text, name, cascade=(mode == "cascade"))
            if self.marker is not None:
                # stay rolled back: if the marker's step (or one before it) goes, it moves to
                # the nearest step before it that is still there
                names = [f["name"] for f in self.desc.get("features", [])]
                left = {f["name"] for f in se.describe(text).get("features", [])}
                if self.marker in names and self.marker not in left:
                    before = [n for n in names[:names.index(self.marker)] if n in left]
                    self.marker = before[-1] if before else None
            self.write(text, f"delete {name}")
            return {}

    def exprs(self) -> dict:
        """How each body is written in the script: its variable, or group[k] for a
        pattern copy. None means the statement that made it has no name yet."""
        import re
        variables = {f["name"] for f in self.desc.get("features", []) if f.get("assigned")}
        groups = {f["name"] for f in self.desc.get("features", []) if f["op"] in ("pattern_rect", "pattern_circ") and f.get("assigned")}
        out = {}
        for b in (self.scene or {}).get("bodies", []):
            bid = b["id"]
            m = re.match(r"^(.+)_(\d+)$", bid)
            if bid in variables:
                out[bid] = bid
            elif m and m.group(1) in groups:
                out[bid] = f"{m.group(1)}[{m.group(2)}]"
            else:
                out[bid] = None
        return out

    # ------------------------------------------------------------ export
    def _full_build(self):
        """Exports always use the whole timeline, even while the view is rolled back."""
        res = self.engine.run_source(self.text, path=self.path)
        if res.script_error:
            raise se.EditError(f"the script has an error (line {res.script_error_line}); fix it before exporting")
        return res

    def export_info(self, quality="standard") -> dict:
        tol = QUALITY.get(quality)
        if tol is None:
            raise se.EditError("quality must be draft, standard or fine")
        with self.lock:
            res = self._full_build()
            out = []
            for b in res.bodies.values():
                if b.role == "removed" or b.kind in FLAT_KINDS:
                    continue
                stale = res.stale.get(b.id)
                problem = f"out of date: {stale} failed, so this body doesn't match the script" if stale else None
                out.append(body_report(b, tol, problem))
            return {"file": pathlib.Path(self.path).stem, "quality": quality, "tolerance": tol,
                    "bodies": out, "rolled_back": self.marker is not None,
                    "failed": [r.name for r in res.results if r.status in ("error", "skipped")]}

    def export_file(self, fmt: str, bodies: list | None, each=False, quality="standard"):
        """bodies=None exports every solid body, as the command line does."""
        if fmt not in FORMATS:
            raise se.EditError("format must be 3mf, stl or step")
        tol = QUALITY.get(quality)
        if tol is None:
            raise se.EditError("quality must be draft, standard or fine")
        with self.lock:
            res = self._full_build()
            if bodies is None:
                chosen = [b for b in res.bodies.values() if b.role == "solid"]
                if not res.ok:
                    raise se.EditError("the model has errors; fix them, or pick the bodies to export")
            else:
                chosen = []
                for bid in bodies:
                    b = res.bodies.get(bid)
                    if b is None or b.role == "removed":
                        raise se.EditError(f"there is no body '{bid}' to export")
                    if bid in res.stale:
                        raise se.EditError(f"{bid} is out of date because {res.stale[bid]} failed; fix that step first")
                    chosen.append(b)
            if not chosen:
                raise se.EditError("there are no solid bodies to export")
            try:
                return export_bundle(chosen, fmt, pathlib.Path(self.path).stem, each, tol)
            except (ValueError, RuntimeError) as e:
                raise se.EditError(str(e))

    def state(self) -> dict:
        with self.lock:
            return {"version": self.version, "file": self.path, "text": self.text,
                    "hash": hashlib.sha1(self.text.encode()).hexdigest(),
                    "desc": self.desc, "scene": self.scene, "marker": self.marker, "ms": self.last_ms,
                    "stale": self.stale, "exprs": self.exprs(),
                    "undo": self.undo[-1][1] if self.undo else None,
                    "redo": self.redo[-1][1] if self.redo else None}


def _statement(text: str, name: str) -> str:
    try:
        sc = se.Script(text)
        st = se._feature_stmt(sc, name)
        return text[st.start:st.end]
    except Exception:
        return ""


# ===================================================================== HTTP
def make_handler(target):
    """HTTP handler for a Workspace (desktop app: documents can switch) or a single Session."""
    from .workspace import Workspace
    ws = target if isinstance(target, Workspace) else Workspace(target)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, separators=(",", ":")).encode()
            elif isinstance(body, str):
                body = body.encode()
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
            p = u.path
            if p in ("/", "/index.html"):
                p = "/static/index.html"
            if p.startswith("/static/"):
                f = (STATIC / p[len("/static/"):]).resolve()
                if STATIC in f.parents and f.is_file():
                    return self.send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))
                return self.send(404, {"error": "not found"})
            if p == "/api/version":
                return self.send(200, {"version": ws.version()})
            if p == "/api/state":
                try:
                    return self.send(200, ws.state())
                except Exception as e:
                    return self.send(500, internal_error(ws.session, p, e))
            s = ws.session
            if s is None and p.startswith("/api/"):
                return self.send(409, {"error": "open a part first"})
            if p in ("/api/export", "/api/export_info"):
                qs = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    if p == "/api/export_info":
                        return self.send(200, s.export_info(qs.get("quality", "standard")))
                    bodies = [x for x in qs.get("bodies", "").split(",") if x] or None
                    name, data, _ = s.export_file(qs.get("fmt", "3mf"), bodies, qs.get("each") == "1",
                                                  qs.get("quality", "standard"))
                except se.EditError as e:
                    return self.send(409, {"error": str(e)})
                return self.send(200, data, "application/octet-stream",
                                 {"Content-Disposition": f'attachment; filename="{name}"', "X-Export-Name": name})
            return self.send(404, {"error": "not found"})

        def do_POST(self):
            p = urlparse(self.path).path
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self.send(400, {"error": "bad JSON"})
            s = ws.session
            try:
                if p.startswith("/api/file/"):
                    out = ws.file_action(p, req)
                elif s is None:
                    return self.send(409, {"error": "open a part first"})
                else:
                    out = route(s, p, req)
            except se.EditError as e:
                deps = None
                if p == "/api/delete" and "used by" in str(e):
                    deps = se.dependents(s.text, req.get("name"))
                return self.send(400, {"error": str(e), "dependents": deps})
            except KeyError as e:
                return self.send(400, {"error": f"missing field {e}"})
            except Exception as e:                      # never drop the connection ("Load failed")
                return self.send(500, internal_error(s, p, e))
            if out is None:
                return self.send(404, {"error": "unknown action"})
            return self.send(200, out)

    return H


def internal_error(s, p, e) -> dict:
    """Something went wrong that isn't the script's fault: log it, then start the engine
    afresh and rebuild the file from zero, so one bad moment can't leave the app stuck."""
    import traceback
    print(f"[{time.strftime('%H:%M:%S')}] internal error in {p}:", flush=True)
    traceback.print_exc()
    recovered = False
    if s is not None:
        try:
            with s.lock:
                s.engine = Engine()
                s.rebuild()
            recovered = True
        except Exception:
            traceback.print_exc()
    msg = f"Internal error ({type(e).__name__}: {e})."
    msg += " The model was rebuilt from scratch; try again." if recovered else " Undo (⌘Z) goes back to the last version."
    return {"error": msg, "internal": True}


def route(s: Session, p: str, req: dict):
    if p == "/api/preview":
        return s.preview(req)
    if p == "/api/statement":
        return s.statement(req)
    if p == "/api/inspect":
        return s.inspect(req)
    if p == "/api/resolve":
        return s.resolve(str(req["expr"]))
    if p == "/api/sketch_source":
        return s.sketch_source(str(req["name"]))
    if p == "/api/upload":
        return s.upload(str(req.get("name", "")), str(req.get("data", "")))
    if p == "/api/sketch_parse":
        from .sketchsrc import parse_text
        try:
            return parse_text(str(req.get("text", "")))
        except ValueError as e:
            raise se.EditError(str(e)) from None
    if p == "/api/selinfo":
        return s.selection_info(list(req.get("items") or []))
    if p == "/api/apply":
        return {**s.apply(req), "state": s.state()}
    if p == "/api/delete":
        out = s.delete(req["name"], req.get("mode", "delete"))
        return out if req.get("mode") == "check" else {**out, "state": s.state()}
    if p == "/api/rename":
        s.write(se.rename_feature(s.text, req["old"], req["new"]), f"rename {req['old']} to {req['new']}")
        if s.marker == req["old"]:
            s.marker = req["new"]
        return {"state": s.state()}
    if p == "/api/fix_names":
        s.write(se.fix_names(s.text), "give reused names their own")
        return {"state": s.state()}
    if p == "/api/move":
        s.write(se.move_feature(s.text, req["name"], req.get("after")), f"move {req['name']}")
        return {"state": s.state()}
    if p == "/api/param":
        if req.get("delete"):
            s.write(se.delete_param(s.text, req["name"]), f"delete parameter {req['name']}")
        elif req.get("rename") is not None:
            s.write(se.rename_param(s.text, req["name"], str(req["rename"]).strip()), f"rename parameter {req['name']}")
        elif req.get("comment") is not None and req.get("value") is None:
            s.write(se.set_param_comment(s.text, req["name"], req["comment"]), f"comment on {req['name']}")
        else:
            text = se.set_param(s.text, req["name"], str(req["value"]))
            if req.get("comment"):                       # a new parameter with its comment: one undo step
                text = se.set_param_comment(text, req["name"], req["comment"])
            s.write(text, f"set {req['name']}")
        return {"state": s.state()}
    if p == "/api/script":
        if req.get("base") and req["base"] != hashlib.sha1(s.text.encode()).hexdigest():
            raise se.EditError("the script changed since you started editing; your edits were kept in the editor")
        s.write(req["text"], "edit script")
        return {"state": s.state()}
    if p == "/api/undo":
        return {"label": s.undo_step(), "state": s.state()}
    if p == "/api/redo":
        return {"label": s.redo_step(), "state": s.state()}
    if p == "/api/marker":
        with s.lock:
            s.marker = req.get("name")
            s.rebuild()
        return {"state": s.state()}
    return None


def watch_forever(ws):
    """Pick up edits made in another editor to whatever document is open."""
    def loop():
        while True:
            try:
                s = ws.session
                if s is not None and s.check_disk():
                    print(f"[{time.strftime('%H:%M:%S')}] picked up an edit from another editor", flush=True)
            except Exception as e:
                print("watch error:", e, flush=True)
            time.sleep(0.3)
    threading.Thread(target=loop, daemon=True).start()


def make_server(path: str, port: int = 8770):
    """The GUI server for one part, not yet serving (port 0 picks a free port)."""
    from .workspace import Workspace
    if not os.path.exists(path):
        raise SystemExit(f"no such file: {path}")
    ws = Workspace(Session(path))
    return LocalServer(("127.0.0.1", port), make_handler(ws)), ws


def serve(path: str, port: int = 8770, open_browser: bool = True):
    srv, ws = make_server(path, port)
    watch_forever(ws)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"OpenFusion GUI: {url}\nEditing {ws.session.path} (Ctrl+C to stop)", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
