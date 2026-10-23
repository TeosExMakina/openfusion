"""OpenFusion as a desktop app: the modeling GUI in its own native window.

    python -m openfusion.app [part.ofs]

The engine runs the same local server as `openfusion gui`; a pywebview window
(WKWebView on macOS) shows it. Without a part it opens on the start screen.
The app adds what a browser tab can't do: native Open/Save dialogs, a File
menu, exports saved where you choose, recent files, and opening .ofs files
from Finder.
"""
from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import threading

from .gui.server import make_handler, watch_forever
from .gui.workspace import Host, Workspace, default_parts_dir, default_support_dir

PORTS = range(47820, 47840)      # a stable port keeps the page's saved preferences between launches


def _free_port() -> int:
    for p in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebviewHost(Host):
    """Native dialogs and window title through pywebview. Safe to call from server threads."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.window = None
        self.last_dir = None

    def _start_dir(self) -> str:
        if self.last_dir and os.path.isdir(self.last_dir):
            return self.last_dir
        s = self.ws.session
        if s is not None:
            return os.path.dirname(s.path)
        d = default_parts_dir()
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def open_dialog(self):
        import webview
        r = self.window.create_file_dialog(webview.FileDialog.OPEN, directory=self._start_dir(),
                                           file_types=("OpenFusion part (*.ofs)", "All files (*.*)"))
        path = r[0] if isinstance(r, (list, tuple)) and r else (r or None)
        if path:
            self.last_dir = os.path.dirname(str(path))
        return str(path) if path else None

    def save_dialog(self, default_name, kind):
        import webview
        r = self.window.create_file_dialog(webview.FileDialog.SAVE, directory=self._start_dir(), save_filename=default_name)
        path = r[0] if isinstance(r, (list, tuple)) and r else (r or None)
        if not path:
            return None
        path = str(path)
        if kind and not path.lower().endswith("." + kind):
            path += "." + kind
        self.last_dir = os.path.dirname(path)
        return path

    def reveal(self, path):
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path], check=False)
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)], check=False)

    def document_changed(self, path):
        if self.window is None:
            return
        try:
            self.window.set_title(f"{pathlib.Path(path).name} — OpenFusion" if path else "OpenFusion")
        except Exception:
            pass


def _menus(ws: Workspace, host: WebviewHost):
    from webview.menu import Menu, MenuAction, MenuSeparator

    def guard(fn):
        def run():
            try:
                fn()
            except Exception as e:      # shown in the page as a toast
                _js(host, f"window.ofToast && window.ofToast({str(e)!r}, true)")
        run.__name__ = fn.__name__
        return run

    def new_part():
        ws.file_action("/api/file/new", {})

    def open_part():
        ws.file_action("/api/file/open", {})

    def documents():
        _js(host, "window.ofApp && window.ofApp.openDocs()")

    def save_copy():
        ws.file_action("/api/file/save_as", {"choose": True, "open": True})

    def export():
        _js(host, "window.ofApp && window.ofApp.openExport()")

    def reveal():
        ws.file_action("/api/file/reveal", {})

    def close_part():
        ws.close()

    def shortcuts():
        _js(host, "window.ofApp && window.ofApp.toggleHelp(true)")

    def script():
        _js(host, "window.ofApp && window.ofApp.toggleEditor()")

    examples = []
    for ex in ws.examples():
        def open_ex(name=ex["name"]):
            ws.open_example(name)
        open_ex.__name__ = f"example_{ex['name']}"
        examples.append(MenuAction(ex["name"].replace("_", " ").title(), guard(open_ex)))

    file_items = [MenuAction("New Part…  (⌘N)", guard(new_part)), MenuAction("Open…  (⌘O)", guard(open_part)),
                  MenuAction("Documents…  (⌘⇧O)", guard(documents)), MenuAction("Save a Copy As…  (⌘⇧S)", guard(save_copy))]
    if examples:
        file_items.append(Menu("Open Example", examples))
    file_items += [MenuSeparator(), MenuAction("Export…  (⌃⇧E)", guard(export)),
                   MenuAction("Show in Finder", guard(reveal)), MenuSeparator(), MenuAction("Close Part", guard(close_part))]
    return [Menu("File", file_items),
            Menu("View", [MenuAction("Script Pane  (⌘E)", guard(script))]),
            Menu("Help", [MenuAction("Keyboard Shortcuts  (?)", guard(shortcuts))])]


def _js(host: WebviewHost, code: str):
    try:
        if host.window is not None:
            host.window.evaluate_js(code)
    except Exception:
        pass


def _accept_finder_opens(ws: Workspace):
    """Double-clicking a .ofs file (or dropping it on the Dock icon) sends the app an
    'open documents' event; pywebview's app delegate doesn't handle it, so teach it."""
    if sys.platform != "darwin":
        return
    try:
        import objc
        from webview.platforms import cocoa
    except Exception:
        return

    def application_openFiles_(self, app, filenames):  # pragma: no cover  (called by Cocoa)
        for f in list(filenames)[-1:]:          # one document per window: the last one wins
            try:
                ws.open(str(f))
            except Exception as e:
                print("could not open", f, e, flush=True)
        try:
            app.replyToOpenOrPrint_(0)          # NSApplicationDelegateReplySuccess
        except Exception:
            pass

    try:
        sel = objc.selector(application_openFiles_, selector=b"application:openFiles:", signature=b"v@:@@")
        objc.classAddMethods(cocoa.BrowserView.AppDelegate, [sel])
    except Exception as e:
        print("Finder open events unavailable:", e, flush=True)


def main(argv=None, exit_process=True):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        import webview
    except ImportError:
        raise SystemExit("The desktop app needs pywebview: pip install 'openfusion[app]'")
    from .localserver import LocalServer

    support = pathlib.Path(os.environ.get("OPENFUSION_SUPPORT", "")) if os.environ.get("OPENFUSION_SUPPORT") else default_support_dir()
    support.mkdir(parents=True, exist_ok=True)
    ws = Workspace(support_dir=support)
    host = WebviewHost(ws)
    ws.host = host
    for a in argv:                               # `open -a OpenFusion part.ofs` or a dev launch
        if a.lower().endswith(".ofs") and os.path.isfile(a):
            try:
                ws.open(a)
            except Exception as e:
                print("could not open", a, e, flush=True)

    port = _free_port()
    srv = LocalServer(("127.0.0.1", port), make_handler(ws))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    watch_forever(ws)

    title = f"{pathlib.Path(ws.session.path).name} — OpenFusion" if ws.session else "OpenFusion"
    host.window = webview.create_window(title, f"http://127.0.0.1:{port}/", width=1440, height=900, min_size=(960, 640),
                                        background_color="#EEF1F4", text_select=True)
    _accept_finder_opens(ws)
    storage = support / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(menu=_menus(ws, host), private_mode=False, storage_path=str(storage))
    srv.shutdown()
    if exit_process:
        os._exit(0)                              # don't wait on idle server threads
    return ws


if __name__ == "__main__":
    main()
