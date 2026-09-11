"""Pure parts of extraction: prompt building and the flat->union mapping."""

from extraction import FIELD_SOURCES_ADDENDUM, build_extraction_prompt
from models import CorruptedResult, ExtractionFlat, FieldSourceEntry, OtherResult, ReceiptResult


def test_prompt_embeds_ocr_text():
    prompt = build_extraction_prompt("HELLO OCR")
    assert "HELLO OCR" in prompt
    assert FIELD_SOURCES_ADDENDUM not in prompt


def test_prompt_appends_field_sources_when_boxes():
    prompt = build_extraction_prompt("text", has_boxes=True)
    assert FIELD_SOURCES_ADDENDUM in prompt
    assert prompt.endswith(FIELD_SOURCES_ADDENDUM)


def test_prompt_includes_custom_instruction():
    prompt = build_extraction_prompt("text", custom_instruction="Treat ATM slips specially.")
    assert "Additional instructions:" in prompt
    assert "Treat ATM slips specially." in prompt


def test_prompt_omits_empty_custom_instruction():
    assert "Additional instructions:" not in build_extraction_prompt("text", custom_instruction="   ")


def test_extraction_flat_to_receipt():
    flat = ExtractionFlat(document_type="receipt", language="ja", date="2025-01-10",
                          name="Shop", currency="JPY", cost=100.0,
                          field_sources=[FieldSourceEntry(field="name", boxes=["1:0"])])
    ext = flat.to_extraction()
    assert isinstance(ext, ReceiptResult)
    assert ext.field_sources == {"name": ["1:0"]}


def test_extraction_flat_to_other_and_corrupted():
    other = ExtractionFlat(document_type="other", title="Business Card - X").to_extraction()
    assert isinstance(other, OtherResult) and other.title == "Business Card - X"

    corrupted = ExtractionFlat(document_type="corrupted").to_extraction()
    assert isinstance(corrupted, CorruptedResult)
