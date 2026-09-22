"""Keys and document grouping — the backbone identifiers the whole app uses."""

import json

import data
import models
from models import DocumentIndex, DocumentKey, FileKey


# --- FileKey ---------------------------------------------------------------
def test_filekey_parse_roundtrip():
    fk = FileKey.parse("3:42")
    assert fk is not None and fk.batch_id == 3 and fk.serial == 42
    assert str(fk) == "3:42"


def test_filekey_parse_invalid():
    assert FileKey.parse("abc") is None
    assert FileKey.parse("1:2:3") is None
    assert FileKey.parse("1:x") is None


def test_filekey_hash_eq():
    assert FileKey(1, 2) == FileKey(1, 2)
    assert FileKey(1, 2) != FileKey(1, 3)
    assert len({FileKey(1, 2), FileKey(1, 2)}) == 1


# --- DocumentKey -----------------------------------------------------------
def test_documentkey_single_vs_multipage():
    single = DocumentKey.parse("1:5")
    assert single is not None and not single.is_multi_page and str(single) == "1:5"

    multi = DocumentKey.parse("5:106-107")
    assert multi is not None and multi.is_multi_page
    assert (multi.batch_id, multi.first_serial, multi.last_serial) == (5, 106, 107)
    assert str(multi) == "5:106-107"


def test_documentkey_from_group_contiguous():
    dk = DocumentKey.from_group(["5:106", "5:107"])
    assert str(dk) == "5:106-107"


def test_documentkey_from_group_rejects_cross_batch():
    import pytest

    with pytest.raises(ValueError):
        DocumentKey.from_group(["5:106", "6:107"])


# --- DocumentIndex ---------------------------------------------------------
def test_document_index_groups_multipage_and_singletons(ingest_dir):
    ocr = data.load_ocr_results(ingest_dir)
    indexed = set(ocr)
    index = data.build_document_index(ingest_dir, indexed, indexed)

    doc_keys = {str(k) for k in index.doc_keys()}
    assert "1:4-5" in doc_keys          # the configured group
    assert "1:1" in doc_keys            # a singleton
    assert "1:4" not in doc_keys        # absorbed into the group

    multi = next(k for k in index.doc_keys() if str(k) == "1:4-5")
    assert index.keys_for_doc(multi) == ["1:4", "1:5"]


def test_document_index_concat_ocr_paginates(ingest_dir):
    ocr_results = data.load_ocr_results(ingest_dir)
    ocr_by_key = {k: v.markdown for k, v in ocr_results.items()}
    indexed = set(ocr_results)
    index = data.build_document_index(ingest_dir, indexed, indexed)

    multi = next(k for k in index.doc_keys() if str(k) == "1:4-5")
    concat = index.concat_ocr(multi, ocr_by_key)
    assert "--- Page 1 ---" in concat and "--- Page 2 ---" in concat
    assert concat.index("--- Page 1 ---") < concat.index("--- Page 2 ---")


def test_document_index_concat_with_boxes_flag(ingest_dir):
    ocr_results = data.load_ocr_results(ingest_dir)
    indexed = set(ocr_results)
    index = data.build_document_index(ingest_dir, indexed, indexed)

    single_with_boxes = next(k for k in index.doc_keys() if str(k) == "1:1")
    text, has_boxes = index.concat_ocr_with_boxes(single_with_boxes, ocr_results)
    assert has_boxes is True
    assert "Grounding Boxes" in text
    assert "[P1-BOX-0]" in text

    multi = next(k for k in index.doc_keys() if str(k) == "1:4-5")
    _text, multi_boxes = index.concat_ocr_with_boxes(multi, ocr_results)
    assert multi_boxes is False


def test_document_index_expand_decisions(ingest_dir):
    ocr = data.load_ocr_results(ingest_dir)
    indexed = set(ocr)
    index = data.build_document_index(ingest_dir, indexed, indexed)
    multi = next(k for k in index.doc_keys() if str(k) == "1:4-5")

    expanded = index.expand_decisions({multi: "verdict"})
    assert expanded == {"1:4": "verdict", "1:5": "verdict"}


# --- Trim ------------------------------------------------------------------
def test_a_trim_that_keeps_the_whole_page_is_no_trim():
    from models import Trim, trim_of

    assert trim_of(0, 1) is None
    assert trim_of(0.2, 1) == Trim(top=0.2, bottom=1.0)
    assert Trim(top=0.123456, bottom=0.9) == Trim(top=0.1235, bottom=0.9)   # a dragged ruler's float noise


def test_a_trim_keeps_some_of_the_page():
    import pytest
    from pydantic import ValidationError

    from models import Trim

    for top, bottom in [(0.5, 0.5), (0.6, 0.4), (0.5, 0.51), (-0.1, 0.5), (0.2, 1.5)]:
        with pytest.raises(ValidationError):
            Trim(top=top, bottom=bottom)


def test_the_thinnest_trim_the_rulers_allow_is_kept():
    """The rulers stop 2% apart; 0.3 - 0.28 is 0.01999... in floating point, and must still pass."""
    from models import MIN_TRIM_BAND, Trim

    for top in (0.28, 0.07, 0.5, 0.0, 0.98):
        Trim(top=top, bottom=round(top + MIN_TRIM_BAND, 4))


def test_a_trim_as_pixel_rows_is_never_empty():
    from models import Trim

    assert Trim(top=0.25, bottom=0.75).rows(100) == (25, 75)
    assert Trim(top=0.0, bottom=0.02).rows(10) == (0, 1)
    assert Trim(top=0.98, bottom=1.0).rows(10) == (9, 10)


def test_a_trim_turns_with_its_file():
    from models import Trim, turned_trim

    band = Trim(top=0.1, bottom=0.6)
    assert turned_trim(band, "") == band
    assert turned_trim(band, "down") == Trim(top=0.4, bottom=0.9)
    assert turned_trim(band, "left") is None and turned_trim(band, "right") is None   # it would lie across
    assert turned_trim(None, "down") is None


def test_a_height_on_the_band_read_is_placed_on_the_band_shown():
    from models import Trim, reframe_y

    whole, top_half, middle = None, Trim(top=0, bottom=0.5), Trim(top=0.25, bottom=0.75)
    assert reframe_y(300, middle, middle) == 300
    assert reframe_y(100, whole, top_half) == 200         # read whole, shown trimmed: the band is stretched
    assert reframe_y(200, top_half, whole) == 100
    assert reframe_y(0, middle, top_half) == 500          # the band's top edge sits halfway down the top half
    assert reframe_y(1000, top_half, middle) == 500


def test_a_batch_without_sliced_sheets_is_saved_as_before(ingest_dir):
    raw = json.loads((ingest_dir / "batches.json").read_text(encoding="utf-8"))
    index = models.load_scan_index(ingest_dir)
    assert json.loads(index.model_dump_json()) == raw          # no empty grids/slices keys appear


def test_a_sliced_toss_is_kept_in_decisions_json_and_others_look_as_before(ingest_dir):
    decisions = data.load_decisions(ingest_dir)
    decisions["1:2"] = models.ReviewDecision(verdict="tossed", toss_reason="sliced", document_type="other",
                                             name="", date="", time="")
    data.save_decisions(ingest_dir, decisions)
    raw = json.loads((ingest_dir / "decisions.json").read_text(encoding="utf-8"))
    assert raw["1:2"]["toss_reason"] == "sliced" and "toss_reason" not in raw["1:1"]
    reread = data.load_decisions(ingest_dir)
    assert reread["1:2"].sliced and not reread["1:6"].sliced   # 1:6 was tossed by hand
