"""The SANDBOX environment: the API on a throwaway archive of invented scans, with fake OCR/LLM models.

It never reads or writes the live archive, never loads a real model (so it takes no VRAM from a live
OCR run), and listens on its own port — 8001 in the main checkout, the live API keeping 8000; a worktree's
own slot otherwise (dev_ports.py) — so every checkout's sandbox and the live app can all run at once:

    .venv/Scripts/python tools/sandbox_server.py
    npm --prefix frontend run dev:sandbox

Then open http://127.0.0.1:5174 (in a worktree, the port tools/worktree_setup.py printed) and walk
File Index -> Slice -> Group -> OCR -> Parse -> Review -> Archive. Pages 16 and 17 are sheets of meal
tickets to slice: 16 is a 3 x 3 grid that ran out after seven; 17, five in a 2 x 3 grid, was scanned
sideways and has to be turned upright first. Slicing 16 after 17 moves 17's crops. Everything lives under <repo>/.sandbox (gitignored).
A restart keeps whatever is there, so you don't lose a half-finished batch when the server picks up new
code; pass --fresh to start from nothing.
"""
import json, os, re, shutil, sys, time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
BOX = REPO / ".sandbox"
sys.path.insert(0, str(REPO))

# Settled before anything is built: a worktree that hasn't claimed its ports must stop here, not come up
# on the main checkout's sandbox ports. PAPERTRAIL_API_PORT still wins, for a one-off.
from dev_ports import NotSetUp, checkout_ports
try:
    PORT = int(os.environ.get("PAPERTRAIL_API_PORT") or checkout_ports(REPO).sandbox_api)
except NotSetUp as not_set_up:
    sys.exit(str(not_set_up))

FRESH = "--fresh" in sys.argv[1:]
REUSING = BOX.exists() and not FRESH
if BOX.exists() and FRESH:
    shutil.rmtree(BOX)
archive = BOX / "archive"
scans = BOX / "scans"
archive.mkdir(parents=True, exist_ok=True)
scans.mkdir(exist_ok=True)

font = ImageFont.truetype(r"C:\Windows\Fonts\meiryo.ttc", 30)
TEXT: dict[str, list[tuple[tuple[int, int, int, int], str]]] = {}


def scan(name, lines, rotate=None):
    w, h = 600, 1000
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, w - 5, h - 5], outline="#bbb", width=3)
    for (x1, y1, x2, y2), text in lines:
        d.text((x1 / 1000 * w + 4, y1 / 1000 * h + 2), text, fill="black", font=font)
    TEXT[name] = lines
    if REUSING and (scans / name).exists():
        return          # the scan may have been rotated or re-filed since; leave it alone
    if rotate:
        img = img.transpose(rotate)
    img.save(scans / name)


def receipt(name, shop, when, total, rotate=None, extra=()):
    scan(name, [((80, 40, 880, 104), shop), ((80, 140, 470, 176), when), *extra, ((80, 300, 520, 338), f"合計 ¥{total}")], rotate)


# Invented scans, Canon ImageFormula names: MMDDYYYYhhmmss_serial.png
receipt("03012026100000_1.png", "Sandbox Bakery", "2026/02/27 08:10", 480)
receipt("03012026100010_2.png", "Kissa Example", "2026/02/27 15:30", 950, rotate=Image.Transpose.ROTATE_90)
receipt("03012026100020_3.png", "Demo Mart 駅前店", "2026/02/28 19:02", 1320)
scan("03012026100030_4.png", [((80, 40, 880, 104), "Hotel Placeholder"), ((80, 140, 470, 176), "2026/02/20 11:00"), ((80, 220, 700, 258), "Page 1 of 2")])
scan("03012026100040_5.png", [((80, 40, 880, 104), "Hotel Placeholder"), ((80, 220, 700, 258), "Page 2 of 2"), ((80, 300, 520, 338), "合計 ¥18000")])
receipt("03012026100050_6.png", "Ramen Testya", "2026/02/26 12:45", 1100, rotate=Image.Transpose.ROTATE_180)
scan("03012026100100_7.png", [((80, 40, 880, 104), "(blank page)")])
receipt("03012026100110_8.png", "Coffee Stand Foo", "2026/02/25 09:05", 420)
for i in range(9, 15):
    receipt(f"030120261002{i:02d}_{i}.png", f"Shop Number {i}", f"2026/02/{i + 5:02d} 10:{i:02d}", 100 * i)
# The same shop, shouted: real scans come out styled differently and smart match has to see through it.
receipt("03012026100215_15.png", "COFFEE STAND FOO", "2026/02/24 09:12", 380)


