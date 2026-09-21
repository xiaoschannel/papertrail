# Papertrail

> _Ex vestigiis veritas_  
> Truth from traces.

<img src="sample-annotated-receipt.jpg" alt="Sample annotated receipt" width="360" />

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
The web app also needs [Node.js](https://nodejs.org/) 22 or newer:
```
npm --prefix frontend install
```
---
The hosted extractors need an API key. Copy `.env.example` to `.env` and put yours in it; the file is
gitignored, and `env.py` says which one is read when you have several checkouts.

Run the API and the web app, each in its own terminal:
```
.venv\Scripts\python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev
```
Then open http://127.0.0.1:5173. Both listen on this machine only.

To try things without touching your archive, `tools/sandbox_server.py` runs the API on a throwaway archive of
invented scans with fake models (port 8001); `npm --prefix frontend run dev:sandbox` serves the web app for it
on port 5174.

## Several checkouts at once

Each git worktree runs its own sandbox beside the main checkout's, on ports of its own. Set a new worktree up
once, with the main checkout's Python:
```
<main checkout>\.venv\Scripts\python tools\worktree_setup.py
npm --prefix frontend install
```
It claims the worktree a numbered slot — slot *n* serves its sandbox on API `8001+n` and web `5174+n` — and
prints the URL. The slot is kept for as long as the worktree exists, so the ports stay put across restarts;
deleting the worktree frees it. The main checkout's ports never change.

A worktree runs the **sandbox only**. The live archive belongs to the main checkout: two API processes on it,
running different code, is how it gets hurt, so `npm run dev` in a worktree refuses. A worktree that hasn't
been set up refuses to start its sandbox too, rather than landing on the main checkout's ports.

The setup also writes `.claude/launch.json`, the dev servers Claude Code starts. A worktree's are named for
its slot (`sandbox-api-1`, `sandbox-web-1`), so starting one never stands in for another checkout's server of
the same name. How slots are chosen is in `dev_ports.py`.

## Deploying live

The live app — the main checkout's API on 8000 and web app on 5173 — runs as the Windows scheduled task
`papertrail-live` rather than in a terminal, so it stays up however terminals and editors come and go.

Set it up once per machine, after the venv and `npm --prefix frontend install` above:
```
.venv\Scripts\python tools\deploy.py install        register the task
.venv\Scripts\python tools\deploy.py autostart on   start live whenever you log in (off by default)
.venv\Scripts\python tools\deploy.py                start it now
```
`autostart off` undoes the second one; without it, a reboot leaves live down until someone starts it. The
commands work from any checkout, and always act on the main checkout.

After a PR merges, that same `tools\deploy.py` puts the main checkout on the latest `main`, installs what
changed in `requirements.txt` or the frontend's packages, restarts live, and waits until the web app answers
through its API proxy.

It won't restart while a job (OCR, Parse, Archive) is running on live, since that would kill it — it names
the job and stops; `--even-with-job` overrides. And it leaves the main checkout alone if it holds work:
uncommitted changes to tracked files, a branch not merged into `main`, or commits `origin` doesn't have.
Either way nothing is stopped, so a refused deploy costs no uptime.

`tools\deploy.py status` shows what live runs, whether it starts at logon, and how far behind it is;
`restart` restarts it as it is, and `stop` takes it down until someone starts it again — killing the servers
by hand instead leaves the task believing it still runs them. Live's output goes to `.live\api.log` and
`.live\web.log` in the main checkout.

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
