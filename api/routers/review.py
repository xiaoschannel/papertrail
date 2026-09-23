"""Review step endpoints — thin wrappers over review_logic, the mid-ingest files and decisions.json."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

import review_logic as rl
from api import ingest_store as store
from api.deps import get_output_path
from api.guards import no_job_running
from api.schemas import (
    DecisionIn, DecisionOut, DraftIn, FieldBoxOut, FormDefaultsOut, HintOut, HintsRequest,
    HintsResponse, QueueItem, ReviewDocument, ReviewPage, ReviewQueue, ReviewSummary, SmartMatch, VerdictCount,
)
from data import build_document_index, build_smart_match_history, load_decisions, load_trims, save_decisions
from models import (
    VERDICT_COLORS, VERDICT_LABELS, DocumentKey, ReviewDecision, batch_serial_key, iter_indexed_files,
)
from name_similarity import get_smart_match_candidates, quick_apply_label
from settings import get_config, scans_path

router = APIRouter(prefix="/api/review", tags=["review"])


def _summary(extractions: dict, decisions: dict[str, ReviewDecision]) -> ReviewSummary:
    counts = {v: 0 for v in VERDICT_LABELS}
    for decision in decisions.values():
        counts[decision.verdict] = counts.get(decision.verdict, 0) + 1
    return ReviewSummary(
        total=len(extractions),
        pending=len(rl.pending_doc_keys(extractions, decisions)),
        verdicts=[VerdictCount(verdict=v, label=VERDICT_LABELS[v], color=VERDICT_COLORS[v], count=counts[v])
                  for v in VERDICT_LABELS],
    )


def _extraction_or_404(output_path: Path, key: str):
    extraction = store.extractions(output_path).get(key)
    if extraction is None:
        raise HTTPException(status_code=404, detail=f"no extraction for document {key}")
    return extraction


def _no_archive_running():
    """Archive deletes decisions.json when it finishes, so decisions can't change while it runs."""
    return no_job_running("change review decisions", kind="archive")


def _draft(d: DraftIn) -> rl.Draft:
    return rl.Draft(document_type=d.document_type, name=d.name, date=d.date, time=d.time,
                    cost=d.cost, currency=d.currency)


def _decision(body: DecisionIn) -> ReviewDecision:
    """The decision a request records (also what Undo expects to find on file)."""
    draft = body.draft
    receipt = draft.document_type == "receipt"
    return ReviewDecision(
        verdict=body.verdict,
        document_type=draft.document_type,
        name=draft.name,
        date=draft.date,
        time=draft.time,
        cost=(draft.cost or 0.0) if receipt else 0.0,
        currency=draft.currency if receipt else "",
        comment=body.comment,
    )


def _confirmed_names(output_path: Path, extractions: dict, decisions: dict) -> tuple[list, set[str]]:
    history = build_smart_match_history(extractions, decisions, store.smart_match_cache(output_path))
    return history, {row.confirmed for row in history}


@router.get("/queue", response_model=ReviewQueue)
def queue_endpoint(output_path: Path = Depends(get_output_path)):
    """Documents awaiting review, in review order, plus progress counts."""
    empty = ReviewSummary(total=0, pending=0, verdicts=[
        VerdictCount(verdict=v, label=VERDICT_LABELS[v], color=VERDICT_COLORS[v], count=0) for v in VERDICT_LABELS])
    if not (output_path / "batches.json").exists():
        return ReviewQueue(blocker="Run File Index first to create batches.json.", summary=empty, items=[])
    extractions = store.extractions(output_path)
    if not extractions:
        return ReviewQueue(blocker="No extractions yet. Run Parse first.", summary=empty, items=[])
    decisions = load_decisions(output_path)
    items = []
    for key in rl.pending_doc_keys(extractions, decisions):
        defaults = rl.form_defaults(extractions[key])
        items.append(QueueItem(key=key, document_type=defaults.document_type, label=defaults.name))
    return ReviewQueue(blocker=None, summary=_summary(extractions, decisions), items=items)


