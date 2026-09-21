"""Set this checkout up to run beside the others: its own ports, and the dev servers to start them.

    <main checkout>/.venv/Scripts/python tools/worktree_setup.py

Run it once in each new worktree; running it again is harmless and keeps the same ports. In the main
checkout it only (re)writes .claude/launch.json — the main checkout's ports never change. The scheme,
and why worktrees run the sandbox only, is in dev_ports.py.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dev_ports import (  # noqa: E402  (needs the repo on the path first)
    LIVE_API, LIVE_WEB, checkout_ports, claim_slot, is_worktree, launch_configurations, merge_launch,
    save_checkout_ports,
)


def nearest_python(root: Path) -> Path | None:
    """The .venv this checkout runs on: its own, else the main checkout's — as frontend/scripts/export-openapi.mjs
    finds it. The search stops at the main checkout, so another project's .venv further up is never used."""
    for folder in (root, *root.parents):
        for candidate in (folder / ".venv" / "Scripts" / "python.exe", folder / ".venv" / "bin" / "python"):
            if candidate.is_file():
                return candidate
        if (folder / ".git").is_dir():
            return None
    return None


def main() -> int:
    worktree = is_worktree(REPO)
    if worktree:
        ports = claim_slot(REPO)
        save_checkout_ports(REPO, ports)
    else:
        ports = checkout_ports(REPO)

    python = nearest_python(REPO)
    if python is None:
        print("No .venv found in this checkout or the main checkout above it; create one first (see README).")
        return 1
    # Inside the checkout, keep the path relative so the main checkout's file reads as it always has.
    shown = python.relative_to(REPO).as_posix() if python.is_relative_to(REPO) else python.as_posix()

    launch = REPO / ".claude" / "launch.json"
    existing = json.loads(launch.read_text(encoding="utf-8")) if launch.is_file() else None
    configurations = launch_configurations(ports, shown, worktree=worktree)
    launch.parent.mkdir(exist_ok=True)
    launch.write_text(json.dumps(merge_launch(existing, configurations), indent=2) + "\n", encoding="utf-8")

    if worktree:
        print(f"Worktree {REPO.name}: slot {ports.slot}")
    else:
        print(f"Main checkout: live API http://127.0.0.1:{LIVE_API}, web http://127.0.0.1:{LIVE_WEB}")
    print(f"  sandbox API  http://127.0.0.1:{ports.sandbox_api}")
    print(f"  sandbox web  http://127.0.0.1:{ports.sandbox_web}")
    print(f"Wrote .claude/launch.json: {', '.join(c['name'] for c in configurations)}")
    if not (REPO / "frontend" / "node_modules").is_dir():
        print("frontend/node_modules is missing; install it once: npm --prefix frontend install")
    return 0


if __name__ == "__main__":
    sys.exit(main())
