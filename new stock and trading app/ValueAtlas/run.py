"""Start ValueAtlas.

    python run.py              start the local web app and open it in your browser
    python run.py --background start without opening a browser (used at Windows sign-in)
    python run.py --refresh    run one data refresh in the console and exit
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from valueatlas import storage  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("VALUEATLAS_PORT", "8765"))
URL = f"http://{HOST}:{PORT}/"


def setup_logging(console):
    storage.DATA.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(storage.DATA / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    if console and sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)
    logging.getLogger("waitress").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def already_running():
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=2) as resp:
            return json.loads(resp.read()).get("app") == "ValueAtlas"
    except Exception:
        return False


def open_browser_when_ready():
    for _ in range(60):
        if already_running():
            webbrowser.open(URL)
            return
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="ValueAtlas local stock dashboard")
    parser.add_argument("--refresh", action="store_true", help="refresh data once and exit")
    parser.add_argument("--background", action="store_true", help="do not open a browser window")
    args = parser.parse_args()
    setup_logging(console=args.refresh or sys.stdout is not None)

    if args.refresh:
        from valueatlas.engine import Progress, refresh
        progress = Progress()
        last = [""]

        def printer():
            while progress.state.get("stage") not in ("Done", "Failed"):
                s = progress.snapshot()
                line = f"{s['stage']} {s['done']}/{s['total']} {s['log'][-1] if s['log'] else ''}"
                if line != last[0]:
                    print(line, flush=True)
                    last[0] = line
                time.sleep(2)
        threading.Thread(target=printer, daemon=True).start()
        try:
            result = refresh(progress)
        except Exception as exc:
            progress.update(stage="Failed")
            print(f"Refresh failed: {exc}")
            return 1
        print(f"Finished: {result['status']} – {result['requested'] - result['failed']} stocks loaded.")
        return 0 if result["status"] == "Complete" else 2

    if already_running():
        if not args.background:
            webbrowser.open(URL)
        return 0

    from waitress import serve
    from valueatlas.server import create_app, scheduler_loop
    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()
    if not args.background:
        threading.Thread(target=open_browser_when_ready, daemon=True).start()
    logging.getLogger("valueatlas").info("ValueAtlas running at %s", URL)
    if sys.stdout:
        print(f"ValueAtlas is running at {URL}  (close this window to stop it)", flush=True)
    serve(create_app(), host=HOST, port=PORT, threads=8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
