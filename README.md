# Papertrail

> _Ex vestigiis veritas_  
> Truth from traces.

A personal document archival tool for digitizing receipts, tickets, and other timed documents into a structured timeline. Tested with over 3000 real documents in Japanese, Chinese and English.

This project is also built to test the "fast fashion era of SaaS from AI coding" idea and practice AI-assisted coding against a messy, real-world problem.

Design decisions are documented in [design_decisions.md](design_decisions.md).

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
1. **Dashboard** — Monthly spending timeline, document volume, top merchants.
2. **Merchant Profile** — Per-merchant stats, spending trend, receipt gallery, visit cadence.
3. **Receipt Detail** — Single-document view with metadata, line items, raw OCR, and in-place editing.
4. **Time Capsule** — "On this day" across past years.
5. **Calendar** — Week and month calendar view of archived documents by date.

## Dev
- **Experiment** — Interactive single-image OCR and extraction testing with image enhancement.
- **Sanity Check** — Validate batch coverage and archive metadata integrity.

## Config
- **Config** — Set input/output paths and toggle structured OCR.
