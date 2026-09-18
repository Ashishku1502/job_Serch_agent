"""
AI Job Application Agent — Web Server & Dashboard
Serves the web dashboard at http://localhost:8000 and provides live API endpoints.
"""

import http.server
import socketserver
import json
import os
import subprocess
import sys

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            with open(os.path.join(DIRECTORY, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return
        elif self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ready", "agent": "Ashish Kumar Agent"}).encode("utf-8"))
            return
        elif self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            drafts_path = os.path.join(DIRECTORY, "output", "drafts.json")
            data = []
            if os.path.exists(drafts_path):
                try:
                    with open(drafts_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = []
            self.wfile.write(json.dumps({"drafts": data, "count": len(data)}).encode("utf-8"))
            return
        
        return super().do_GET()


    def do_POST(self):
        if self.path == "/api/run":
            python_exe = sys.executable
            main_script = os.path.join(DIRECTORY, "main.py")
            subprocess.Popen([python_exe, main_script], cwd=DIRECTORY)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"message": "Pipeline started successfully!"}).encode("utf-8"))
            return

        self.send_error(404, "Endpoint not found")


def start_server():
    print(f"AI Job Application Agent Dashboard running at: http://localhost:{PORT}")
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


if __name__ == "__main__":
    start_server()
