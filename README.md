# Papertrail

A personal document archival tool for digitizing receipts, tickets, and other timed documents into a structured timeline. Battle-tested with over 1000 documents in Japanese, Chinese and English, on a GeForce RTX 4070 Super(12GB VRAM).

# Design Decisions
This is more of an AI(both LLM and classic ML)-assisted manual workflow due to these real-world problems I encountered:
- Most OCR models are abysmal at half-width katakana. I tried to identify the boxes then stretching them, but half-width dakutens often still break them. (In case you want to give it a shot, I found 2.5x-4x width to be the sweet spot for those Cardnet slips) Apparently CLOVA can handle it, but I had to give up due to time constraints.
- Japanese calendars are sometimes used undeclared(looking at you, ゆうちょ銀行) and there are absolutely no way to differentiate between Heisei 25(2013) and 2025 in YY-MM-DD format (nor Reiwa 6 vs 2006), unless you are willing to constrain the date range or build some sort of system that allow rules for each merchant.

The repo has multi-provider setup for flexibility. In my testing, Deepseek OCR 2 and gpt4.1 for batch processing, and for receipts that Deepseek OCR 2 failed, GLM-OCR can often get it right. Qwen3-8b is usable if you want to process everything fully locally, but gpt4.1's better accuracy really cuts down the manual re-editing.

I also experimented with Datalab's Chandra, but it was too slow to host locally even at int4. The accuracy was very good, but it also do not handle half-width katakana well, so the subscription felt too expensive to justify.

# Setup

Install the virtual environment and dependencies:
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-deepseek.txt
```
Then follow the below guide to [install flash-attention on Windows](flash_attn.md).

---
If you don't want to use Deepseek OCR 2:
```
pip install -r requirements.txt
```
---
Run
```
streamlit run app.py
```

# Workflow
Scan your documents into a folder, and follow this process:

## Ingest
1. **File Index** — Ingest new batches of scanned files.
2. **OCR** — Batch OCR across all scanned images.
3. **Parse** — Parse OCR results into file metadata.
4. **Review** — Review parsed metadata and manually correct if needed. Mark bad documents for re-processing.
5. **Archive** — Organize files into date-based folders and clean up.

## Curate
1. **Marked Workshop** — Reprocess marked files with image enhancement, and contextual aids.
2. **Dedupe** — Time-based duplicate detection for documents with matching timestamps and costs.
3. **Normalize** — Unify similar merchant/document names.

## Visualize
(To be implemented)

## Dev
- **OCR Experiment** — Interactive single-image OCR testing with image enhancement.
- **Sanity Check** — Validate batch coverage and archive metadata integrity.