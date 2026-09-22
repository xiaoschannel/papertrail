"""The SANDBOX environment: the API on a throwaway archive of invented scans, with fake OCR/LLM models.

It never reads or writes the live archive, never loads a real OCR or LLM model (so it takes no VRAM from a
live OCR run; the check for turned scans does run its small CPU model, orientation.py), and listens on its
own port — 8001 in the main checkout, the live API keeping 8000; a worktree's own slot otherwise
(dev_ports.py) — so every checkout's sandbox and the live app can all run at once:

    .venv/Scripts/python tools/sandbox_server.py
    npm --prefix frontend run dev:sandbox

Then open http://127.0.0.1:5174 (in a worktree, the port tools/worktree_setup.py printed). A new sandbox
starts in one shared state with something to try on every page: a batch already filed (some of it marked,
for the Marked Workshop), a batch waiting in Review, and a batch indexed but not read yet, to walk from
Fix Rotation. tools/sandbox_seed.py builds it, and says what each stage holds for which feature.

Everything lives under <repo>/.sandbox (gitignored). A restart keeps whatever is there, so you don't lose a
half-finished batch when the server picks up new code. To go back to the shared state, stop the server and
run tools/sandbox_reset.py, or start it with --fresh.
"""
import os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOX = REPO / ".sandbox"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

# Settled before anything is built: a worktree that hasn't claimed its ports must stop here, not come up
# on the main checkout's sandbox ports. PAPERTRAIL_API_PORT still wins, for a one-off.
from dev_ports import NotSetUp, checkout_ports
try:
    PORT = int(os.environ.get("PAPERTRAIL_API_PORT") or checkout_ports(REPO).sandbox_api)
except NotSetUp as not_set_up:
    sys.exit(str(not_set_up))

import sandbox_seed as seed

sb = seed.prepare(BOX, fresh="--fresh" in sys.argv[1:])

import experiment_runs
experiment_runs.ROOT = BOX / "experiment"
from api import ingest_registry
ingest_registry.ocr_providers = lambda: {seed.OCR_MODEL: seed.FakeOcr(sb)}
# The hosted models' own names too, so the Experiment bench can price a run the way it would for real.
from extraction import PRICES
fake_extract = seed.fake_extractor(sb)
ingest_registry.extractors = lambda: {seed.EXTRACTOR: fake_extract, **{name: fake_extract for name in PRICES}}
ingest_registry.unload_extractor = lambda name: (lambda: print(f"[sandbox] unloaded {name}", flush=True))

import uvicorn
from api.main import create_app
uvicorn.run(create_app(), host="127.0.0.1", port=PORT)
