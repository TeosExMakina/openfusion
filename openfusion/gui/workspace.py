"""The document the app is working on, and switching between documents.

In the browser (`openfusion gui part.ofs`) there is always exactly one document.
In the desktop app a Workspace starts empty (the start screen) and the app's
Host supplies native Open/Save dialogs, so the same server can open, create and
switch parts, remember recent files and save exports where the user chooses.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import threading
import time

from .. import scriptedit as se

NEW_PART = '''# OpenFusion part
from openfusion import *

'''

EXAMPLE_TITLES = {
    "showcase": "Showcase: every primitive, booleans, fillets, patterns",
    "plate_post": "Plate with posts: anchors, a hole pattern, fillets",
    "enclosure": "Enclosure: box, lid, screw bosses",
}


class Host:
    """What the desktop shell provides. Every method may return None (cancelled)."""

    def open_dialog(self) -> str | None:
        return None

    def save_dialog(self, default_name: str, kind: str) -> str | None:
        """kind: 'ofs' for a part, or an export format ('stl', '3mf', 'step', 'zip')."""
        return None

    def reveal(self, path: str) -> None:
        pass

    def document_changed(self, path: str | None) -> None:
        pass


def default_support_dir() -> pathlib.Path:
    import sys
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "OpenFusion"
    return home / ".openfusion"


def default_examples_dir() -> pathlib.Path | None:
    env = os.environ.get("OPENFUSION_EXAMPLES")
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env)
    here = pathlib.Path(__file__).resolve().parents[2] / "examples"   # a source checkout
    return here if here.is_dir() else None


def default_parts_dir() -> pathlib.Path:
    return pathlib.Path.home() / "Documents" / "OpenFusion"


class Workspace:
    def __init__(self, session=None, host: Host | None = None, support_dir=None, examples_dir=None, parts_dir=None):
        self.session = session
        self.host = host
        self.lock = threading.RLock()
        self.support_dir = pathlib.Path(support_dir) if support_dir else default_support_dir()
        self.examples_dir = pathlib.Path(examples_dir) if examples_dir else default_examples_dir()
        self.parts_dir = pathlib.Path(parts_dir) if parts_dir else default_parts_dir()
        self.generation = 0          # bumps when the document changes, so the page notices
        if session is not None:
            self.generation = 1

    @property
    def app(self) -> bool:
        return self.host is not None

    # ------------------------------------------------------------ recent files
    def _recent_file(self) -> pathlib.Path:
        return self.support_dir / "recent.json"

    def recent(self) -> list[dict]:
        try:
            paths = json.loads(self._recent_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            paths = []
        out = []
        for p in paths:
            if not isinstance(p, str):
                continue
            path = pathlib.Path(p)
            if not path.is_file():
                continue
            folder = str(path.parent)
            home = str(pathlib.Path.home())
            if folder == home or folder.startswith(home + os.sep):
                folder = "~" + folder[len(home):]
            out.append({"path": str(path), "name": path.stem, "folder": folder,
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))})
        return out[:12]

    def _remember(self, path: str):
        try:
            paths = [r["path"] for r in self.recent()]
        except Exception:
            paths = []
        paths = [path] + [p for p in paths if p != path]
        try:
            self.support_dir.mkdir(parents=True, exist_ok=True)
            self._recent_file().write_text(json.dumps(paths[:12], indent=1), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ examples
    def examples(self) -> list[dict]:
        if not self.examples_dir:
            return []
        out = []
        for f in sorted(self.examples_dir.glob("*.ofs")):
            out.append({"name": f.stem, "title": EXAMPLE_TITLES.get(f.stem, f.stem.replace("_", " "))})
        return out

    # ------------------------------------------------------------ opening documents
    def open(self, path: str):
        from .server import Session
        path = os.path.abspath(os.path.expanduser(path))
        if not path.lower().endswith(".ofs"):
            raise se.EditError("OpenFusion opens .ofs part files")
        if not os.path.isfile(path):
            raise se.EditError(f"no such file: {path}")
        with self.lock:
            if self.session is not None and self.session.path == path:
                return self.session
            s = Session(path)
            self.session = s
            self.generation += 1
        self._remember(path)
        if self.host:
            self.host.document_changed(path)
        return s

    def new(self, path: str):
        path = os.path.abspath(os.path.expanduser(path))
        if not path.lower().endswith(".ofs"):
            path += ".ofs"
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        if not os.path.exists(path):
            pathlib.Path(path).write_text(NEW_PART, encoding="utf-8")
        return self.open(path)

    def open_example(self, name: str):
        """Examples ship inside the app (read-only): open a copy in ~/Documents/OpenFusion."""
        if not self.examples_dir:
            raise se.EditError("no examples are installed")
        src = self.examples_dir / f"{name}.ofs"
        if not src.is_file() or src.parent != self.examples_dir:
            raise se.EditError(f"no example called {name}")
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        dst = self.parts_dir / f"{name}.ofs"
        k = 2
        while dst.exists() and dst.read_bytes() != src.read_bytes():
            dst = self.parts_dir / f"{name}-{k}.ofs"
            k += 1
        if not dst.exists():
            shutil.copyfile(src, dst)
        return self.open(str(dst))

    def close(self):
        with self.lock:
            self.session = None
            self.generation += 1
        if self.host:
            self.host.document_changed(None)

    # ------------------------------------------------------------ what the page asks for
    def version(self):
        s = self.session
        return f"{self.generation}:{s.version if s else 0}"

    def state(self) -> dict:
        s = self.session
        info = {"app": self.app, "generation": self.generation}
        if s is None:
            return {**info, "version": self.version(), "start": True, "recent": self.recent(), "examples": self.examples()}
        return {**s.state(), **info, "version": self.version()}

    def file_action(self, p: str, req: dict) -> dict | None:
        """/api/file/* routes. Dialog-driven ones need the desktop app."""
        act = p.rsplit("/", 1)[-1]
        if act == "recent":
            return {"recent": self.recent(), "examples": self.examples()}
        if act == "open":
            path = req.get("path")
            if not path:
                self._need_host()
                path = self.host.open_dialog()
                if not path:
                    return {"cancelled": True}
            self.open(path)
            return {"opened": self.session.path}
        if act == "new":
            path = req.get("path")
            if not path:
                self._need_host()
                self.parts_dir.mkdir(parents=True, exist_ok=True)
                path = self.host.save_dialog("Untitled part.ofs", "ofs")
                if not path:
                    return {"cancelled": True}
            self.new(path)
            return {"opened": self.session.path}
        if act == "example":
            self.open_example(str(req.get("name", "")))
            return {"opened": self.session.path}
        if act == "close":
            self.close()
            return {"closed": True}
        if act == "reveal":
            self._need_host()
            path = req.get("path") or (self.session.path if self.session else None)
            if path:
                self.host.reveal(path)
            return {"ok": True}
        if act == "export":           # save an export where the user chooses (desktop app)
            self._need_host()
            s = self._need_session()
            name, data, n = s.export_file(req.get("fmt", "3mf"), req.get("bodies") or None,
                                          bool(req.get("each")), req.get("quality", "standard"))
            path = self.host.save_dialog(name, name.rsplit(".", 1)[-1])
            if not path:
                return {"cancelled": True}
            pathlib.Path(path).write_bytes(data)
            return {"path": path, "name": os.path.basename(path), "size": len(data), "count": n}
        return None

    def _need_host(self):
        if not self.host:
            raise se.EditError("this needs the OpenFusion app (in the browser, start with: openfusion gui part.ofs)")

    def _need_session(self):
        if self.session is None:
            raise se.EditError("open a part first")
        return self.session
