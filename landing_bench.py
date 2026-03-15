#!/usr/bin/env python3
"""Benchmark for landing page evolution. Runs Google Lighthouse and scores the page."""

import http.server
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

PORT = 8197  # unlikely to conflict

def serve_file(html_path, port):
    """Serve a single HTML file on localhost."""
    import functools

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.path.dirname(os.path.abspath(html_path)), **kwargs)

        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                with open(html_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass  # silence logs

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()
    return server


def run_lighthouse(port):
    """Run Lighthouse CLI and return scores dict."""
    url = f"http://127.0.0.1:{port}"
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

    cmd = [
        "lighthouse", url,
        "--output=json",
        "--quiet",
        "--no-enable-error-reporting",
        f"--chrome-flags=--headless --no-sandbox --disable-gpu",
        "--only-categories=performance,accessibility,best-practices,seo",
    ]

    if os.path.exists(chrome_path):
        cmd.append(f"--chrome-path={chrome_path}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            stderr = result.stderr[:500] if result.stderr else ""
            return None, f"Lighthouse failed: {stderr}"

        report = json.loads(result.stdout)
        categories = report.get("categories", {})

        scores = {}
        for key in ["performance", "accessibility", "best-practices", "seo"]:
            cat = categories.get(key, {})
            scores[key] = round((cat.get("score", 0) or 0) * 100)

        return scores, None

    except subprocess.TimeoutExpired:
        return None, "Lighthouse timed out"
    except json.JSONDecodeError:
        return None, "Could not parse Lighthouse output"
    except Exception as e:
        return None, str(e)


def main():
    html_path = sys.argv[1]

    # basic validation
    try:
        content = open(html_path).read()
    except Exception as e:
        print(f"LOAD ERROR: {e}")
        return

    if len(content.strip()) < 50:
        print("ERROR: file too small, not a real page")
        return

    if "<html" not in content.lower():
        print("ERROR: not valid HTML")
        return

    # start server in background
    server_thread = threading.Thread(target=serve_file, args=(html_path, PORT), daemon=True)
    server_thread.start()
    time.sleep(1)  # let server start

    # run lighthouse
    scores, error = run_lighthouse(PORT)

    if error:
        print(f"LIGHTHOUSE ERROR: {error}")
        print(f"AVERAGE: 0/100")
        return

    # print results
    for category, score in scores.items():
        print(f"{category.upper()}: {score}/100")

    avg = sum(scores.values()) / len(scores)
    print(f"AVERAGE: {avg:.0f}/100")


if __name__ == "__main__":
    main()
