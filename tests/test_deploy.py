"""Deploying live: what blocks a restart, and never losing work in the main checkout on the way to main."""

import pathlib
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools import deploy, live_server
from tools.deploy import Refusal

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _commit(repo, name, text="x"):
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}")


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """An origin with main, and a main checkout cloned from it."""
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "Test")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "test@example.com")
    origin, seed, main = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "main"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    _commit(seed, "app.py")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(main)], check=True)
    return seed, main


def _land_on_main(seed, name):
    """Someone merges a change into main on origin."""
    _commit(seed, name)
    _git(seed, "push", "-q", "origin", "main")


def test_main_behind_origin_is_fast_forwarded(checkout):
    seed, main = checkout
    _land_on_main(seed, "requirements.txt")
    branch = deploy.check_update(main)
    assert deploy.apply_update(main, branch) == ["requirements.txt"]
    assert (main / "requirements.txt").is_file()


def test_a_merged_branch_is_left_for_the_latest_main(checkout):
    seed, main = checkout
    _git(main, "switch", "-q", "-c", "feat/done")
    _commit(main, "feature.py")
    _git(main, "push", "-q", "origin", "feat/done")
    _git(seed, "pull", "-q", "origin", "feat/done")      # the PR merges
    _land_on_main(seed, "later.py")
    branch = deploy.check_update(main)
    assert set(deploy.apply_update(main, branch)) == {"later.py"}
    assert _git(main, "branch", "--show-current").strip() == "main"


def test_a_branch_not_merged_into_main_is_not_switched_away_from(checkout):
    _, main = checkout
    _git(main, "switch", "-q", "-c", "feat/wip")
    _commit(main, "wip.py")
    with pytest.raises(Refusal, match="isn't merged"):
        deploy.check_update(main)


def test_commits_on_main_that_origin_lacks_are_not_run_as_live(checkout):
    _, main = checkout
    _commit(main, "local.py")
    with pytest.raises(Refusal, match="aren't on origin"):
        deploy.check_update(main)


def test_a_worktree_finds_the_main_checkout(checkout, tmp_path):
    _, main = checkout
    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(worktree))
    assert deploy.main_checkout(worktree).resolve() == main.resolve()


def test_only_running_jobs_block_a_restart():
    """/api/jobs lists each kind's last finished job too; those must not hold a deploy up."""
    jobs = [{"kind": "ocr", "status": "succeeded"}, {"kind": "parse", "status": "running"},
            {"kind": "archive", "status": "failed"}]
    assert deploy.blocking_jobs(jobs) == [{"kind": "parse", "status": "running"}]


def test_changed_files_say_what_else_to_install():
    assert deploy.followups(["api/main.py"]) == []
    assert deploy.followups(["requirements.txt", "frontend/package-lock.json"]) == ["pip", "npm"]
    assert deploy.followups(["requirements-deepseek.txt"]) == ["gpu"]


def test_listeners_are_read_off_netstat():
    netstat = """
  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       32284
  TCP    127.0.0.1:18000        0.0.0.0:0              LISTENING       111
  TCP    127.0.0.1:52000        127.0.0.1:8000         ESTABLISHED     222
  TCP    [::1]:5173             [::]:0                 LISTENING       333
  UDP    0.0.0.0:8000           *:*                                    444
"""
    assert deploy.listeners(netstat, 8000) == {32284}
    assert deploy.listeners(netstat, 5173) == {333}


def test_the_task_runs_on_demand_forever_and_escapes_its_paths():
    root = ET.fromstring(deploy.task_xml(Path(r"C:\Tom & Jerry\.venv\Scripts\pythonw.exe"),
                                         Path(r"C:\Users\x\AppData\Roaming\papertrail\live_server.py"),
                                         Path(r"C:\Tom & Jerry")).split("\n", 1)[1])
    assert root.find("t:Triggers", NS) is not None and len(root.find("t:Triggers", NS)) == 0
    settings = root.find("t:Settings", NS)
    assert settings.find("t:ExecutionTimeLimit", NS).text == "PT0S"
    assert settings.find("t:StopIfGoingOnBatteries", NS).text == "false"
    assert settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    exec_ = root.find("t:Actions/t:Exec", NS)
    assert exec_.find("t:Command", NS).text == r"C:\Tom & Jerry\.venv\Scripts\pythonw.exe"
    assert exec_.find("t:Arguments", NS).text.endswith(r'--checkout "C:\Tom & Jerry"')


def test_untracked_files_do_not_block_a_deploy(checkout):
    """A switch and a fast-forward both keep untracked files — deploy's own .live/ among them."""
    seed, main = checkout
    _land_on_main(seed, "later.py")
    (main / ".live").mkdir()
    (main / ".live" / "api.log").write_text("noise", encoding="utf-8")
    branch = deploy.check_update(main)
    assert deploy.apply_update(main, branch) == ["later.py"]
    assert (main / ".live" / "api.log").read_text(encoding="utf-8") == "noise"


def test_edits_to_tracked_files_still_block_a_deploy(checkout):
    _, main = checkout
    (main / "app.py").write_text("edited", encoding="utf-8")
    with pytest.raises(Refusal, match="tracked"):
        deploy.check_update(main)


def test_the_task_can_be_asked_to_start_live_at_logon():
    root = ET.fromstring(deploy.task_xml(Path("pythonw.exe"), Path("live_server.py"), Path(r"C:\main"),
                                         at_logon=True).split("\n", 1)[1])
    trigger = root.find("t:Triggers/t:LogonTrigger", NS)
    assert trigger is not None and trigger.find("t:Enabled", NS).text == "true"
    assert trigger.find("t:Delay", NS).text == "PT30S"      # out of the way of logging in


