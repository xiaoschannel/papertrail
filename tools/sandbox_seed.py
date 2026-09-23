"""The sandbox's one shared state: invented scans at every stage of the pipeline, and the fake models.

A new sandbox (or ``--fresh``) starts in a state where every page of the app has something to try, built
by running the real pipeline on invented scans with the fake models below, so it can't drift from what
the app really produces:

* **Batch 1, archived** — filed under ``YYYY/MM``, a few marked: the Marked Workshop, Receipt Detail, the
  Calendar and the other Visualize pages.
* **Batch 2, parsed** — OCR'd and extracted, nothing decided yet: Review.
* **Batch 3, indexed** — a batch not yet read: Fix Rotation, Slice and Group, then OCR, Parse, Review and
  Archive from the start. Pick it in the batch picker: the ingest pages open on the oldest unarchived
  batch, which is batch 2.

No scans are left unindexed: nothing needs them yet (File Index's scheme check would). A feature that does
adds a stage of them, drawn after the last ``index()`` in ``build``.

There is one state, not one per feature: **a feature adds its examples to the stage where its page reads
them** (a scan to ``ingest_batch``, a page to review to ``review_batch``, a filed or marked document to
``archived_batch``), and says in a comment what to try. Everything is invented; the repo is public.

What each stage holds for a feature today:

* Slice: two sheets of meal tickets in batch 3 (one scanned sideways).
* Turned scans: three scans in batch 3 were fed in turned (2 and 17 sideways, 6 upside down), for
  Fix Rotation to point out.
* Trim: a receipt with a coupon under it at every stage. In batch 3, to trim in Group; in Review, one
  trimmed before OCR (read as the receipt alone) and one trimmed after (read again by the next OCR); in
  the archive, a filed receipt trimmed, and a marked one untrimmed (the Workshop's Trim row).
* Tilted scans: two receipts in batch 3 were fed in crooked (10 a little clockwise, 13 further the other way),
  for Fix Rotation to point out; any scan can be straightened from its full-size view. The fake OCR
  boxes each line where it lies on the crooked page, as a real one would. Turning or straightening a page
  after OCR sends it back to OCR and Parse.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

SCHEME = "Canon ImageFormula"
OCR_MODEL, EXTRACTOR = "Fake OCR (sandbox)", "Fake LLM (sandbox)"
_FONT_PATH = Path(r"C:\Windows\Fonts\meiryo.ttc")

Lines = list[tuple[tuple[int, int, int, int], str]]


def _boxed_on_tilted(lines: Lines, degrees: float, size: tuple[int, int] = (600, 1000)) -> Lines:
    """Each line's box (0-1000) once the page is turned ``degrees`` counter-clockwise on a canvas grown to
    hold it: the box around the turned box's corners, as OCR draws a box around a sloping line."""
    import math

    w, h = size
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    out_w, out_h = abs(w * cos) + abs(h * sin), abs(w * sin) + abs(h * cos)

    def turned(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - w / 2, y - h / 2
        return out_w / 2 + dx * cos + dy * sin, out_h / 2 - dx * sin + dy * cos

    boxed = []
    for (x1, y1, x2, y2), text in lines:
        corners = [turned(x * w / 1000, y * h / 1000) for x in (x1, x2) for y in (y1, y2)]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        boxed.append(((round(min(xs) * 1000 / out_w), round(min(ys) * 1000 / out_h),
                       round(max(xs) * 1000 / out_w), round(max(ys) * 1000 / out_h)), text))
    return boxed


def font(size: int):
    """Meiryo where there is one (the sandbox runs on Windows); Pillow's own font elsewhere, e.g. in CI."""
    return ImageFont.truetype(str(_FONT_PATH), size) if _FONT_PATH.exists() else ImageFont.load_default(size)


class Sandbox(BaseModel):
    """A sandbox folder: its scans, its archive, and what the fake OCR reads on every scan drawn."""

    root: Path
    #: seconds the fake models take per call; nothing waits while the state is being built
    ocr_delay: float = 0.6
    extract_delay: float = 0.5
    text: dict[str, Lines] = {}

    @property
    def scans(self) -> Path:
        return self.root / "scans"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    def filed(self) -> set[str]:
        """The scans the archive holds: Archive took them out of ``scans/``, so they aren't drawn again."""
        from data import filed_scan_pages

        return set(filed_scan_pages(self.archive)) if self.archive.is_dir() else set()

    # --- drawing --------------------------------------------------------------------------------------
    def scan(self, name: str, lines: Lines, rotate=None, tilt: float = 0.0) -> None:
        """A 600 x 1000 page with these lines on it (boxes on OCR's 0-1000 scale), written if not there yet.

        An existing scan is left alone: it may have been rotated since; so is one the archive holds. ``tilt``
        feeds it in crooked: turned that many degrees counter-clockwise on the white scanner bed, each line
        then boxed where it lies.
        """
        self.text[name] = _boxed_on_tilted(lines, tilt) if tilt else lines
        if (self.scans / name).exists() or name in self.filed():
            return
        w, h = 600, 1000
        img = Image.new("RGB", (w, h), "white")
        d = ImageDraw.Draw(img)
        d.rectangle([4, 4, w - 5, h - 5], outline="#bbb", width=3)
        for (x1, y1, x2, y2), text in lines:
            d.text((x1 / 1000 * w + 4, y1 / 1000 * h + 2), text, fill="black", font=font(30))
        self.scans.mkdir(parents=True, exist_ok=True)
        img = img.transpose(rotate) if rotate else img
        if tilt:
            img = img.rotate(tilt, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        img.save(self.scans / name)

    def receipt(self, name: str, shop: str, when: str, total: int, rotate=None, extra: Lines = (),
                tilt: float = 0.0) -> None:
        self.scan(name, [((80, 40, 880, 104), shop), ((80, 140, 470, 176), when), *extra,
                         ((80, 300, 520, 338), f"合計 ¥{total}")], rotate, tilt)

    def coupon_receipt(self, name: str, shop: str, when: str, total: int) -> None:
        """A receipt with a coupon printed under it, whose own total extraction takes for the receipt's
        (it comes first): trimmed off, the receipt's is read. Trim at COUPON_TRIM to cut the coupon off."""
        self.receipt(name, shop, when, total, extra=(
            ((80, 640, 880, 700), "- キリトリ - COUPON - キリトリ -"), ((80, 740, 880, 790), "次回 ¥200 OFF"),
            ((80, 830, 700, 870), "合計 ¥3000"), ((80, 890, 700, 930), "以上のお買い物で")))

    def ticket_sheet(self, name: str, rows: int, cols: int, tickets, rotate=None) -> None:
        """A sheet of small meal tickets taped on in a grid, the last cells left empty: for the Slice page.

        Each ticket's crop (named as slicing names it) gets its own text, so OCR reads a sliced ticket.
        """
        for i, (shop, when, total) in enumerate(tickets):
            r, c = divmod(i, cols)
            self.text[f"{Path(name).stem}.r{r + 1}c{c + 1}{Path(name).suffix}"] = [
                ((80, 40, 900, 140), shop), ((80, 400, 900, 500), when), ((80, 650, 900, 750), f"合計 ¥{total}")]
        self.text[name] = [((40, 20, 900, 60), "(a sheet of meal tickets)")]
        if (self.scans / name).exists() or name in self.filed():
            return
        cell_w, cell_h = 300, 380
        img = Image.new("RGB", (cols * cell_w + 60, rows * cell_h + 60), "#f4f1ea")
        d = ImageDraw.Draw(img)
        for i, (shop, when, total) in enumerate(tickets):
            r, c = divmod(i, cols)
            x, y = 30 + c * cell_w, 30 + r * cell_h
            d.rectangle([x + 18, y + 18, x + cell_w - 18, y + cell_h - 18], fill="white", outline="#999", width=2)
            for dy, text in ((40, "食券"), (100, shop), (180, when), (260, f"合計 ¥{total}")):
                d.text((x + 36, y + dy), text, fill="black", font=font(26))
        self.scans.mkdir(parents=True, exist_ok=True)
        (img.transpose(rotate) if rotate else img).save(self.scans / name)


#: Where a coupon receipt's trim cuts: below its total (y 338), above the coupon's tear line (y 640).
COUPON_TRIM = (0.0, 0.5)


# --- the stages: invented Canon ImageFormula names, MMDDYYYYhhmmss_serial.png; a batch's serials restart ---
def archived_batch(sb: Sandbox) -> None:
    """Batch 1, filed. MARKED go to the Marked Workshop; the rest are accepted into the archive."""
    sb.receipt("02152026090000_1.png", "Sandbox Bakery", "2026/02/10 08:10", 480)
    sb.receipt("02152026090010_2.png", "Kissa Example", "2026/02/10 15:30", 950)
    # Trim: filed trimmed, so Receipt Detail and the Calendar show the receipt alone, with a shadow at the cut.
    sb.coupon_receipt("02152026090020_3.png", "Sandbox Supermarket", "2026/02/11 17:40", 2150)
    # Trim: marked untrimmed, read with its coupon (so its cost is the coupon's): try the Workshop's Trim row.
    sb.coupon_receipt("02152026090030_4.png", "Sandbox Drugstore", "2026/02/12 19:15", 1680)
    sb.receipt("02152026090040_5.png", "Ramen Testya", "2026/02/13 12:45", 1100)
    sb.receipt("02152026090050_6.png", "Coffee Stand Foo", "2026/02/14 09:05", 420)


ARCHIVED_TRIMMED_BEFORE_OCR = {3: COUPON_TRIM}
MARKED = {4: "the total looks like the coupon's", 5: "faded print"}


def review_batch(sb: Sandbox) -> None:
    """Batch 2, OCR'd and parsed, waiting in Review."""
    # Trim: trimmed before OCR, so it is read as the receipt alone (its cost is right, its boxes on the band).
    sb.coupon_receipt("02252026090000_1.png", "Sandbox Supermarket", "2026/02/20 18:05", 2380)
    # Trim: read whole, then trimmed: "trimmed since it was read" in Review, and OCR has it to read again.
    sb.coupon_receipt("02252026090010_2.png", "Sandbox Drugstore", "2026/02/21 10:20", 760)
    sb.receipt("02252026090020_3.png", "Kissa Example", "2026/02/21 15:00", 890)
    sb.receipt("02252026090030_4.png", "Demo Mart 駅前店", "2026/02/22 19:02", 1320)


REVIEW_TRIMMED_BEFORE_OCR = {1: COUPON_TRIM}
REVIEW_TRIMMED_AFTER_OCR = {2: COUPON_TRIM}


def ingest_batch(sb: Sandbox) -> None:
    """Indexed as batch 3 and not read yet: walk it from Fix Rotation."""
    sb.receipt("03012026100000_1.png", "Sandbox Bakery", "2026/02/27 08:10", 480)
    sb.receipt("03012026100010_2.png", "Kissa Example", "2026/02/27 15:30", 950, rotate=Image.Transpose.ROTATE_90)
    sb.receipt("03012026100020_3.png", "Demo Mart 駅前店", "2026/02/28 19:02", 1320)
    sb.scan("03012026100030_4.png", [((80, 40, 880, 104), "Hotel Placeholder"), ((80, 140, 470, 176), "2026/02/20 11:00"),
                                      ((80, 220, 700, 258), "Page 1 of 2")])
    sb.scan("03012026100040_5.png", [((80, 40, 880, 104), "Hotel Placeholder"), ((80, 220, 700, 258), "Page 2 of 2"),
                                      ((80, 300, 520, 338), "合計 ¥18000")])
    sb.receipt("03012026100050_6.png", "Ramen Testya", "2026/02/26 12:45", 1100, rotate=Image.Transpose.ROTATE_180)
    sb.scan("03012026100100_7.png", [((80, 40, 880, 104), "(blank page)")])
    sb.receipt("03012026100110_8.png", "Coffee Stand Foo", "2026/02/25 09:05", 420)
    # Tilted: 10 and 13 were fed in crooked (with item lines, so there is text to level): Fix Rotation points them out.
    tilts = {10: -4.0, 13: 7.0}
    for i in range(9, 15):
        items = [((80, 200 + 36 * n, 700, 232 + 36 * n), f"品目 {n + 1}    ¥{50 * (n + i)}") for n in range(2)]             if i in tilts else ()
        sb.receipt(f"030120261002{i:02d}_{i}.png", f"Shop Number {i}", f"2026/02/{i + 5:02d} 10:{i:02d}", 100 * i,
                   extra=items, tilt=tilts.get(i, 0.0))
    # The same shop, shouted: real scans come out styled differently and smart match has to see through it.
    sb.receipt("03012026100215_15.png", "COFFEE STAND FOO", "2026/02/24 09:12", 380)
    # Slice: 16 is a 3 x 3 grid that ran out after seven; 17, five in a 2 x 3 grid, was scanned sideways and
    # has to be turned upright first. Slicing 16 after 17 moves 17's crops.
    sb.ticket_sheet("03012026100216_16.png", 3, 3, [
        ("Ramen Testya", f"2026/02/2{d} 12:0{d}", 850 + 50 * (d % 3)) for d in range(1, 8)])
    sb.ticket_sheet("03012026100217_17.png", 2, 3, [
        ("Soba Sampleya", f"2026/02/1{d} 11:3{d}", 700 + 100 * (d % 2)) for d in range(1, 6)],
        rotate=Image.Transpose.ROTATE_90)
    # Trim: open it in Group and trim the coupon off; OCR then reads the receipt's own total.
    sb.coupon_receipt("03012026100218_18.png", "Sandbox Supermarket", "2026/02/23 17:40", 2150)


# --- the fake models ------------------------------------------------------------------------------------
class FakeOcr:
    """Reads back the lines a scan was drawn with: only those the band it is handed keeps, measured on it."""

    grounding = True

    def __init__(self, sb: Sandbox):
        self.sb = sb

    def run(self, path: Path, structured: bool = False) -> str:
        time.sleep(self.sb.ocr_delay)
        # OCR is handed copies: the workshop's treated "<name>.enhanced.png", a trimmed page's
        # "<stem>.<random>.trimmed.png" in the temp folder. Answer for the original.
        name = re.sub(r"\.[^.]+\.trimmed\.png$", ".png", path.name.replace(".enhanced", ""))
        lines = self.sb.text.get(name) or self.sb.text.get(re.sub(r"\.\d+-\d+-\d+-\d+(?=\.[a-z]+$)", "", name), [])
        band = self._band_read(name, path) if name != path.name else None
        if band is not None:
            top, height = band.top * 1000, (band.bottom - band.top) * 1000
            lines = [((x1, max(0, round((y1 - top) / height * 1000)), x2, min(1000, round((y2 - top) / height * 1000))), t)
                     for (x1, y1, x2, y2), t in lines if y2 > top and y1 < top + height]
        if structured:
            return "\n".join(f"<|ref|>{t}<|/ref|><|det|>[[{x1}, {y1}, {x2}, {y2}]]<|/det|>" for (x1, y1, x2, y2), t in lines)
        return "\n".join(t for _, t in lines)

    def _band_read(self, original: str, path: Path):
        """The band of ``original`` that the image at ``path`` is.

        Mid-ingest pages keep their trim in trims.json, marked ones in their sidecar. A marked page handed
        over turned a quarter (its stored size, swapped) is the whole page: the Workshop reads it whole.
        """
        from data import load_trims, read_sidecar
        from models import batch_serial_key, filename_to_batch_serial, load_scan_index

        marked = self.sb.archive / "marked" / original
        if marked.exists():
            with Image.open(path) as given, Image.open(marked) as stored:
                if given.size == stored.size[::-1] != stored.size:
                    return None
            sidecar = read_sidecar(marked)
            return sidecar.trim if sidecar else None
        try:
            files = filename_to_batch_serial(load_scan_index(self.sb.archive))
        except FileNotFoundError:
            return None
        key = files.get(original) or files.get(f"slices/{original}")        # a crop lives in the input's slices/
        return load_trims(self.sb.archive).get(batch_serial_key(*key)) if key else None

    def teardown(self) -> None:
        print("[sandbox] unloaded Fake OCR", flush=True)


def fake_extractor(sb: Sandbox):
    """An extractor that reads the fake OCR's lines back: the first is the shop, the first 合計 the total."""

    def fake_extract(ocr_text, has_boxes=False, custom_instruction="", on_usage=None):
        from models import CorruptedResult, ReceiptResult, TokenUse
        time.sleep(sb.extract_delay)
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
        sources = {name: [refs[text]] for name, text in (("name", lines[0]), ("date", when), ("time", when),
                                                         ("cost", total)) if text in refs}
        return ReceiptResult(document_type="receipt", language="ja", date=date.replace("/", "-"), time=clock,
                             name=lines[0], currency="JPY", address="", cost=float(total.split("¥")[-1]),
                             field_sources=sources)

    return fake_extract


# --- building the state -----------------------------------------------------------------------------------
def remove_tree(path: Path) -> None:
    """Delete a folder, read-only files and all: the folder's history (``.git``) is kept in those."""
    import os
    import shutil
    import stat

    def writable_then_again(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=writable_then_again)


class _Quiet:
    """What the pipeline's steps report to, when nobody is watching."""

    cancelled = False
    job_id = "sandbox-seed"

    def set_total(self, total): pass
    def tick(self, ok=True, item="", error=""): pass
    def say(self, message): pass
    def record(self, run): pass


def draw(sb: Sandbox) -> None:
    """Every stage's scans: drawn where missing, and registered so the fake OCR can read them."""
    archived_batch(sb)
    review_batch(sb)
    ingest_batch(sb)


def prepare(root: Path, fresh: bool = False) -> Sandbox:
    """The sandbox at ``root``, ready to serve: in the shared state if it is new (or ``fresh``, which
    wipes it first), or as it was left, with every scan registered for the fake OCR.

    Points the app's config at it, as the server does, so the pipeline and the API use the sandbox.
    """
    import json

    import settings

    if fresh and root.exists():
        remove_tree(root)
    sb = Sandbox(root=root)
    sb.archive.mkdir(parents=True, exist_ok=True)
    sb.scans.mkdir(exist_ok=True)
    config = root / "config.json"
    saved = (json.loads(config.read_text(encoding="utf-8")) if config.exists()
             else {"normalize_engine": "string", "indexing_scheme": SCHEME})
    # The sandbox is always one Papertrail folder, here. A sandbox made before there was one names its scan
    # folder and archive instead: those settings are gone, so its config is moved onto the folder.
    wanted = {**{k: v for k, v in saved.items() if k not in ("input_image_path", "batch_output_path")},
              "root_path": str(root)}
    if wanted != saved:
        config.write_text(json.dumps(wanted, indent=2), encoding="utf-8")
    settings.CONFIG_PATH = config
    # A new sandbox is built into the shared state; one in use keeps what was done in it.
    if not (sb.archive / "batches.json").exists() and not any(sb.scans.iterdir()):
        print(f"[sandbox] building the shared state in {root}...", flush=True)
        build(sb)
    else:
        draw(sb)
    return sb


def build(sb: Sandbox) -> None:
    """Take an empty sandbox to the shared state, one stage at a time, through the real pipeline."""
    import ingest_pipeline as pipeline
    import review_logic as rl
    from data import save_decisions
    from models import ReviewDecision, Trim, batch_serial_key

    delays = sb.ocr_delay, sb.extract_delay
    sb.ocr_delay = sb.extract_delay = 0.0            # nobody is waiting on a fake model yet
    ocr, extract = FakeOcr(sb), fake_extractor(sb)

    def index() -> int:
        proposal = pipeline.propose_index(sb.scans, sb.archive, SCHEME)
        [batch] = pipeline.confirm_index(sb.scans, sb.archive, SCHEME, proposal.token)
        return batch.batch_id

    def trim(batch_id: int, trims: dict[int, tuple[float, float]]) -> None:
        for serial, (top, bottom) in trims.items():
            pipeline.trim_page(sb.archive, batch_serial_key(batch_id, serial), Trim(top=top, bottom=bottom))

    def read_and_parse(batch_id: int, trimmed_after: dict[int, tuple[float, float]] = {}) -> None:
        plan = pipeline.plan_ocr(sb.archive, sb.scans, batch_id, reprocess=False, limit=0)
        pipeline.run_ocr(sb.archive, plan.items, ocr, structured=True, progress=_Quiet(), model=OCR_MODEL,
                         shuffle=False)
        trim(batch_id, trimmed_after)                # after the read: due to be read again
        pipeline.run_parse(sb.archive, pipeline.plan_parse(sb.archive, reprocess=False, limit=0), extract, "",
                           _Quiet(), model=EXTRACTOR, shuffle=False)

    try:
        archived_batch(sb)
        first = index()
        trim(first, ARCHIVED_TRIMMED_BEFORE_OCR)
        read_and_parse(first)
        from data import load_extractions
        decisions = {}
        for key, extraction in load_extractions(sb.archive).items():
            defaults = rl.form_defaults(extraction)
            serial = int(key.split(":")[1].split("-")[0])
            decisions[key] = ReviewDecision(
                verdict="marked" if serial in MARKED else "accepted", document_type=defaults.document_type,
                name=defaults.name, date=defaults.date, time=defaults.time, cost=defaults.cost,
                currency=defaults.currency, comment=MARKED.get(serial, ""))
        save_decisions(sb.archive, decisions)
        pipeline.run_archive(sb.archive, sb.scans, _Quiet())

        review_batch(sb)
        second = index()
        trim(second, REVIEW_TRIMMED_BEFORE_OCR)
        read_and_parse(second, REVIEW_TRIMMED_AFTER_OCR)

        ingest_batch(sb)
        index()
    finally:
        sb.ocr_delay, sb.extract_delay = delays
