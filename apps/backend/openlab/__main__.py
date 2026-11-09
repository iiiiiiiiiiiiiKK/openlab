"""Uvicorn entry point for the OPENLAB backend (local web + desktop mode)."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

import uvicorn

from openlab.app import create_app
from openlab.config import Settings, get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openlab-backend", description="OPENLAB local backend")
    parser.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="port (default 3210)")
    parser.add_argument("--reload", action="store_true", help="reload on file changes (dev)")
    parser.add_argument("--lan", action="store_true", help="listen on 0.0.0.0 (explicit opt-in)")
    parser.add_argument("--open", action="store_true", dest="open_browser", help="open the workbench in a browser")
    parser.add_argument("--data-dir", default=None, help="override OPENLAB_DATA directory")
    parser.add_argument("--log-level", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_dir:
        os.environ["OPENLAB_DATA_DIR"] = args.data_dir
    settings = get_settings()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if args.lan:
        settings.allow_lan = True
    if args.log_level:
        settings.log_level = args.log_level

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    app = create_app(settings)
    url = f"http://{'127.0.0.1' if settings.bind_host == '0.0.0.0' else settings.bind_host}:{settings.port}"
    print(f"OPENLAB backend starting on {url}")
    if args.open_browser:
        import threading

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
