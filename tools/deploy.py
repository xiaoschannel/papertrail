"""Deploy the live app: bring the main checkout up to date with main and restart it, safely.

    <main checkout>/.venv/Scripts/python tools/deploy.py            update the main checkout, restart live
    <main checkout>/.venv/Scripts/python tools/deploy.py restart    restart live as it is
    <main checkout>/.venv/Scripts/python tools/deploy.py stop        stop live until someone starts it again
    <main checkout>/.venv/Scripts/python tools/deploy.py status     what live runs, and how far behind it is
    <main checkout>/.venv/Scripts/python tools/deploy.py install    (re)register the scheduled task
    <main checkout>/.venv/Scripts/python tools/deploy.py autostart on|off   start live at logon, or not

Setting it up on a new machine, once the README's venv and npm install are done: `install`, then `autostart
on` if live should come back by itself after a reboot, then `deploy`. Windows only — it is a scheduled task.

It runs from any checkout and always acts on the main checkout. Live runs as the "papertrail-live"
scheduled task, outside every Claude session (tools/live_server.py says why); the first deploy registers it.

Everything that could make it refuse is checked before live is stopped, so a refused deploy changes nothing.
It refuses:
- while a job runs on live: restarting the API would kill it. It names the job; --even-with-job overrides;
- a main checkout with uncommitted changes, on a branch not merged into main, or with commits of its own
  that aren't on origin — each of those is someone's work.
"""
from __future__ import annotations

import argparse
import getpass
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent

TASK = "papertrail-live"
LIVE_API, LIVE_WEB = 8000, 5173
HEALTH_TIMEOUT = 120       # seconds: the web app regenerates its API types before it serves


class Refusal(Exception):
    """Why deploying now would hurt something; nothing has been changed."""


