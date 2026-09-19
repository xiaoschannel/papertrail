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


def test_each_hosted_model_is_its_own_extractor_and_one_refusing_a_temperature_is_asked_without(monkeypatch):
    import httpx
    from openai import BadRequestError

    import extraction
    from models import ExtractionFlat

    assert [name for name in extraction.EXTRACTORS if name.startswith("OpenAI")] == [
        "OpenAI - gpt-5.4", "OpenAI - gpt-5.6-terra", "OpenAI - gpt-5.6-luna"]
    calls = []

    class Completions:
        def parse(self, **request):
            calls.append(request)
            if "temperature" in request:
                raise BadRequestError("Unsupported value: 'temperature' does not support 0.2", body=None,
                                      response=httpx.Response(400, request=httpx.Request("POST", "https://x")))
            message = type("Message", (), {"parsed": ExtractionFlat(document_type="corrupted")})
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})]})

    class Client:
        chat = type("Chat", (), {"completions": Completions()})

    monkeypatch.setattr(extraction, "OpenAI", Client)
    result = extraction.EXTRACTORS["OpenAI - gpt-5.6-luna"]("text")

    assert result.document_type == "corrupted"
    assert [(c["model"], "temperature" in c) for c in calls] == [("gpt-5.6-luna", True), ("gpt-5.6-luna", False)]
