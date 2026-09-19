"""Marked Workshop: documents (not files), and what accepting or tossing one does."""

import json
import shutil

import pytest
from PIL import Image

import workshop
from data import load_smart_match_cache, read_sidecar
from models import ReceiptResult, ReviewDecision
from scan_enhance import Enhancement, enhance


def _two_page_marked(archive_dir):
    """Turn the fixture's marked document into a two-page one, as a stapled receipt would be."""
    marked = archive_dir / "marked"
    image, sidecar_path = marked / "08102025142000_202.png", marked / "08102025142000_202.json"
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    data["document_key"] = "9:202-203"
    sidecar_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    second = data | {"original_filename": "08102025142100_203.png", "serial": 203,
                     "ocr": {"markdown": "page two", "succeeded": True}}
    shutil.copy(image, marked / "08102025142100_203.png")
    (marked / "08102025142100_203.json").write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")
    return "9:202-203"


def _decision(**changes):
    values = dict(verdict="accepted", document_type="receipt", name="Rescued Shop", date="2025-08-10",
                  time="14:20", cost=300.0, currency="JPY", comment="")
    values.update(changes)
    return ReviewDecision(**values)


def test_marked_files_are_listed_as_documents(archive_dir):
    [single] = workshop.marked_documents(archive_dir)
    assert single.key == "9:202" and len(single.pages) == 1

    key = _two_page_marked(archive_dir)
    [document] = workshop.marked_documents(archive_dir)          # both pages, one entry — issue #14
    assert document.key == key
    assert [path.name for path in document.pages] == ["08102025142000_202.png", "08102025142100_203.png"]


def test_accepting_files_every_page_and_keeps_what_the_model_read(archive_dir):
    key = _two_page_marked(archive_dir)
    document = workshop.find(archive_dir, key)
    read = document.first.extraction

    placed = workshop.accept(archive_dir, document, _decision())

    assert len(placed) == 2 and all("Rescued Shop" in path for path in placed)
    assert all((archive_dir / path).exists() for path in placed)
    assert not any(page.exists() for page in document.pages)      # nothing left behind in marked/
    assert {read_sidecar(archive_dir / p).review.verdict for p in placed} == {"accepted"}
    # the decision is what was confirmed; the extraction stays what the model read
    assert read_sidecar(archive_dir / placed[0]).extraction == read
    assert load_smart_match_cache(archive_dir)[key]["confirmed"] == "Rescued Shop"
    assert workshop.marked_documents(archive_dir) == []


def test_accepting_keeps_a_pending_reread_and_turns_the_pages_it_read(archive_dir):
    from PIL import Image

    from models import DetectedBox, OcrResult

    key = _two_page_marked(archive_dir)
    document = workshop.find(archive_dir, key)
    with Image.open(document.pages[0]) as page:
        width, height = page.size
    read = ReceiptResult(document_type="receipt", language="ja", date="2025-08-10", time="14:20",
                         name="ローソン 池袋店", currency="JPY", address="", cost=300.0,
                         field_sources={"cost": ["1:0"]})
    boxes = [DetectedBox(ref_type="0", coords=[[1, 2, 3, 4]], text="合計 ¥300")]
    reread = workshop.Reread(pages=tuple(document.filenames), results=[OcrResult(markdown="p1", boxes=boxes),
                                                                         OcrResult(markdown="p2")],
                             extraction=read, top_points="left", ocr_model="m", extractor="e")

    placed = workshop.accept(archive_dir, document, _decision(), reread)

    first = read_sidecar(archive_dir / placed[0])
    assert first.extraction == read and first.ocr.markdown == "p1" and first.ocr.boxes == boxes
    assert read_sidecar(archive_dir / placed[1]).ocr.markdown == "p2"
    with Image.open(archive_dir / placed[0]) as page:
        assert page.size == (height, width)                        # turned as it was read
    assert load_smart_match_cache(archive_dir)[key] == {
        "extracted": "ローソン 池袋店", "confirmed": "Rescued Shop", "extracted_phone": ""}


def test_a_reread_of_a_document_that_changed_is_dropped(archive_dir):
    key = _two_page_marked(archive_dir)
    document = workshop.find(archive_dir, key)
    store = workshop.Rereads()
    store.put(key, workshop.Reread(pages=("somebody else.png",), results=[], extraction=None,  # type: ignore[arg-type]
                                   top_points="", ocr_model="m", extractor="e"))
    assert store.get(document) is None and store.get(document) is None


def test_tossing_moves_every_page_out_of_marked(archive_dir):
    key = _two_page_marked(archive_dir)
    document = workshop.find(archive_dir, key)

    tossed = workshop.toss(archive_dir, document)

    assert len(tossed) == 2 and all(path.startswith("tossed/") for path in tossed)
    assert not any(page.exists() for page in document.pages)
    assert {read_sidecar(archive_dir / p).review.verdict for p in tossed} == {"tossed"}


def test_ocr_text_joins_the_pages(archive_dir):
    _two_page_marked(archive_dir)
    document = workshop.find(archive_dir, "9:202-203")

    text = workshop.ocr_of(document)

    assert text.startswith("--- Page 1 ---") and "page two" in text and "--- Page 2 ---" in text


def test_the_extractor_is_given_every_page_as_parse_gives_it():
    from models import DetectedBox, OcrResult

    plain, has_boxes = workshop.extractor_input([OcrResult(markdown="just text")])
    assert (plain, has_boxes) == ("--- Page 1 ---\njust text", False)

    boxes = [DetectedBox(ref_type="0", coords=[[1, 2, 3, 4]], text="合計 ¥300")]
    text, has_boxes = workshop.extractor_input([OcrResult(markdown="one"), OcrResult(markdown="two", boxes=boxes)])
    assert has_boxes
    assert text == ("--- Page 1 ---\none\n\n--- Page 2 ---\ntwo\n"
                    "--- Page 2 Grounding Boxes ---\n[P2-BOX-0] 合計 ¥300")


@pytest.mark.parametrize("treatment", ["none", "clahe", "contrast", "whiten"])
def test_every_enhancement_returns_a_usable_image(treatment):
    original = Image.new("RGB", (40, 80), "white")
    result = enhance(original, Enhancement(treatment=treatment))
    assert result.size == (40, 80) and result.mode == "RGB"


def test_denoising_around_a_treatment_keeps_the_image_usable():
    original = Image.new("RGB", (40, 80), "white")
    result = enhance(original, Enhancement(treatment="clahe", denoise_before=True, denoise_after=True))
    assert result.size == (40, 80) and result.mode == "RGB"


def test_enhancement_rotates_before_treating():
    original = Image.new("RGB", (40, 80), "white")
    assert enhance(original, Enhancement(top_points="left")).size == (80, 40)
    assert enhance(original, Enhancement(top_points="down")).size == (40, 80)


def test_a_scan_marked_by_hand_without_a_sidecar_is_listed_and_can_be_decided(archive_dir):
    import shutil

    marked = archive_dir / "marked"
    shutil.copy(marked / "08102025142000_202.png", marked / "hand placed.png")

    document = workshop.find(archive_dir, "hand placed")
    assert document is not None and document.first.review.verdict == "marked" and document.first.ocr is None

    [placed] = workshop.accept(archive_dir, document, _decision())
    assert read_sidecar(archive_dir / placed).original_filename == "hand placed.png"

