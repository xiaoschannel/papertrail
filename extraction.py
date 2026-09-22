import functools
import inspect
import logging
import typing
from collections.abc import Callable

from ollama import chat, generate
from openai import BadRequestError, OpenAI
from pydantic import BaseModel

from models import DocumentExtraction, DocumentExtractionAdapter, ExtractionFlat, TokenUse
from rate_budget import RateBudget

log = logging.getLogger(__name__)

#: What is left of the hosted models' rate limit, as their responses last described it.
budget = RateBudget()

#: Where a caller that wants a call's token counts hands in something to receive them.
UsageSink = Callable[[TokenUse], None] | None

OLLAMA_MODEL = "qwen3:8b"
#: The hosted models on offer, each its own extractor, cheapest first: Luna is what a whole batch runs
#: on, Sol is there for the awkward ones.
OPENAI_MODELS = ("gpt-6-luna", "gpt-6-sol")


class Price(BaseModel):
    """Dollars per million tokens. A prompt the model has already seen bills at ``cached_input``."""

    input: float
    cached_input: float
    output: float


#: What each hosted model charges, from OpenAI's pricing page, checked 2026-09-23. Models that run on
#: this machine are absent: they cost time and electricity, not money.
PRICES = {
    "OpenAI - gpt-6-luna": Price(input=0.10, cached_input=0.01, output=0.50),
    "OpenAI - gpt-6-sol": Price(input=2.00, cached_input=0.20, output=10.00),
}

EXTRACTION_PROMPT = """You are extracting structured data from OCR text of a scanned document.
If the text contains multiple pages (marked with --- Page N ---), treat as one document and extract from all pages.
If a field is not present, corrupted or unreadable, use empty string.

First, determine the document_type:
- "receipt" if this is a receipt or invoice.
- "other" if you can tell this is a document but it's not a receipt.
- "corrupted" if the OCR text is empty, gibberish, or contains large chunks of repeated/garbled text despite the rest is readable.

For corrupted documents, output only: {{"document_type": "corrupted"}}

For non-corrupted documents, first identify the language of the document.
Your remaining extraction should be in that language.
Then output the date and time of the document.
- date: date in yyyy-mm-dd.
- time: time in hh:mm or hh:mm:ss, whichever is present.

Date and time extraction rules:
- If the document's date is in xx-xx-xx format, consider the document's language when interpreting the date, i.e.:
  - English: month-day-year
  - Japanese/Chinese: year-month-day
    - By default, assume Gregorian calendar, i.e. 22-03-15 -> 2022-03-15...
    - unless the input is formatted explicitly in Japanese calendar, i.e. R6-10-02 -> 2024-10-02, 平成27年7月1日 -> 2015-07-01.
- Use the date/time this receipt was printed if there are multiple dates/times.
- e.g. A parking receipt may have a start time and end time.
- Since the receipt would be printed at the end of the parking session, the end date/time should be extracted.

About inferring:
All the ocr output can be unreliable. They can either be
- garbled(bad spelling, repeats, anything that do not make sense), or
- coherently wrong(526 instead of 528, make sense while wrong)

For the first case, sometimes you can recover from other cues.
- e.g. name from address, if name looks garbled
Do not try to recover the second case:
- e.g. total cost from items or vice versa
- or name from address (or vice versa), if they differ, and both make sense
This is because you do not know which one is correct.

If document_type is "receipt", output:
- name: Merchant/Store name. Include branch if present. If the merchant/store is a tenant of a mall, that also tend to be the branch name.
  - Example: "Subway at King Station", "セブン-イレブン 新宿駅前店".
  - Place a space character between the chain and the branch name for languages that do not use a separator.
    - Example: "セブン-イレブン新宿駅前店" -> "セブン-イレブン 新宿駅前店"
- phone: Store or merchant telephone if printed. Normalize to digits with the same separators/spacing style as on the receipt (spaces, hyphens, parentheses, slashes). Do not convert to E.164 or country-code minimal form. Empty string if absent.
- currency: ISO 4217 code (e.g. USD, CNY, JPY). Leave empty if not present.
- address: If the receipt has a detailed address (not part of the branch name), include it here.
  - Example: "123 Main St, Anytown, USA", "東京都新宿区新宿1-2-3"
- items: line items if listed; each with name, total_price, and optionally quantity (may be decimal), unit_price; Empty list [] if no items are listed.
  - If tax is excluded from the price, add an item for the tax. If the tax is included, do not add it.
    - Note: In Japanese, "内" (tax is included), and "外" (tax is excluded) are common shorthands.
  - Sometimes there are reductions to the total price such as buy 2 get 1 dollar off, add an item for that right after the item it applies.
    - Note this should be negative.
  - likewise if there are other charges, such as tips, service fees whatever, add an item for that.
  - Some receipts round the amount, make sure to include that amount as well.
    - For example if the total price is $10.03 rounded to $10, add an item for -0.03 so the total price adds up to the cost.
  - Do not include the actual payment/change amount in this list.
- cost: total amount paid, tax-inclusive.

If document_type is "other", output:
- title: Try formulate the title with two parts using the exact words of the document:
  - What this document is: e.g. "Business Card"
  - Where/whom this document is from: e.g. "John Doe"
    - Do not force this if nothing appears to make sense immediately.
  - In the above case the title should be "Business Card - John Doe"
{field_sources}
{optional_custom}"""

