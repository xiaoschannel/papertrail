"""Write the API's OpenAPI schema to ``frontend/openapi.json``.

The frontend's TypeScript types are generated from it (``src/api/schema.d.ts``). Both are generated,
not committed: ``npm --prefix frontend run gen:api`` runs this and then the type generator, and runs by
itself before ``dev``, ``build`` and ``typecheck``, so the types always match the API as it is.
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
