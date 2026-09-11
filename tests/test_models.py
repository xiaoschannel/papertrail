"""Keys and document grouping — the backbone identifiers the whole app uses."""

import data
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
