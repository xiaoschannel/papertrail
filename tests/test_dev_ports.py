"""Ports per checkout: the main checkout's never change, each worktree claims a slot of its own."""

import json
import subprocess

import pytest

import dev_ports
from dev_ports import NotSetUp, Ports


def _main(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)       # the main checkout: its .git is a folder
    return main


def _worktree(main, name):
    worktree = main / ".claude" / "worktrees" / name
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: ../../../.git/worktrees/{name}\n", encoding="utf-8")
    return worktree


def _all_free(_port):
    return True


def test_the_main_checkout_keeps_its_ports_without_setup(tmp_path):
    assert dev_ports.checkout_ports(_main(tmp_path)) == Ports(0, 8001, 5174)


def test_a_worktree_that_has_not_claimed_refuses_rather_than_taking_the_main_ports(tmp_path):
    worktree = _worktree(_main(tmp_path), "wt")
    with pytest.raises(NotSetUp, match="worktree_setup.py"):
        dev_ports.checkout_ports(worktree)


def test_worktrees_claim_the_lowest_free_slots_in_turn(tmp_path):
    main, registry = _main(tmp_path), tmp_path / "ports.json"
    a, b = _worktree(main, "a"), _worktree(main, "b")
    assert dev_ports.claim_slot(a, registry=registry, port_free=_all_free) == Ports(1, 8002, 5175)
    assert dev_ports.claim_slot(b, registry=registry, port_free=_all_free) == Ports(2, 8003, 5176)


def test_claiming_again_keeps_the_same_slot_even_while_its_ports_are_busy(tmp_path):
    """Its ports are busy exactly when its own servers are up, which must not move it."""
    main, registry = _main(tmp_path), tmp_path / "ports.json"
    worktree = _worktree(main, "wt")
    first = dev_ports.claim_slot(worktree, registry=registry, port_free=_all_free)
    assert dev_ports.claim_slot(worktree, registry=registry, port_free=lambda _port: False) == first


def test_a_deleted_worktree_gives_its_slot_back(tmp_path):
    main, registry = _main(tmp_path), tmp_path / "ports.json"
    gone, kept = _worktree(main, "gone"), _worktree(main, "kept")
    dev_ports.claim_slot(gone, registry=registry, port_free=_all_free)
    dev_ports.claim_slot(kept, registry=registry, port_free=_all_free)
    (gone / ".git").unlink()
    gone.rmdir()
    new = _worktree(main, "new")
    assert dev_ports.claim_slot(new, registry=registry, port_free=_all_free).slot == 1
    assert sorted(json.loads(registry.read_text(encoding="utf-8"))["slots"].values()) == [1, 2]


def test_a_slot_another_program_listens_on_is_skipped(tmp_path):
    main, registry = _main(tmp_path), tmp_path / "ports.json"
    worktree = _worktree(main, "wt")
    assert dev_ports.claim_slot(worktree, registry=registry, port_free=lambda port: port != 5175).slot == 2


def test_the_claimed_slot_is_read_back_from_the_checkout(tmp_path):
    worktree = _worktree(_main(tmp_path), "wt")
    ports = dev_ports.claim_slot(worktree, registry=tmp_path / "ports.json", port_free=_all_free)
    dev_ports.save_checkout_ports(worktree, ports)
    assert dev_ports.checkout_ports(worktree) == ports


def test_a_worktree_launches_the_sandbox_only_under_names_of_its_own():
    """Copied in from the main checkout, the live entries and the plain names go; a stale slot's go too."""
    existing = {"version": "0.0.1", "configurations": [
        {"name": "live-api", "port": 8000},
        {"name": "sandbox-api", "port": 8001},
        {"name": "sandbox-web-3", "port": 5177},
        {"name": "my-own-tool", "port": 9999},
    ]}
    configurations = dev_ports.launch_configurations(Ports(1, 8002, 5175), "python", worktree=True)
    merged = dev_ports.merge_launch(existing, configurations)
    assert {c["name"]: c["port"] for c in merged["configurations"]} == {
        "sandbox-api-1": 8002, "sandbox-web-1": 5175, "my-own-tool": 9999,
    }


def test_the_main_checkout_launches_live_and_sandbox_on_the_ports_it_always_had():
    configurations = dev_ports.launch_configurations(Ports(0, 8001, 5174), ".venv/Scripts/python.exe",
                                                     worktree=False)
    assert {c["name"]: c["port"] for c in configurations} == {
        "live-api": 8000, "live-web": 5173, "sandbox-api": 8001, "sandbox-web": 5174,
    }


def test_a_slot_the_worktree_wrote_down_is_kept_when_the_registry_has_lost_it(tmp_path):
    """Its own servers keep its ports busy, which must not push it onto a new slot."""
    worktree = _worktree(_main(tmp_path), "wt")
    dev_ports.save_checkout_ports(worktree, Ports(3, 8004, 5177))
    ports = dev_ports.claim_slot(worktree, registry=tmp_path / "ports.json", port_free=lambda _port: False)
    assert ports == Ports(3, 8004, 5177)


def test_a_written_down_slot_another_worktree_holds_is_given_up(tmp_path):
    main, registry = _main(tmp_path), tmp_path / "ports.json"
    holder, copy = _worktree(main, "holder"), _worktree(main, "copy")
    dev_ports.claim_slot(holder, registry=registry, port_free=_all_free)
    dev_ports.save_checkout_ports(copy, Ports(1, 8002, 5175))     # e.g. copied over from the holder
    assert dev_ports.claim_slot(copy, registry=registry, port_free=_all_free).slot == 2


def test_the_registry_is_shared_by_every_worktree_of_the_clone(tmp_path, monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"),
                       ("GIT_AUTHOR_EMAIL", "test@example.com"), ("GIT_COMMITTER_EMAIL", "test@example.com")):
        monkeypatch.setenv(key, value)
    main = tmp_path / "main"
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-q", "--allow-empty", "-m", "start"], check=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(tmp_path / "wt")], check=True)
    expected = (main / ".git" / "papertrail-ports.json").resolve()
    assert dev_ports.registry_path(tmp_path / "wt").resolve() == expected
    assert dev_ports.registry_path(main).resolve() == expected
