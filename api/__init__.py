"""FastAPI backend for Papertrail.

Thin HTTP layer over the pure Python core. Importing this package puts
the repo root on sys.path so the top-level modules (settings, data, viz_records,
analytics, ...) resolve whether launched via uvicorn or pytest.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
