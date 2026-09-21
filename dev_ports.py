r"""Which ports this checkout's dev servers listen on, so that several checkouts can run at once.

The main checkout keeps the ports everything was written for: the live API on 8000 and its web app on
5173, the sandbox on 8001 and 5174. A git worktree is a second working copy of the same repo, and if
it started its sandbox on those same ports it would either fail or — worse — end up with one
checkout's web app proxying to another checkout's API. So each worktree claims a *slot*, and slot n
runs its sandbox on ``8001 + n`` and ``5174 + n``.

Slots are kept in one registry, ``papertrail-ports.json`` in the main checkout's ``.git`` folder: every
worktree of the clone shares that folder, and git never tracks what is in it. Not ``%APPDATA%``: the
Claude desktop app is a packaged (MSIX) app, and Windows quietly redirects what anything it starts
writes there, so a Claude session and an ordinary terminal would each see a registry of their own. A
worktree keeps its slot for as long as it exists, across restarts; a deleted one gives its slot back the
next time anyone claims.

A worktree runs the sandbox only. Two API processes on the real archive, running different code, is
how an archive gets hurt — so the live ports, and the live archive, stay the main checkout's.

``tools/worktree_setup.py`` claims the slot and writes it to ``.dev-ports.json`` at the checkout's root
(gitignored), where the sandbox server and ``frontend/vite.config.ts`` read it. A worktree without that
file refuses to start rather than falling back onto the main checkout's ports.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PORTS_FILE = ".dev-ports.json"

LIVE_API, LIVE_WEB = 8000, 5173
SANDBOX_API, SANDBOX_WEB = 8001, 5174
MAX_SLOT = 20            # 8021 / 5194 at the top: twenty worktrees running at once is plenty

SETUP_HINT = "run: python tools/worktree_setup.py (with the main checkout's .venv)"


class NotSetUp(RuntimeError):
    """A worktree that has not claimed a slot yet."""


@dataclass(frozen=True)
class Ports:
    slot: int
    sandbox_api: int
    sandbox_web: int


def ports_for_slot(slot: int) -> Ports:
    return Ports(slot, SANDBOX_API + slot, SANDBOX_WEB + slot)


def is_worktree(root: Path) -> bool:
    """A worktree's ``.git`` is a file pointing into the main checkout's; the main checkout's is a folder."""
    return (root / ".git").is_file()


def registry_path(root: Path = REPO_ROOT) -> Path:
    common = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            check=True, capture_output=True, text=True).stdout.strip()
    return Path(common) / "papertrail-ports.json"


def port_is_free(port: int) -> bool:
    """Whether nothing on this machine is listening on ``port`` — every dev server here binds 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _load_slots(registry: Path) -> dict[str, int]:
    try:
        raw = json.loads(registry.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {str(path): int(slot) for path, slot in raw.get("slots", {}).items()}


def claim_slot(
    root: Path,
    *,
    registry: Path | None = None,
    port_free: Callable[[int], bool] = port_is_free,
) -> Ports:
    """This worktree's slot: the one it already holds, else the lowest one nobody holds or listens on.

    A slot already held is kept without checking its ports — they are busy exactly when this worktree's
    own servers are running. So is one the worktree has written down but the registry has lost, as long as
    no other worktree holds it. A new slot must have both its ports free, so an unrelated program sitting
    on 8003 just moves the claim along to the next slot.
    """
    from data import atomic_write_text   # the app's own safe write; imported here so readers stay light

    registry = registry or registry_path(root)
    key = str(root.resolve())
    # A deleted worktree's slot is free again.
    slots = {path: slot for path, slot in _load_slots(registry).items() if Path(path).exists()}
    written = _written_slot(root)
    if key not in slots and written is not None and written not in slots.values():
        slots[key] = written
    if key not in slots:
        held = set(slots.values())
        free = (n for n in range(1, MAX_SLOT + 1)
                if n not in held and port_free(SANDBOX_API + n) and port_free(SANDBOX_WEB + n))
        slot = next(free, None)
        if slot is None:
            raise RuntimeError(f"Every slot from 1 to {MAX_SLOT} is taken; {registry} says by which worktrees.")
        slots[key] = slot
    registry.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(registry, json.dumps({"slots": slots}, indent=2) + "\n")
    return ports_for_slot(slots[key])


def _written_slot(root: Path) -> int | None:
    try:
        return int(json.loads((root / PORTS_FILE).read_text(encoding="utf-8"))["slot"])
    except (FileNotFoundError, KeyError, ValueError):
        return None


def save_checkout_ports(root: Path, ports: Ports) -> Path:
    """Write the claimed slot down where the sandbox server and the Vite config look for it."""
    path = root / PORTS_FILE
    path.write_text(json.dumps(asdict(ports), indent=2) + "\n", encoding="utf-8")
    return path


def checkout_ports(root: Path = REPO_ROOT) -> Ports:
    """The ports this checkout's dev servers use: the main checkout's own, or a worktree's claimed slot."""
    if not is_worktree(root):
        return ports_for_slot(0)
    try:
        raw = json.loads((root / PORTS_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotSetUp(f"{root.name} is a worktree with no ports of its own yet; {SETUP_HINT}") from None
    return Ports(int(raw["slot"]), int(raw["sandbox_api"]), int(raw["sandbox_web"]))


# --- .claude/launch.json -------------------------------------------------------------------------
# The dev servers Claude Code's preview can start. The file is gitignored (all of .claude/ is), so it is
# generated per checkout; these are the entries the setup owns, and any others in the file are kept.
# A worktree's carry its slot ("sandbox-web-1"): the preview may treat two servers of one name as the same
# server even from different checkouts, and starting one would then stop the other.
OWNED = re.compile(r"(live|sandbox)-(api|web)(-\d+)?")


def server_name(kind: str, ports: Ports) -> str:
    return f"{kind}-{ports.slot}" if ports.slot else kind


def launch_configurations(ports: Ports, python: str, *, worktree: bool) -> list[dict]:
    """The dev servers for this checkout: the main checkout's under their plain names, a worktree's by slot."""
    sandbox = [
        {"name": server_name("sandbox-api", ports), "runtimeExecutable": python,
         "runtimeArgs": ["tools/sandbox_server.py"], "port": ports.sandbox_api},
        {"name": server_name("sandbox-web", ports), "runtimeExecutable": "npm",
         "runtimeArgs": ["--prefix", "frontend", "run", "dev:sandbox"], "port": ports.sandbox_web},
    ]
    if worktree:
        return sandbox   # the live archive is the main checkout's alone
    live = [
        {"name": "live-api", "runtimeExecutable": python,
         "runtimeArgs": ["-m", "uvicorn", "api.main:app", "--port", str(LIVE_API), "--host", "127.0.0.1"],
         "port": LIVE_API},
        {"name": "live-web", "runtimeExecutable": "npm", "runtimeArgs": ["--prefix", "frontend", "run", "dev"],
         "port": LIVE_WEB},
    ]
    return live + sandbox


def merge_launch(existing: dict | None, configurations: list[dict]) -> dict:
    """``existing`` with the owned entries replaced by ``configurations``; entries it doesn't own stay."""
    kept = [c for c in (existing or {}).get("configurations", []) if not OWNED.fullmatch(c.get("name", ""))]
    return {"version": (existing or {}).get("version", "0.0.1"), "configurations": configurations + kept}
