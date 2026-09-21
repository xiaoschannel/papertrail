"""The live app, as the "papertrail-live" scheduled task runs it: the main checkout's API and web app.

Anything a Claude session starts lives in that session's job object and dies with it, so a live app a
session started goes down whenever that session restarts or is archived — usually right after the merge
it was deploying. The task runs outside every session, so live stays up while sessions come and go.

tools/deploy.py copies this file to <main checkout>/.live/ and registers the task to run that copy, so
the task doesn't depend on this file having reached main yet; don't run it by hand. It must stay
self-contained for the same reason: no imports from the repo.

Each server's output goes to <checkout>/.live/<name>.log. If either server exits, the other is stopped
and so is this script, so the task never shows as running while half the app is down.
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW: the task runs in the user's session; no consoles popping up
LIVE_API, LIVE_WEB = 8000, 5173


def run(servers: dict[str, list[str]], root: Path, logs: Path) -> None:
    """Start every server, wait until one of them stops, then stop the rest.

    The commands are an argument so a test can hand it two harmless ones and check the survivor really is
    stopped — what keeps the task from looking healthy while half the app is down.
    """
    running: dict[str, subprocess.Popen] = {}
    for name, command in servers.items():
        log = open(logs / f"{name}.log", "ab")
        log.write(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
        log.flush()
        running[name] = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                                         stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    try:
        while all(process.poll() is None for process in running.values()):
            time.sleep(0.2)
    finally:
        for process in running.values():
            if process.poll() is None:
                # The whole tree: npm starts Vite through cmd, and stopping npm alone leaves Vite serving.
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True, type=Path, help="the main checkout to serve")
    root = parser.parse_args().checkout
    logs = root / ".live"
    logs.mkdir(exist_ok=True)

    npm = shutil.which("npm")
    if npm is None:
        (logs / "web.log").write_bytes(b"npm is not on the PATH the task sees; install Node.js for all users\n")
        return 1
    run({"api": [str(root / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "api.main:app",
                 "--host", "127.0.0.1", "--port", str(LIVE_API)],
         "web": [npm, "--prefix", "frontend", "run", "dev"]}, root, logs)
    return 1


if __name__ == "__main__":
    sys.exit(main())
