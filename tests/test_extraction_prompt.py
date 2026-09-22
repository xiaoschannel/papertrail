"""Pure parts of extraction: prompt building and the flat->union mapping."""

import pytest

import extraction
from extraction import FIELD_SOURCES_ADDENDUM, build_extraction_prompt, extraction_messages
from models import CorruptedResult, ExtractionFlat, FieldSourceEntry, OtherResult, ReceiptResult, TokenUse


def test_prompt_embeds_ocr_text():
    prompt = build_extraction_prompt("HELLO OCR")
    assert "HELLO OCR" in prompt
    assert FIELD_SOURCES_ADDENDUM not in prompt


def test_prompt_appends_field_sources_when_boxes():
    prompt = build_extraction_prompt("OCR HERE", has_boxes=True)
    assert FIELD_SOURCES_ADDENDUM in prompt
    assert prompt.endswith("OCR HERE")


def test_everything_that_stays_the_same_between_documents_is_its_own_message():
    # What a hosted model can cache ends where a message ends, so the document's text is a message of
    # its own and everything that repeats between documents sits in the one before it.
    instructions, document = extraction_messages("OCR HERE", has_boxes=True,
                                                 custom_instruction="Treat ATM slips specially.")

    assert (instructions["role"], document["role"]) == ("system", "user")
    assert FIELD_SOURCES_ADDENDUM.strip() in instructions["content"]
    assert "Treat ATM slips specially." in instructions["content"]
    assert "OCR HERE" not in instructions["content"]
    assert document["content"] == "OCR text:\nOCR HERE"


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


#: The rate limits a response carries, as the provider writes them.
HEADERS = {"x-ratelimit-limit-requests": "500", "x-ratelimit-remaining-requests": "499",
           "x-ratelimit-reset-requests": "120ms", "x-ratelimit-limit-tokens": "200000",
           "x-ratelimit-remaining-tokens": "198000", "x-ratelimit-reset-tokens": "1.5s"}

#: What the SDK's usage object dumps to, near enough for a test that only passes it through.
USAGE_PAYLOAD = {"prompt_tokens": 2000, "completion_tokens": 400, "total_tokens": 2400,
                 "prompt_tokens_details": {"cached_tokens": 1024, "audio_tokens": 0},
                 "completion_tokens_details": {"reasoning_tokens": 250, "audio_tokens": 0}}


def _fake_openai(monkeypatch, refuses=("reasoning_effort",)):
    """Stand in for the OpenAI client, refusing the named settings the way a model that fixes them does."""
    import httpx
    from openai import BadRequestError

    calls = []

    class Completions:
        """The client as the SDK shapes it: the raw call carries the headers, the parsed one the result."""

        @property
        def with_raw_response(self):
            outer = self

            class Raw:
                def parse(self, **request):
                    parsed = outer.parse(**request)
                    return type("Raw", (), {"headers": HEADERS, "parse": lambda self=None: parsed})()
            return Raw()

        def parse(self, **request):
            calls.append(request)
            named = [name for name in refuses if name in request]
            if named:
                raise BadRequestError(f"Unsupported value: '{named[0]}' does not support that value", body=None,
                                      response=httpx.Response(400, request=httpx.Request("POST", "https://x")))
            message = type("Message", (), {"parsed": ExtractionFlat(document_type="corrupted")})
            usage = type("Usage", (), {
                "prompt_tokens": 2000, "completion_tokens": 400,
                "prompt_tokens_details": type("P", (), {"cached_tokens": 1024}),
                "completion_tokens_details": type("C", (), {"reasoning_tokens": 250}),
                "model_dump": lambda self=None: USAGE_PAYLOAD})
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})], "usage": usage,
                                         "model": "gpt-6-sol-2026-09-01", "service_tier": "default",
                                         "system_fingerprint": "fp_test"})

    monkeypatch.setattr(extraction, "OpenAI", type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})}))
    return calls


def test_the_hosted_models_are_asked_to_think_only_a_little_and_for_nothing_they_refuse(monkeypatch):
    calls = _fake_openai(monkeypatch, refuses=())

    extraction.EXTRACTORS["OpenAI - gpt-6-sol"]("text")

    # No temperature: a reasoning model takes only its own, and asking costs a whole call to be told so.
    assert [c["reasoning_effort"] for c in calls] == ["low"]
    assert "temperature" not in calls[0]


def test_a_call_reports_what_it_read_and_wrote_to_a_caller_that_asks(monkeypatch):
    _fake_openai(monkeypatch, refuses=())
    seen = []

    extraction.EXTRACTORS["OpenAI - gpt-6-sol"]("text", on_usage=seen.append)

    assert seen[0].model_dump(exclude={"raw"}) == {"prompt": 2000, "cached": 1024, "completion": 400, "thinking": 250}
    # and the provider's own payload, untouched, for when the summary above is the thing in doubt
    assert seen[0].raw == {"model": "gpt-6-sol-2026-09-01", "service_tier": "default",
                           "system_fingerprint": "fp_test", "usage": USAGE_PAYLOAD}
    # The cached part of the prompt bills at a tenth, and the thinking bills as output.
    assert extraction.cost_of("OpenAI - gpt-6-sol", seen[0]) == pytest.approx(0.0061568)
    assert extraction.cost_of("Ollama - qwen3:8b", seen[0]) is None      # it runs here; it bills nothing


def test_each_hosted_model_is_its_own_extractor_and_one_refusing_a_setting_is_asked_without(monkeypatch):
    assert [name for name in extraction.EXTRACTORS if name.startswith("OpenAI")] == [
        "OpenAI - gpt-6-luna", "OpenAI - gpt-6-sol"]     # cheapest first: it is the default
    calls = _fake_openai(monkeypatch)                          # a model that fixes its reasoning effort

    result = extraction.EXTRACTORS["OpenAI - gpt-6-luna"]("text")

    assert result.document_type == "corrupted"
    assert [(c["model"], "reasoning_effort" in c) for c in calls] == [("gpt-6-luna", True),
                                                                      ("gpt-6-luna", False)]
    assert calls[1]["response_format"] is ExtractionFlat       # only the setting it refused is dropped
