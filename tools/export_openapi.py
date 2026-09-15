"""Snapshot the API's OpenAPI schema to ``frontend/openapi.json``.

The frontend's TypeScript types are generated from this file (``npm --prefix frontend run gen:api``),
so run this after changing an endpoint or a model in ``api/schemas.py``, then regenerate the types.
``tests/test_api_openapi.py`` fails while the snapshot is stale.

Run from the repo root::

    python tools/export_openapi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO_ROOT / "frontend" / "openapi.json"

sys.path.insert(0, str(REPO_ROOT))


def render() -> str:
    from api.main import create_app

    return json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    SNAPSHOT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"Wrote {SNAPSHOT.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