# --- the main checkout ----------------------------------------------------------------------------------
def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def main_checkout(start: Path = REPO) -> Path:
    """The checkout every worktree of this clone shares its .git with."""
    return Path(git(start, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent


def _is_ancestor(root: Path, commit: str, of: str) -> bool:
    return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", commit, of]).returncode == 0


def check_update(main: Path) -> str:
    """Fetch, and make sure bringing ``main`` up to date loses nobody's work. Returns the current branch."""
    # Tracked edits only: untracked files survive a switch and a fast-forward, and this tool's own .live/
    # would otherwise block every deploy until .gitignore covering it reaches main.
    if git(main, "status", "--porcelain", "--untracked-files=no"):
        raise Refusal(f"{main} has uncommitted changes to tracked files; that's someone's work, so it's "
                      "left alone.")
    git(main, "fetch", "--quiet", "origin")
    branch = git(main, "branch", "--show-current")
    if branch != "main" and not _is_ancestor(main, "HEAD", "origin/main"):
        raise Refusal(f"{main} is on {branch or 'a detached HEAD'}, which isn't merged into main; "
                      "that's someone's work, so it isn't switched away from.")
    has_main = subprocess.run(["git", "-C", str(main), "rev-parse", "--verify", "--quiet", "refs/heads/main"],
                              capture_output=True).returncode == 0
    if has_main and git(main, "rev-list", "origin/main..main"):
        raise Refusal(f"main in {main} has commits that aren't on origin; push or drop them first.")
    return branch


def apply_update(main: Path, branch: str) -> list[str]:
    """Put ``main`` on the latest main. Returns the files that changed."""
    before = git(main, "rev-parse", "HEAD")
    if branch != "main":
        git(main, "switch", "--quiet", "main")
    git(main, "merge", "--quiet", "--ff-only", "origin/main")
    return git(main, "diff", "--name-only", before, "HEAD").splitlines()


def followups(changed: list[str]) -> list[str]:
    """What else a set of changed files needs before live can run them."""
    needed = []
    if "requirements.txt" in changed:
        needed.append("pip")
    if {"frontend/package.json", "frontend/package-lock.json"} & set(changed):
        needed.append("npm")
    if "requirements-deepseek.txt" in changed:
        needed.append("gpu")       # never installed here: the GPU stack is pinned by hand (flash_attn.md)
    return needed


def run_followups(main: Path, needed: list[str]) -> None:
    if "pip" in needed:
        # Without -U: installs what's missing and leaves what's there — the hand-pinned GPU stack included.
        subprocess.run([str(main / ".venv" / "Scripts" / "python.exe"), "-m", "pip", "install", "--quiet",
                        "-r", "requirements.txt"], cwd=main, check=True)
    if "npm" in needed:
        # From the frontend folder, not --prefix: npm reads package.json from the working directory, so
        # `npm --prefix frontend install` looks for one in the repo root and fails, however well --prefix
        # works for `npm --prefix frontend run <script>`.
        subprocess.run([shutil.which("npm") or "npm", "install"], cwd=main / "frontend", check=True)
    if "gpu" in needed:
        print("requirements-deepseek.txt changed; the GPU stack is updated by hand (flash_attn.md), not here.")


# --- jobs on live ---------------------------------------------------------------------------------------
def blocking_jobs(jobs: list[dict]) -> list[dict]:
    """The jobs a restart would kill. /api/jobs also lists each kind's last finished job; only running ones count."""
    return [job for job in jobs if job.get("status") == "running"]


def live_jobs() -> list[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{LIVE_API}/api/jobs", timeout=5) as response:
            return json.load(response)
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return []      # the API is down, so nothing is running on it
        raise Refusal(f"can't tell whether a job is running on live ({error.reason}).") from None
    except TimeoutError:
        # Up but not answering: busy is exactly when a restart would hurt, so this is no "all clear".
        raise Refusal("live's API didn't answer within 5s, so there's no telling whether a job is running.") from None


# --- the processes --------------------------------------------------------------------------------------
def listeners(netstat: str, port: int) -> set[int]:
    """The PIDs listening on ``port``, from ``netstat -ano`` output."""
    pids = set()
    for line in netstat.splitlines():
        fields = line.split()
        if len(fields) == 5 and fields[0] == "TCP" and fields[3] == "LISTENING" \
                and fields[1].rsplit(":", 1)[-1] == str(port):
            pids.add(int(fields[4]))
    return pids


def _listening(port: int) -> set[int]:
    return listeners(subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout, port)


def installed_launcher(main: Path) -> Path:
    """Where the task runs the launcher from: beside live's logs, in the main checkout. Not %APPDATA%: the
    Claude desktop app is a packaged app, Windows redirects what its sessions write there, and the task —
    outside the package — would find nothing."""
    return main / ".live" / "live_server.py"


def task_xml(pythonw: Path, launcher: Path, main: Path, *, at_logon: bool = False) -> str:
    """The task: as the logged-on user, never stopped for running long or on battery, and started either on
    demand only or at logon as well — a reboot otherwise leaves live down until someone asks for it."""
    arguments = escape(f'"{launcher}" --checkout "{main}"')
    triggers = "<Triggers />" if not at_logon else (
        f"<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(getpass.getuser())}</UserId>"
        "<Delay>PT30S</Delay></LogonTrigger></Triggers>")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Papertrail's live app (tools/deploy.py).</Description></RegistrationInfo>
  {triggers}
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <AllowHardTerminate>true</AllowHardTerminate>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(pythonw))}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{escape(str(main))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _task_exists() -> bool:
    return subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True).returncode == 0


def starts_at_logon() -> bool:
    """Whether the registered task already has its logon trigger, so re-registering keeps what was chosen."""
    query = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/XML", "ONE"], capture_output=True, text=True)
    return "<LogonTrigger>" in query.stdout


def refresh_launcher(main: Path) -> None:
    """The launcher the task runs: the main checkout's copy, else this one's (before it has reached main)."""
    source = main / "tools" / "live_server.py"
    target = installed_launcher(main)
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(source if source.is_file() else Path(__file__).with_name("live_server.py"), target)


def install(main: Path, at_logon: bool | None = None) -> None:
    """Register the task. ``at_logon`` unset keeps whatever the registered task already does, so reinstalling
    (and a deploy that finds no task) never quietly changes when live starts."""
    pythonw = main / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        raise Refusal(f"No {pythonw}; create the main checkout's .venv first (see README).")
    if at_logon is None:
        at_logon = _task_exists() and starts_at_logon()
    refresh_launcher(main)
    with tempfile.TemporaryDirectory() as folder:
        definition = Path(folder) / "task.xml"
        definition.write_text(task_xml(pythonw, installed_launcher(main), main, at_logon=at_logon),
                              encoding="utf-16")
        subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", str(definition), "/F"],
                       check=True, capture_output=True)
    when = "at logon and on demand" if at_logon else "on demand only"
    print(f"Registered the scheduled task {TASK}: {when}, as you, outside every Claude session.")


