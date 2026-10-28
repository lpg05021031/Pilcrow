"""``python -m pilcrow_qt``: the native Pilcrow window.

    python -m pilcrow_qt [--url http://127.0.0.1:47117/] [--dir ~/pilcrow] [--keep-server]
                         [--node /path/to/node] [--title Pilcrow]

Without ``--url`` the running Pilcrow server for this user is found through its lock file, or one is
started from ``--dir`` (``server/main.js --shell=none``) and stopped again when the window closes.
"""

from __future__ import annotations

import argparse
import sys

from .app import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pilcrow_qt", description="Pilcrow desktop window (Qt)")
    ap.add_argument("--url", default="", help="address of a running Pilcrow server (http://127.0.0.1:PORT/)")
    ap.add_argument("--dir", default="", help="Pilcrow install folder, to start the server from (default ~/pilcrow)")
    ap.add_argument("--node", default="", help="node executable (default: from PATH or the install's .launch-env)")
    ap.add_argument("--keep-server", action="store_true", help="leave the server running when the window closes")
    ap.add_argument("--title", default="Pilcrow")
    ap.add_argument(
        "--lite", action="store_true", help="no animations (set automatically when the system asks for reduced motion)"
    )
    ap.add_argument(
        "--native-titlebar", action="store_true", help="use the window manager's title bar instead of Pilcrow's"
    )
    args = ap.parse_args(argv)
    if args.native_titlebar:
        import os

        os.environ["PILCROW_TITLEBAR"] = "native"
    return run(
        url=args.url,
        install_dir=args.dir,
        node=args.node,
        keep_server=args.keep_server,
        title=args.title,
        lite=args.lite,
    )


if __name__ == "__main__":
    sys.exit(main())