FIELD_SOURCES_ADDENDUM = """
After each page's OCR text there is a "Grounding Boxes" section listing detected regions of that page, each tagged like [P1-BOX-0].
For each field you extract, also output field_sources: a dict mapping field names to the list of box tags (as "page:box" strings) that the field's value came from.
For example: "field_sources": {{"name": ["1:0"], "date": ["1:2"], "cost": ["2:1"]}}
- Use the page number and box index from the tag, e.g. [P1-BOX-3] becomes "1:3".
- A field may cite multiple boxes if its value spans several regions.
- Only cite boxes that directly contain the field's value.
- Only provide sources for: name, title, date, time, cost, address, phone, and items.
- Do NOT provide sources for document_type, language, or currency.

Note the grounding boxes are a separate OCR pass from the original image -- They are variations from the same ground truth.
Both can be unreliable, but they can be used as additional cues for inference-based recovery.
"""


def extraction_messages(ocr_text: str, has_boxes: bool = False,
                        custom_instruction: str = "") -> list[dict[str, str]]:
    """The instructions, then the document: two messages, because a message boundary is what caches.

    A hosted model saves a prefix that ends where a message ends, not wherever two requests happen to
    stop agreeing. Everything that stays the same between documents therefore goes in the first
    message and only the document's own text in the second, so a run pays for the instructions once.
    With boxes and without are two instruction messages, each cached in its own right; editing the
    custom instructions writes a new one, at the cost of a single miss.
    """
    stripped = custom_instruction.strip()
    optional_custom = f"Additional instructions:\n{stripped}\n" if stripped else ""
    instructions = EXTRACTION_PROMPT.format(field_sources=FIELD_SOURCES_ADDENDUM if has_boxes else "",
                                            optional_custom=optional_custom)
    return [{"role": "system", "content": instructions.rstrip()},
            {"role": "user", "content": f"OCR text:\n{ocr_text}"}]


def build_extraction_prompt(ocr_text: str, has_boxes: bool = False, custom_instruction: str = "") -> str:
    """The same prompt as one piece of text, for a model that takes a prompt rather than messages."""
    return "\n\n".join(m["content"] for m in extraction_messages(ocr_text, has_boxes, custom_instruction))


def extract_ollama(ocr_text: str, has_boxes: bool = False, custom_instruction: str = "",
                   on_usage: UsageSink = None) -> DocumentExtraction:
    prompt = build_extraction_prompt(ocr_text, has_boxes, custom_instruction=custom_instruction)
    response = chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        format=DocumentExtractionAdapter.json_schema(),
        options={"temperature": 0.2},
    )
    report(on_usage, OLLAMA_MODEL, TokenUse(prompt=response.prompt_eval_count or 0,
                                            completion=response.eval_count or 0,
                                            raw=response.model_dump(exclude={"message"})))
    return DocumentExtractionAdapter.validate_json(response.message.content)


