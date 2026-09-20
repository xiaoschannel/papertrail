"""What the hosted models will take, per minute, as the account is set up now.

A run paces itself on the limits each response carries (``rate_budget``), which is enough and needs no
special credential. This prints them ahead of time instead -- useful when deciding how many documents
to extract at once, or when a run is being held back and the question is by how much.

    python tools/rate_limits.py            # the models Papertrail uses
    python tools/rate_limits.py --all      # every model the project has a limit for

Needs OPENAI_ADMIN_KEY (see .env.example): an admin key reads the organization, so nothing that runs
unattended should hold one. Read-only: this asks, and changes nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from env import load_env
from extraction import OPENAI_MODELS

API = "https://api.openai.com/v1/organization"


def rows(key: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {key}"}
    projects = httpx.get(f"{API}/projects?limit=100", headers=headers, timeout=30).json().get("data", [])
    found = []
    for project in projects:
        after, page = None, None
        while page is None or page.get("has_more"):
            url = f"{API}/projects/{project['id']}/rate_limits?limit=100" + (f"&after={after}" if after else "")
            page = httpx.get(url, headers=headers, timeout=30).json()
            batch = page.get("data", [])
            found += [{**row, "project": project.get("name") or project["id"]} for row in batch]
            if not batch:
                break
            after = batch[-1]["id"]
    return found


def main() -> int:
    load_env()
    key = os.environ.get("OPENAI_ADMIN_KEY")
    if not key:
        print("Set OPENAI_ADMIN_KEY in .env to read the project's limits (see .env.example).")
        return 1

    everything = "--all" in sys.argv
    wanted = set(OPENAI_MODELS)
    listed = [row for row in rows(key) if everything or row["model"] in wanted]
    if not listed:
        print("No rate limits listed for", ", ".join(sorted(wanted)) if not everything else "this project")
        return 1

    print(f"{'project':<16} {'model':<28} {'requests/min':>13} {'tokens/min':>13}")
    for row in sorted(listed, key=lambda r: (r["project"], r["model"])):
        print(f"{row['project'][:16]:<16} {row['model']:<28} "
              f"{row.get('max_requests_per_1_minute', '—'):>13} {row.get('max_tokens_per_1_minute', '—'):>13}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
