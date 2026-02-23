from ollama import chat
from openai import OpenAI

from models import DocumentExtractionAdapter, ExtractionFlat, DocumentExtraction

OLLAMA_MODEL = "qwen3:8b"
OPENAI_MODEL = "gpt-4.1"

EXTRACTION_PROMPT = """You are extracting structured data from OCR text of a scanned document.
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

If document_type is "receipt", output:
- name: Merchant/Store name. Include branch if present. If the merchant/store is a tenant of a mall, that also tend to be the branch name.
  - Example: "Subway at King Station", "セブン-イレブン 新宿駅前店".
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
  - Do not include the actual payment/change amount in this list
- cost: total amount paid, tax-inclusive.

If document_type is "other", output:
- title: Try formulate the title with two parts using the exact words of the document:
  - What this document is: e.g. "Business Card"
  - Where/whom this document is from: e.g. "John Doe"
    - Do not force this if nothing appears to make sense immediately.
  - In the above case the title should be "Business Card - John Doe"

OCR text:
{ocr_text}"""


def extract_ollama(ocr_text: str) -> DocumentExtraction:
    prompt = EXTRACTION_PROMPT.format(ocr_text=ocr_text)
    response = chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        format=DocumentExtractionAdapter.json_schema(),
        options={"temperature": 0.2},
    )
    return DocumentExtractionAdapter.validate_json(response.message.content)


def extract_openai(ocr_text: str) -> DocumentExtraction:
    client = OpenAI()
    prompt = EXTRACTION_PROMPT.format(ocr_text=ocr_text)
    response = client.chat.completions.parse(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format=ExtractionFlat,
        temperature=0.2,
    )
    return response.choices[0].message.parsed.to_extraction()


EXTRACTORS = {
    f"OpenAI - {OPENAI_MODEL}": extract_openai,
    f"Ollama - {OLLAMA_MODEL}": extract_ollama,
}