def test_npm_installs_from_the_frontend_folder(tmp_path, monkeypatch):
    """`npm --prefix frontend install` reads package.json from the working directory and fails there."""
    calls = []
    monkeypatch.setattr(deploy.subprocess, "run", lambda command, **kw: calls.append((command, kw)) or None)
    deploy.run_followups(tmp_path, ["npm"])
    command, keywords = calls[0]
    assert command[-1] == "install" and "--prefix" not in command
    assert keywords["cwd"] == tmp_path / "frontend"


# --- the paths a passing deploy never takes ------------------------------------------------------------
def test_a_merge_blocked_by_an_untracked_file_leaves_live_running(checkout, monkeypatch, capsys):
    """git refuses to overwrite an untracked file; live must come back anyway, on the code it already had."""
    seed, main = checkout
    _land_on_main(seed, "later.py")
    (main / "later.py").write_text("mine, untracked", encoding="utf-8")
    started = []
    monkeypatch.setattr(deploy, "stop", lambda: None)
    monkeypatch.setattr(deploy, "start", lambda _main: started.append(True))
    monkeypatch.setattr(deploy, "wait_healthy", lambda _main: True)
    monkeypatch.setattr(deploy, "live_jobs", list)
    monkeypatch.setattr(deploy, "_task_exists", lambda: True)
    assert deploy.deploy(main, update=True, even_with_job=False) == 1
    assert started == [True]                                   # live came back up
    assert "Could not update the checkout" in capsys.readouterr().out
    assert (main / "later.py").read_text(encoding="utf-8") == "mine, untracked"


def test_a_failed_install_is_not_reported_as_a_failed_update(checkout, monkeypatch, capsys):
    seed, main = checkout
    _land_on_main(seed, "requirements.txt")
    monkeypatch.setattr(deploy, "stop", lambda: None)
    monkeypatch.setattr(deploy, "start", lambda _main: None)
    monkeypatch.setattr(deploy, "wait_healthy", lambda _main: True)
    monkeypatch.setattr(deploy, "live_jobs", list)
    monkeypatch.setattr(deploy, "_task_exists", lambda: True)
    monkeypatch.setattr(deploy, "run_followups", lambda *_: (_ for _ in ()).throw(
        subprocess.CalledProcessError(1, "pip", stderr="no network")))
    assert deploy.deploy(main, update=True, even_with_job=False) == 1
    out = capsys.readouterr().out
    assert "is on main at" in out and "installing what it needs failed" in out
    assert "Could not update the checkout" not in out


def test_installing_without_a_venv_refuses(tmp_path):
    with pytest.raises(Refusal, match="venv"):
        deploy.install(tmp_path)


def test_an_app_that_never_answers_reports_the_end_of_its_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(deploy, "HEALTH_TIMEOUT", 1)
    # Ports nothing serves: otherwise this passes or fails depending on whether live happens to be running.
    monkeypatch.setattr(deploy, "LIVE_API", 65000)
    monkeypatch.setattr(deploy, "LIVE_WEB", 65001)
    (tmp_path / ".live").mkdir()
    (tmp_path / ".live" / "api.log").write_text("Traceback: boom", encoding="utf-8")
    assert deploy.wait_healthy(tmp_path) is False
    out = capsys.readouterr().out
    assert "did not come up" in out and "Traceback: boom" in out


@pytest.mark.skipif(sys.platform != "win32", reason="the launcher is what a Windows scheduled task runs: "
                    "it uses Windows-only process flags and job objects, so it is tested on the Windows CI job")
def test_one_server_stopping_stops_the_other(tmp_path):
    """Otherwise the task looks healthy while half the app is down."""
    node = shutil.which("node")
    assert node, "node is needed for this test"
    logs = tmp_path / ".live"
    logs.mkdir()
    marker = (tmp_path / "still-alive.txt").as_posix()
    # If it outlives run(), it says so in a file a second later; being killed is the only way it stays quiet.
    stays = f"setTimeout(() => require('fs').writeFileSync('{marker}', 'alive'), 1000); setTimeout(() => {{}}, 600000)"
    live_server.run({"quits": [node, "-e", "process.exit(3)"], "stays": [node, "-e", stays]}, tmp_path, logs)
    assert "started" in (logs / "stays.log").read_text(encoding="utf-8")    # it really ran
    # The logs say which one went first, so a server that dies without a word can still be told apart.
    assert "--- exited with code 3 at" in (logs / "quits.log").read_text(encoding="utf-8")
    assert "--- stopped because quits exited with code 3 at" in (logs / "stays.log").read_text(encoding="utf-8")
    time.sleep(2.5)
    assert not pathlib.Path(marker).exists(), "the surviving server was left running"


def test_a_crash_code_is_logged_as_the_status_windows_names_it(tmp_path):
    """A native crash exits with an NTSTATUS; 0xC0000005 is searchable, 3221225477 is not."""
    live_server._log_ending(["api", "web"], tmp_path, "api", 0xC0000005, "2026-01-01 00:00:00")
    api_log = (tmp_path / "api.log").read_text(encoding="utf-8")
    assert "exited with code 3221225477 (0xC0000005) at 2026-01-01 00:00:00" in api_log
    assert "because api exited with code 3221225477 (0xC0000005)" in (tmp_path / "web.log").read_text(encoding="utf-8")