@router.get("/document", response_model=ReviewDocument)
def document_endpoint(key: str = Query(...), output_path: Path = Depends(get_output_path)):
    """Everything the review form needs for one document."""
    if not (output_path / "batches.json").exists():
        raise HTTPException(status_code=409, detail="Run File Index first to create batches.json.")
    extraction = _extraction_or_404(output_path, key)
    extractions = store.extractions(output_path)
    decisions = load_decisions(output_path)

    indexed = iter_indexed_files(store.scan_index(output_path), include_archived=False)
    key_to_filename = {batch_serial_key(b, s): fn for b, s, fn in indexed}
    index = build_document_index(output_path, set(key_to_filename))
    doc_key = DocumentKey.parse(key) or DocumentKey.from_group([key])
    page_keys = index.keys_for_doc(doc_key)

    ocr = store.ocr_results(output_path)
    ocr_text = index.concat_ocr(doc_key, {k: r.markdown for k, r in ocr.items() if r.succeeded})

    field_sources = getattr(extraction, "field_sources", {}) or {}
    root = get_config().root_path
    input_dir = scans_path(root) if root else None
    trims = load_trims(output_path)
    pages = []
    for page_num, file_key in enumerate(page_keys, start=1):
        filename = key_to_filename.get(file_key)
        result = ocr.get(file_key)
        band = trims.get(file_key)
        boxes = rl.reframed(rl.field_boxes(page_num, result.boxes or [], field_sources), result.trim, band)             if result else []
        pages.append(ReviewPage(
            file_key=file_key,
            filename=filename,
            image_available=bool(input_dir and filename and (input_dir / filename).is_file()),
            boxes=FieldBoxOut.all_of(boxes),
            trim=band,
            retrimmed=bool(result and result.succeeded and result.trim != band),
        ))

    defaults = rl.form_defaults(extraction)
    history, _ = _confirmed_names(output_path, extractions, decisions)
    candidates = get_smart_match_candidates(defaults.name, defaults.phone, history)
    decision = decisions.get(key)
    return ReviewDocument(
        key=key,
        defaults=FormDefaultsOut(**defaults.__dict__),
        initial_name=rl.initial_name(defaults.name, candidates),
        ocr_text=ocr_text,
        pages=pages,
        smart_matches=[SmartMatch(name=c.confirmed_name, name_score=c.name_score, phone_score=c.phone_score,
                                  quick_apply=c.quick_apply, label=quick_apply_label(c)) for c in candidates],
        decision=DecisionOut(**decision.model_dump()) if decision else None,
    )


@router.post("/hints", response_model=HintsResponse)
def hints_endpoint(body: HintsRequest, output_path: Path = Depends(get_output_path)):
    """Hint rules, name status and accept blocker for the form's unsaved values."""
    extraction = _extraction_or_404(output_path, body.key)
    draft = _draft(body.draft)
    _, confirmed = _confirmed_names(output_path, store.extractions(output_path), load_decisions(output_path))
    hints = rl.hints_for(rl.draft_extraction(extraction, draft))
    return HintsResponse(hints=[HintOut(message=h.message, color=h.color) for h in hints],
                         name_status=rl.name_status(draft.name, confirmed),
                         accept_error=rl.accept_error(draft))


def _refuse_a_sliced_sheet(decisions: dict[str, ReviewDecision], key: str) -> None:
    """A sheet cut into crops stays tossed until it is unsliced; nothing in Review may change that."""
    if key in decisions and decisions[key].sliced:
        raise HTTPException(status_code=409,
                            detail=f"{key} is a sliced sheet; unslice it on the Slice page to review it.")


@router.post("/decisions", response_model=ReviewSummary)
def decide_endpoint(body: DecisionIn, output_path: Path = Depends(get_output_path)):
    """Record accept/mark/toss for a document. Accept is validated; mark and toss never are."""
    _extraction_or_404(output_path, body.key)
    if body.verdict == "accepted":
        error = rl.accept_error(_draft(body.draft))
        if error:
            raise HTTPException(status_code=422, detail=error)
    decision = _decision(body)
    with _no_archive_running(), store.decisions_lock:
        decisions = load_decisions(output_path)
        _refuse_a_sliced_sheet(decisions, body.key)
        decisions[body.key] = decision
        save_decisions(output_path, decisions)
    return _summary(store.extractions(output_path), decisions)


@router.post("/undo", response_model=ReviewSummary)
def undo_endpoint(body: DecisionIn, output_path: Path = Depends(get_output_path)):
    """Take back a decision, returning the document to the queue (Undo).

    The body is the decision as it was made, and it is removed only if it is still the one on file.
    A key names a position in its batch, so after a regroup it can name another document; and the same
    document may have been decided again since. Either way the undo is refused rather than guessed at.
    """
    with _no_archive_running(), store.decisions_lock:
        decisions = load_decisions(output_path)
        if body.key not in decisions:
            raise HTTPException(status_code=404, detail=f"Can't undo {body.key}: it no longer has a decision.")
        _refuse_a_sliced_sheet(decisions, body.key)
        if decisions[body.key] != _decision(body):
            raise HTTPException(status_code=409,
                                detail=f"Can't undo {body.key}: it was decided again or regrouped since.")
        del decisions[body.key]
        save_decisions(output_path, decisions)
    return _summary(store.extractions(output_path), decisions)


@router.delete("/decisions", response_model=ReviewSummary)
def clear_endpoint(output_path: Path = Depends(get_output_path)):
    """Clear every review decision (the UI confirms first). Sliced sheets stay tossed: only unslicing a
    sheet takes that toss back."""
    with _no_archive_running(), store.decisions_lock:
        kept = {k: v for k, v in load_decisions(output_path).items() if v.sliced}
        save_decisions(output_path, kept)
    return _summary(store.extractions(output_path), kept)
