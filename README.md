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
cd frontend
npm install
cd ..
```
---
Fix Rotation checks for scans that are sideways or upside down with a small model (PaddleClas's text image
orientation classifier, 6.8 MB, run on the CPU). It is downloaded the first time that page checks a batch,
and kept in `~/.cache/papertrail`.

---
The hosted extractors need an API key. Copy `.env.example` to `.env` and put yours in it; the file is
gitignored, and `env.py` says which one is read when you have several checkouts.

---
Everything lives in one **Papertrail folder**, set on the Config page: the scanner drops images into its
`scans\` subfolder and the app files them into `archive\`. The folder's root is a git repository holding
the history of both (see [The folder's history](#the-folders-history)), so `git` has to be installed and
on `PATH`.

Run the API and the web app, each in its own terminal:
```
.venv\Scripts\python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
npm --prefix frontend run dev
```
Then open http://127.0.0.1:5173. Both listen on this machine only.

To try things without touching your archive, `tools/sandbox_server.py` runs the API on a throwaway archive of
invented scans with fake models (port 8001); `npm --prefix frontend run dev:sandbox` serves the web app for it
on port 5174. A new sandbox starts with something on every page: a batch already filed (some of it marked,
for the Marked Workshop), a batch waiting in Review, and a batch indexed and waiting for Fix Rotation. A
restart keeps what you did in it. To start over, stop the sandbox API and run
```
.venv\Scripts\python tools\sandbox_reset.py
```
then start it again (or start it with `--fresh`, which does the same). `tools/sandbox_seed.py` builds that
state, and says what is in it for which feature.

## Several checkouts at once

Each git worktree runs its own sandbox beside the main checkout's, on ports of its own. Set a new worktree up
once, with the main checkout's Python:
```
<main checkout>\.venv\Scripts\python tools\worktree_setup.py
cd frontend
npm install
cd ..
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

Set it up once per machine, after the venv and the frontend's `npm install` above:
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
2. **Fix Rotation** — Turn scans fed in sideways or upside down upright, and level the ones fed in slightly
   crooked, before anything is cut or read from them. The page points out the scans that look so, and shows
   each one turned to confirm before it is saved; any scan can be straightened from its full-size view,
   previewed over level guides, and its OCR boxes move with it.
3. **Slice** — Cut a sheet of receipts too small to scan alone (tape them onto a sheet in a grid) into one page
   per receipt. The sheet is kept, tossed. Fix its rotation first: the crops are cut the way it faces.
4. **Group** — Link pages that belong to one document, and toss pages that don't belong in the archive. From
   a page's full-size view, trim it to the part OCR should read.
5. **OCR** — Batch OCR across all scanned images.
6. **Parse** — Parse OCR results into file metadata.
7. **Review** — Review parsed metadata and manually correct if needed. Mark bad documents for re-processing.
8. **Archive** — Organize files into date-based folders, commit them to the folder's history, and clear the
   filed scans out of the scan folder.

### The folder's history

The Papertrail folder's root is a git repository (made at the first milestone), so every scan as it came in
and as it was turned or cut, and every page and sidecar as filed and as edited since, is kept in every state
it was ever in. Nothing in the app reads the history: `git log`, `git show` and `git checkout` in the folder
do that. A scan and its archived copy are the same bytes, so git stores each image once.

A commit is made only at a **milestone**, and holds only what that milestone produced:

| Milestone | What the commit holds |
| --- | --- |
| File Index confirm | the index and the new batch's scans |
| Slice apply, Group save | the index, the crops, and the working files the step rewrote |
| OCR or Parse ending, however it ended | that run's result files |
| Archive | the filed pages and the working files it deleted; then the scans it removed |
| The Commit button in the sidebar | everything uncommitted, with a message |
| The API stopping | everything uncommitted, as a parking commit the next start undoes |

Nothing else commits. Review and Workshop decisions, Receipt Detail edits, Normalize, Dedupe and rotation
fixes accumulate, and the sidebar shows how many files wait, until a milestone that owns them or the
Commit button.

A scan leaves the scan folder only once its filed page is in the last commit as it is on disk. Archive
removes the scans it just filed, and any filed earlier that are still there; a page edited since keeps its
scan until the edit is committed, and a scan a batch still being ingested needs stays. An archive from
before the history gets every page filed so far committed by the first Archive run, which then clears the
backlog of scans out. If git is missing, Archive stops after filing and says so; the next run commits and
clears out.

The history lives in the folder's `.git`, which roughly doubles the folder's size (images don't compress).
Keep it on the same backup as the folder: it protects against the app and against mistakes, not against
the disk.

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
- **Config** — Set the Papertrail folder, the models, and toggle structured OCR.
