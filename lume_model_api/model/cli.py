from __future__ import annotations

import argparse
import subprocess
import sys
from importlib.resources import files


def _run_marimo_app(script_name: str, default_port: int) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--token", action="store_true")
    parser.add_argument("--token-password", default=None)
    args, unknown = parser.parse_known_args()

    app_path = files("lume_visualizations").joinpath(script_name)
    command = [
        sys.executable,
        "-m",
        "marimo",
        "run",
        str(app_path),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.base_url:
        command.extend(["--base-url", args.base_url])
    if args.headless:
        command.append("--headless")
    if args.token:
        command.append("--token")
    else:
        command.append("--no-token")
    if args.token_password:
        command.extend(["--token-password", args.token_password])
    command.extend(unknown)
    return subprocess.call(command)


def quad_scan_main() -> int:
    return _run_marimo_app("quad_scan_monitor.py", default_port=2718)


def live_stream_main() -> int:
    return _run_marimo_app("live_stream_monitor.py", default_port=2719)


if __name__ == "__main__":
    raise SystemExit(quad_scan_main())