def stop() -> None:
    subprocess.run(["schtasks", "/End", "/TN", TASK], capture_output=True)
    # Whatever still listens is live too: the task's leftovers, or a live app a session started by hand.
    for port in (LIVE_API, LIVE_WEB):
        for pid in _listening(port):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    deadline = time.monotonic() + 20
    while (_listening(LIVE_API) or _listening(LIVE_WEB)) and time.monotonic() < deadline:
        time.sleep(0.5)


def start(main: Path) -> None:
    refresh_launcher(main)
    subprocess.run(["schtasks", "/Run", "/TN", TASK], check=True, capture_output=True)


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status == 200 and "json" in response.headers.get("content-type", "")
    except (urllib.error.URLError, OSError):
        return False


def wait_healthy(main: Path) -> bool:
    """Through the web app's proxy too: a Vite without its /api proxy answers with index.html, not JSON."""
    deadline = time.monotonic() + HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        if healthy(f"http://127.0.0.1:{LIVE_API}/api/health") and healthy(f"http://127.0.0.1:{LIVE_WEB}/api/health"):
            print(f"Live is up: http://127.0.0.1:{LIVE_WEB}")
            return True
        time.sleep(1)
    print(f"Live did not come up within {HEALTH_TIMEOUT}s. The end of its logs:")
    for name in ("api", "web"):
        log = main / ".live" / f"{name}.log"
        if log.is_file():
            print(f"--- {log}")
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]))
    return False


# --- commands -------------------------------------------------------------------------------------------
def deploy(main: Path, *, update: bool, even_with_job: bool) -> int:
    """0 deployed and healthy; 1 restarted but not healthy; 2 a job is running; 3 refused. Only 0 and 1 touched live."""
    try:
        running = blocking_jobs(live_jobs())
        if running and not even_with_job:
            print("Not restarting live: restarting the API would kill what's running on it:")
            for job in running:
                print(f"  {job.get('title', job.get('kind'))}: {job.get('done', 0)}/{job.get('total', 0)}")
            print("Wait for it to finish, or pass --even-with-job.")
            return 2
        branch = check_update(main) if update else None
        if not _task_exists():
            install(main)            # before stopping anything: a task that can't be made mustn't leave live down
    except Refusal as refusal:
        print(f"Not deploying: {refusal}")
        return 3
    stop()
    updated = True
    try:
        if branch is not None:
            changed = apply_update(main, branch)
            print(f"{main} is on main at {git(main, 'log', '-1', '--format=%h %s')}")
            try:
                run_followups(main, followups(changed))
            except subprocess.CalledProcessError as failure:
                # Said apart from a failed update: the new code is checked out, only its packages are not.
                updated = False
                print(f"The checkout is up to date, but installing what it needs failed: "
                      f"{(failure.stderr or '').strip() or failure}")
    except subprocess.CalledProcessError as failure:
        # e.g. an untracked file in the way of one the merge brings in: live still goes back up, as it was.
        updated = False
        print(f"Could not update the checkout: {(failure.stderr or '').strip() or failure}")
    finally:
        start(main)                  # whatever happened to the update, live comes back up
    healthy_now = wait_healthy(main)
    return 0 if healthy_now and updated else 1


