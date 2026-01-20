#!/usr/bin/env python3
"""
Alpha-OSK Dashboard Server

Serves the project dashboard at http://localhost:8080
"""

import http.server
import socketserver
import webbrowser
from functools import partial
from pathlib import Path

PORT = 8080
DIRECTORY = Path(__file__).parent.resolve() / "templates"


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler that serves dashboard.html as the root."""

    def do_GET(self):
        if self.path == "/" or self.path == "":
            self.path = "/dashboard.html"
        elif self.path == "/slides" or self.path == "/slides/":
            self.path = "/slides.html"
        return super().do_GET()

    def log_message(self, format, *args):
        print(f"[Dashboard] {args[0]}")


def main():
    print("=" * 50)
    print("  Alpha-OSK Project Dashboard")
    print("  AI-Powered On-Screen Keyboard for Windows")
    print("=" * 50)
    print()
    print("Available pages:")
    print("  - Dashboard: http://localhost:8080")
    print("  - Slides:    http://localhost:8080/slides")
    print()

    if not DIRECTORY.exists():
        print(f"Error: Templates directory not found: {DIRECTORY}")
        return

    dashboard_file = DIRECTORY / "dashboard.html"
    if not dashboard_file.exists():
        print(f"Error: Dashboard file not found: {dashboard_file}")
        return

    handler = partial(DashboardHandler, directory=str(DIRECTORY))
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"Serving dashboard at: {url}")
        print()
        print("Press Ctrl+C to stop the server")
        print()

        # Open browser automatically
        webbrowser.open(url)

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")


if __name__ == "__main__":
    main()
