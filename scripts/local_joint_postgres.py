from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = REPOSITORY_ROOT / ".local-postgres"
PG_CTL = LOCAL_ROOT / "runtime" / "bin" / "pg_ctl.exe"
DATA_DIRECTORY = LOCAL_ROOT / "data"
LOG_PATH = LOCAL_ROOT / "postgres.log"
LISTEN_OPTIONS = "-h 127.0.0.1 -p 55432"


def _verify_assets() -> None:
    for path in (PG_CTL, DATA_DIRECTORY):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(LOCAL_ROOT.resolve(strict=True)):
            raise SystemExit("local PostgreSQL asset resolved outside .local-postgres")


def is_running() -> bool:
    _verify_assets()
    completed = subprocess.run(  # noqa: S603
        [str(PG_CTL), "-D", str(DATA_DIRECTORY), "status"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def start() -> None:
    _verify_assets()
    if is_running():
        raise SystemExit("local PostgreSQL is already running")
    subprocess.run(  # noqa: S603
        [
            str(PG_CTL),
            "-D",
            str(DATA_DIRECTORY),
            "-l",
            str(LOG_PATH),
            "-o",
            LISTEN_OPTIONS,
            "-w",
            "start",
        ],
        check=True,
    )


def stop() -> None:
    _verify_assets()
    if not is_running():
        print("LOCAL_POSTGRES_STATUS=already-stopped")  # noqa: T201
        return
    subprocess.run(  # noqa: S603
        [
            str(PG_CTL),
            "-D",
            str(DATA_DIRECTORY),
            "stop",
            "-m",
            "fast",
            "-w",
        ],
        check=True,
    )


def serve() -> None:
    start()
    print("LOCAL_POSTGRES_STATUS=running host=127.0.0.1 port=55432")  # noqa: T201
    try:
        while is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("serve", "start", "stop", "status"))
    action = parser.parse_args().action
    if action == "serve":
        serve()
    elif action == "start":
        start()
    elif action == "stop":
        stop()
    else:
        print(  # noqa: T201
            f"LOCAL_POSTGRES_STATUS={'running' if is_running() else 'stopped'}"
        )


if __name__ == "__main__":
    main()