def status(main: Path) -> int:
    state = "not registered"
    if _task_exists():
        query = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/FO", "CSV", "/NH"], capture_output=True, text=True)
        state = query.stdout.strip().split('","')[-1].strip('"') or "registered"
        state += ", starts at logon" if starts_at_logon() else ", starts on demand only"
    print(f"Task {TASK}: {state}")
    for name, port in (("API", LIVE_API), ("web", LIVE_WEB)):
        print(f"  {name} :{port} {'up' if healthy(f'http://127.0.0.1:{port}/api/health') else 'down'}")
    try:
        running = ", ".join(job.get("title", "?") for job in blocking_jobs(live_jobs())) or "none"
    except Refusal as refusal:
        running = f"unknown: {refusal}"
    print(f"Running jobs: {running}")
    git(main, "fetch", "--quiet", "origin")
    branch = git(main, "branch", "--show-current") or "a detached HEAD"
    behind = git(main, "rev-list", "--count", "HEAD..origin/main")
    print(f"Main checkout: {branch} at {git(main, 'log', '-1', '--format=%h')}, {behind} commit(s) behind origin/main")
    return 0


def halt(main: Path, *, even_with_job: bool) -> int:
    """Stop live and leave it stopped — the one way to take it down, since killing the servers by hand
    leaves the task thinking it still runs."""
    try:
        running = blocking_jobs(live_jobs())
    except Refusal as refusal:
        print(f"Not stopping live: {refusal}")
        return 3
    if running and not even_with_job:
        print("Not stopping live: a job is running on it:")
        for job in running:
            print(f"  {job.get('title', job.get('kind'))}: {job.get('done', 0)}/{job.get('total', 0)}")
        print("Wait for it to finish, or pass --even-with-job.")
        return 2
    stop()
    print("Live is stopped. Start it again with: python tools/deploy.py restart")
    return 0


def live_is_up() -> bool:
    return healthy(f"http://127.0.0.1:{LIVE_API}/api/health") and healthy(f"http://127.0.0.1:{LIVE_WEB}/api/health")


def autostart(main: Path, on: bool) -> int:
    """Whether Windows starts live when you log in. Live keeps running either way."""
    was_up = live_is_up()
    try:
        install(main, at_logon=on)
    except Refusal as refusal:
        print(refusal)
        return 1
    if was_up and not live_is_up():
        # Registering replaces the task, which ends the instance that was serving live: put it back.
        print("Restarting live, which the re-registered task had stopped.")
        stop()
        start(main)
        return 0 if wait_healthy(main) else 1
    if not was_up:
        print("Live isn't running now; start it with: python tools/deploy.py restart")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", default="deploy",
                        choices=["deploy", "restart", "stop", "status", "install", "autostart"])
    parser.add_argument("state", nargs="?", choices=["on", "off"], help="autostart: on starts live at logon")
    parser.add_argument("--even-with-job", action="store_true",
                        help="restart or stop even though a job is running on live")
    args = parser.parse_args()
    checkout = main_checkout()
    if args.command == "status":
        return status(checkout)
    if args.command == "autostart":
        if args.state is None:
            parser.error("autostart needs on or off")
        return autostart(checkout, args.state == "on")
    if args.command == "stop":
        return halt(checkout, even_with_job=args.even_with_job)
    if args.command == "install":
        try:
            install(checkout)
        except Refusal as refusal:
            print(refusal)
            return 1
        return 0
    return deploy(checkout, update=args.command == "deploy", even_with_job=args.even_with_job)


if __name__ == "__main__":
    sys.exit(main())
