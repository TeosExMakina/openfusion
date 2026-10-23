"""The web server behind the app and the viewer: on this computer only (127.0.0.1)."""
from __future__ import annotations

import socketserver
from http.server import ThreadingHTTPServer


class LocalServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that skips the host name lookup http.server does when it starts
    (socket.getfqdn). On a Mac that lookup can go out on the local network, which made macOS
    ask for Local Network access although the app only ever talks to itself."""

    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]