def ticket_sheet(name, rows, cols, tickets, rotate=None):
    """A sheet of small meal tickets taped on in a grid, the last cells left empty: for the Slice page.

    Each ticket's crop (named as slicing names it) gets its own text, so OCR reads a sliced ticket.
    """
    cell_w, cell_h = 300, 380
    img = Image.new("RGB", (cols * cell_w + 60, rows * cell_h + 60), "#f4f1ea")
    d = ImageDraw.Draw(img)
    small = ImageFont.truetype(r"C:\Windows\Fonts\meiryo.ttc", 26)
    for i, (shop, when, total) in enumerate(tickets):
        r, c = divmod(i, cols)
        x, y = 30 + c * cell_w, 30 + r * cell_h
        d.rectangle([x + 18, y + 18, x + cell_w - 18, y + cell_h - 18], fill="white", outline="#999", width=2)
        for dy, text in ((40, "食券"), (100, shop), (180, when), (260, f"合計 ¥{total}")):
            d.text((x + 36, y + dy), text, fill="black", font=small)
        TEXT[f"{Path(name).stem}.r{r + 1}c{c + 1}{Path(name).suffix}"] = [
            ((80, 40, 900, 140), shop), ((80, 400, 900, 500), when), ((80, 650, 900, 750), f"合計 ¥{total}")]
    TEXT[name] = [((40, 20, 900, 60), "(a sheet of meal tickets)")]
    if not (REUSING and (scans / name).exists()):
        (img.transpose(rotate) if rotate else img).save(scans / name)


ticket_sheet("03012026100216_16.png", 3, 3, [
    ("Ramen Testya", f"2026/02/2{d} 12:0{d}", 850 + 50 * (d % 3)) for d in range(1, 8)])
ticket_sheet("03012026100217_17.png", 2, 3, [
    ("Soba Sampleya", f"2026/02/1{d} 11:3{d}", 700 + 100 * (d % 2)) for d in range(1, 6)],
    rotate=Image.Transpose.ROTATE_90)


class FakeOcr:
    grounding = True

    def run(self, path, structured=False):
        time.sleep(0.6)
        # the workshop hands OCR a treated copy ("<name>.enhanced.png"): answer for the original
        name = path.name.replace(".enhanced", "")
        lines = TEXT.get(name) or TEXT.get(re.sub(r"\.\d+-\d+-\d+-\d+(?=\.[a-z]+$)", "", name), [])   # a crop's box
        if structured:
            return "\n".join(f"<|ref|>{t}<|/ref|><|det|>[[{x1}, {y1}, {x2}, {y2}]]<|/det|>" for (x1, y1, x2, y2), t in lines)
        return "\n".join(t for _, t in lines)

    def teardown(self):
        print("[sandbox] unloaded Fake OCR", flush=True)


def fake_extract(ocr_text, has_boxes=False, custom_instruction="", on_usage=None):
    from models import ReceiptResult, CorruptedResult, TokenUse
    time.sleep(0.5)
    if on_usage is not None:
        # Roughly what a hosted model would report: four characters to a token, the fixed prompt cached.
        prompt = 1100 + len(ocr_text) // 4
        on_usage(TokenUse(prompt=prompt, cached=1024, completion=320, thinking=180,
                          raw={"model": "fake-model-2026-01-01", "service_tier": "default",
                               "system_fingerprint": "fp_sandbox",
                               "usage": {"prompt_tokens": prompt, "completion_tokens": 320,
                                         "total_tokens": prompt + 320,
                                         "prompt_tokens_details": {"cached_tokens": 1024, "audio_tokens": 0},
                                         "completion_tokens_details": {"reasoning_tokens": 180}}}))
    # Given boxes ("[P1-BOX-3] text"), cite them like a grounding extractor, so the scans get field boxes.
    cited = [(f"{m[1]}:{m[2]}", m[3]) for m in (re.match(r"\[P(\d+)-BOX-(\d+)\] (.*)", l) for l in ocr_text.splitlines()) if m]
    lines = [text for _, text in cited] if cited else \
        [l for l in ocr_text.splitlines() if l.strip() and not l.startswith("--- Page")]
    refs = dict((text, ref) for ref, text in reversed(cited))
    if not lines or lines[0].startswith("(blank"):
        return CorruptedResult(document_type="corrupted")
    when = next((l for l in lines if re.match(r"\d{4}/\d\d/\d\d", l)), "")
    total = next((l for l in lines if l.startswith("合計")), "¥0")
    date, _, clock = when.partition(" ")
    sources = {field: [refs[text]] for field, text in (("name", lines[0]), ("date", when), ("time", when),
                                                      ("cost", total)) if text in refs}
    return ReceiptResult(document_type="receipt", language="ja", date=date.replace("/", "-"), time=clock,
                         name=lines[0], currency="JPY", address="", cost=float(total.split("¥")[-1]),
                         field_sources=sources)


if not (BOX / "config.json").exists():
    cfg = {"batch_output_path": str(archive), "input_image_path": str(scans), "normalize_engine": "string",
           "indexing_scheme": "Canon ImageFormula"}
    (BOX / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

import settings
settings.CONFIG_PATH = BOX / "config.json"
import experiment_runs
experiment_runs.ROOT = BOX / "experiment"
from api import ingest_registry
ingest_registry.ocr_providers = lambda: {"Fake OCR (sandbox)": FakeOcr()}
# The hosted models' own names too, so the Experiment bench can price a run the way it would for real.
from extraction import PRICES
ingest_registry.extractors = lambda: {"Fake LLM (sandbox)": fake_extract,
                                      **{name: fake_extract for name in PRICES}}
ingest_registry.unload_extractor = lambda name: (lambda: print(f"[sandbox] unloaded {name}", flush=True))

import uvicorn
from api.main import create_app
uvicorn.run(create_app(), host="127.0.0.1", port=PORT)
