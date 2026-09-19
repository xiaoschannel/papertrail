r"""Where API keys come from.

`.env` is gitignored, so it never reaches GitHub — but that also means a git worktree, which starts
with only tracked files, has none, and whatever runs there fails with "Missing credentials". Keeping a
copy per checkout is the problem, not the fix: copies drift, and rotating a key means remembering
every one of them. So exactly one file is read, the first of:

1. ``<repo>/.env`` — this checkout's own;
2. for a worktree under ``<repo>/.claude/worktrees/<name>``, the main checkout's — so a worktree needs
   nothing of its own. The search goes no higher than the main checkout (the first folder whose
   ``.git`` is a directory), so another project's ``.env`` further up is never read;
3. ``%APPDATA%\papertrail\.env`` (``~/.config/papertrail/.env`` elsewhere) — a machine-wide file for
   a clone that lives outside the main checkout.

A variable already exported to the process always wins over the file, for a one-off override.
Call :func:`load_env` once at startup (``api/main.py``, ``app.py``).
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def user_env_path() -> Path:
    """The machine-wide file, used only when no checkout in the tree above has one."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / "papertrail" / ".env"


def env_path() -> Path | None:
    """The one file that will be read, or None if there is none to read."""
    for folder in (REPO_ROOT, *REPO_ROOT.parents):
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate
        if (folder / ".git").is_dir():        # the main checkout: nothing above it belongs to this project
            break
    user = user_env_path()
    return user if user.is_file() else None


def load_env() -> Path | None:
    """Load that file, without overriding variables already exported. Returns the file used."""
    from dotenv import dotenv_values

    path = env_path()
    if path is None:
        return None
    for name, value in dotenv_values(path).items():
        # A blank line like "OPENAI_API_KEY=" must not count as "already set" for the SDKs.
        if value and not os.environ.get(name):
            os.environ[name] = value
    return path


def missing_keys(*names: str) -> list[str]:
    """Which of these variables are still unset — for a clearer message than the SDK's."""
    return [name for name in names if not os.environ.get(name)]
