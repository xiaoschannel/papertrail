import typing

from ollama import chat
from openai import OpenAI

from models import DocumentExtraction, DocumentExtractionAdapter, ExtractionFlat

OLLAMA_MODEL = "qwen3:8b"
OPENAI_MODEL = "gpt-5.4"

EXTRACTION_PROMPT = """You are extracting structured data from OCR text of a scanned document.
If the text contains multiple pages (marked with --- Page N ---), treat as one document and extract from all pages.
If a field is not present, corrupted or unreadable, use empty string.

First, determine the document_type:
- "receipt" if this is a receipt or invoice.
- "other" if you can tell this is a document but it's not a receipt.
- "corrupted" if the OCR text is empty or gibberish.

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
- e.g. name from location, if name looks garbled
Do not try to recover the second case:
- e.g. total cost from items or vice versa
- or name from location(or vice versa), if they differ, and both make sense
This is because you do not know which one is correct.

If document_type is "receipt", output:
- name: Merchant/Store name. Include branch if present. If the merchant/store is a tenant of a mall, that also tend to be the branch name.
  - Example: "Subway at King Station", "セブン-イレブン 新宿駅前店".
  - Place a space character between the chain and the branch name for languages that do not use a separator.
    - Example: "セブン-イレブン新宿駅前店" -> "セブン-イレブン 新宿駅前店"
- currency: ISO 4217 code (e.g. USD, CNY, JPY). Leave empty if not present.
- location: If the receipt has a detailed address (not part of the branch name), include it here.
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

OCR text:
{ocr_text}"""

FIELD_SOURCES_ADDENDUM = """

After each page's OCR text there is a "Grounding Boxes" section listing detected regions of that page, each tagged like [P1-BOX-0].
For each field you extract, also output field_sources: a dict mapping field names to the list of box tags (as "page:box" strings) that the field's value came from.
For example: "field_sources": {{"name": ["1:0"], "date": ["1:2"], "cost": ["2:1"]}}
- Use the page number and box index from the tag, e.g. [P1-BOX-3] becomes "1:3".
- A field may cite multiple boxes if its value spans several regions.
- Only cite boxes that directly contain the field's value.
- Only provide sources for: name, title, date, time, cost, location, and items.
- Do NOT provide sources for document_type, language, or currency.

Note the grounding boxes are a separate OCR pass from the original image -- They are variations from the same ground truth.
Both can be unreliable, but they can be used as additional cues for inference-based recovery.
"""


def build_extraction_prompt(ocr_text: str, has_boxes: bool = False) -> str:
    base = EXTRACTION_PROMPT.format(ocr_text=ocr_text)
    return base + FIELD_SOURCES_ADDENDUM if has_boxes else base


def extract_ollama(ocr_text: str, has_boxes: bool = False) -> DocumentExtraction:
    prompt = build_extraction_prompt(ocr_text, has_boxes)
    response = chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        format=DocumentExtractionAdapter.json_schema(),
        options={"temperature": 0.2},
    )
    return DocumentExtractionAdapter.validate_json(response.message.content)


def extract_openai(ocr_text: str, has_boxes: bool = False) -> DocumentExtraction:
    client = OpenAI()
    prompt = build_extraction_prompt(ocr_text, has_boxes)
    response = client.chat.completions.parse(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format=ExtractionFlat,
        temperature=0.2,
    )
    return response.choices[0].message.parsed.to_extraction()


EXTRACTORS: dict[str, typing.Callable[..., DocumentExtraction]] = {
    f"OpenAI - {OPENAI_MODEL}": extract_openai,
    f"Ollama - {OLLAMA_MODEL}": extract_ollama,
}
