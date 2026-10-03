"""python -m it45 [--port 8045] [--no-browser]"""
import argparse
import sys
import threading
import webbrowser

import uvicorn


def main():
    for s in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="it45")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8045)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    from .api import create_app
    if not a.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(f"http://{a.host}:{a.port}")).start()
    print(f"IT45 dashboard -> http://{a.host}:{a.port}   (Ctrl+C to stop)")
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