def extract_openai(ocr_text: str, has_boxes: bool = False, custom_instruction: str = "",
                   model: str = OPENAI_MODELS[0], on_usage: UsageSink = None) -> DocumentExtraction:
    client = OpenAI()
    messages = extraction_messages(ocr_text, has_boxes, custom_instruction=custom_instruction)
    # Reading fields off OCR text is closer to transcription than reasoning, and thinking is billed at the
    # output rate, so ask for little of it -- enough to weigh the custom instructions, not to deliberate.
    # No temperature: these models take their own and refuse any other, and a refusal costs a whole call.
    request = dict(model=model, messages=messages, response_format=ExtractionFlat, reasoning_effort="low")
    try:
        response = _call(client, request)
    except BadRequestError as exc:
        # A model that fixes one of these settings names it: ask again without the ones it named.
        refused = [name for name in ("reasoning_effort",) if name in str(exc)]
        if not refused:
            raise
        response = _call(client, {k: v for k, v in request.items() if k not in refused})
    use = token_use(response)
    budget.spent(use.prompt + use.completion)
    report(on_usage, model, use)
    return response.choices[0].message.parsed.to_extraction()


def _call(client: OpenAI, request: dict):
    """One call, taking the rate limits off the response on the way past: they are only told to us here."""
    raw = client.chat.completions.with_raw_response.parse(**request)
    budget.observe(raw.headers)
    return raw.parse()


def token_use(response) -> TokenUse:
    """An OpenAI response's usage: the numbers pricing needs, and the payload it came in."""
    usage = response.usage
    if usage is None:
        return TokenUse()
    raw = {field: getattr(response, field, None) for field in ("model", "service_tier", "system_fingerprint")}
    raw["usage"] = usage.model_dump()
    return TokenUse(prompt=usage.prompt_tokens, completion=usage.completion_tokens,
                    cached=getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0,
                    thinking=getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0,
                    raw=raw)


def report(on_usage: UsageSink, model: str, use: TokenUse) -> None:
    """Log what the call consumed, and hand it to a caller that asked (the Experiment bench does)."""
    log.info("%s: %d prompt tokens (%d cached), %d completion (%d thinking)",
             model, use.prompt, use.cached, use.completion, use.thinking)
    if on_usage is not None:
        on_usage(use)


def cost_of(extractor: str, use: TokenUse) -> float | None:
    """What a call costs in dollars, or None for a model that runs on this machine and bills nothing."""
    price = PRICES.get(extractor)
    if price is None:
        return None
    return ((use.prompt - use.cached) * price.input + use.cached * price.cached_input
            + use.completion * price.output) / 1_000_000


def call_extractor(extract: typing.Callable[..., DocumentExtraction], ocr_text: str, has_boxes: bool,
                   custom_instruction: str, on_usage: UsageSink = None) -> DocumentExtraction:
    """Run an extractor, asking for its token counts only if it is one that can report them.

    A local model reports none, and a stand-in extractor may take the three plain arguments and no more.
    """
    extra = {}
    if on_usage is not None and "on_usage" in inspect.signature(extract).parameters:
        extra["on_usage"] = on_usage
    return extract(ocr_text, has_boxes=has_boxes, custom_instruction=custom_instruction, **extra)


def unload_ollama() -> None:
    """Free the Ollama extraction model's memory now instead of after Ollama's idle timeout."""
    generate(model=OLLAMA_MODEL, prompt="", keep_alive=0)


EXTRACTORS: dict[str, typing.Callable[..., DocumentExtraction]] = {
    **{f"OpenAI - {model}": functools.partial(extract_openai, model=model) for model in OPENAI_MODELS},
    f"Ollama - {OLLAMA_MODEL}": extract_ollama,
}
