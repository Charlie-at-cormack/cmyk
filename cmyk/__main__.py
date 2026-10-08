"""`python -m cmyk` - start the local service and open the browser."""
from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="Cormack Media Yield Kontrol (CMYK)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--lan", action="store_true",
                    help="Listen on all interfaces. Off by default: PDFs may be confidential.")
    args = ap.parse_args()

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    print(f"CMYK running at {url}  (Ctrl+C to stop)")
    uvicorn.run("cmyk.app:app", host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
