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


def test_accepting_files_every_page_and_remembers_the_name(archive_dir):
    key = _two_page_marked(archive_dir)
    document = workshop.find(archive_dir, key)
    extraction = ReceiptResult(document_type="receipt", language="ja", date="2025-08-10", time="14:20",
                               name="ローソン 池袋店", currency="JPY", address="", cost=300.0)

    placed = workshop.accept(archive_dir, document, _decision(), extraction)

    assert len(placed) == 2 and all("Rescued Shop" in path for path in placed)
    assert all((archive_dir / path).exists() for path in placed)
    assert not any(page.exists() for page in document.pages)      # nothing left behind in marked/
    assert {read_sidecar(archive_dir / p).review.verdict for p in placed} == {"accepted"}
    assert load_smart_match_cache(archive_dir)[key] == {
        "extracted": "ローソン 池袋店", "confirmed": "Rescued Shop", "extracted_phone": ""}
    assert workshop.marked_documents(archive_dir) == []


def test_tossing_moves_every_page_out_of_marked(archive_dir):
    key = _two_page_marked(archive_dir)
    document = workshop.find(archive_dir, key)

    tossed = workshop.toss(archive_dir, document)

    assert len(tossed) == 2 and all(path.startswith("tossed/") for path in tossed)
    assert not any(page.exists() for page in document.pages)
    assert {read_sidecar(archive_dir / p).review.verdict for p in tossed} == {"tossed"}


def test_ocr_text_joins_the_pages_and_keeps_the_boxes(archive_dir):
    _two_page_marked(archive_dir)
    document = workshop.find(archive_dir, "9:202-203")

    text, boxes = workshop.ocr_of(document)

    assert text.startswith("--- Page 1 ---") and "page two" in text and "--- Page 2 ---" in text
    assert boxes == []                                            # this fixture stored no boxes


def test_the_extractor_is_given_boxes_when_there_are_any():
    from models import DetectedBox

    plain, has_boxes = workshop.extractor_input("just text", [])
    assert (plain, has_boxes) == ("just text", False)

    boxes = [DetectedBox(ref_type="0", coords=[[1, 2, 3, 4]], text="合計 ¥300")]
    annotated, has_boxes = workshop.extractor_input("ignored", boxes)
    assert has_boxes and annotated == "--- Page 1 ---\n[P1-BOX-0] 合計 ¥300"


@pytest.mark.parametrize("treatment", ["none", "clahe", "contrast", "whiten"])
def test_every_enhancement_returns_a_usable_image(treatment):
    original = Image.new("RGB", (40, 80), "white")
    result = enhance(original, Enhancement(treatment=treatment))
    assert result.size == (40, 80) and result.mode == "RGB"


def test_enhancement_rotates_before_treating():
    original = Image.new("RGB", (40, 80), "white")
    assert enhance(original, Enhancement(top_points="left")).size == (80, 40)
    assert enhance(original, Enhancement(top_points="down")).size == (40, 80)
