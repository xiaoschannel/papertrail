"""Curate: likely duplicates in the archive, and merging merchant names that mean one shop."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

import document_files
import name_merge
import workshop
from api import cache
from api.deps import get_output_path
from api.jobs import Claim
from api.guards import no_job_running, one_edit_at_a_time
from api.model_manager import models
from api.routers.brands import prefix_suggestions
from api.schemas import (
    CurateCountsOut, DedupeCluster, DedupeMember, DedupeOut, DistinctIn, DistinctOut, KeepIn, KeptPair, MergeIn,
    MergeOut, MoveOut, NameCount, NameGroupOut, NormalizeEngineOut, NormalizeOut, RestoreIn, TossIn, TossOut,
)
from data import (
    load_distinct_pairs, load_kept_duplicates, read_sidecar, save_kept_duplicates,
)
from dedupe_candidates import drop_confirmed_different, find_dedupe_clusters
from models import ReviewDecision
from name_merge import NameGroup
from name_similarity import EMBED_MODEL, unload_embeddings
from normalize_engines import ENGINES
from settings import get_config, update_config

router = APIRouter(prefix="/api/curate", tags=["curate"])


# --- The sidebar's counts ----------------------------------------------------------------------
def _mtime(path: Path) -> int:
    return path.stat().st_mtime_ns if path.exists() else 0


@router.get("/counts", response_model=CurateCountsOut)
def curate_counts(
    brands_min_names: int | None = Query(None, ge=1, le=50),
    prefix_boundary_only: bool | None = None,
    prefix_max_length: int | None = Query(None, ge=4, le=80),
    prefix_min_length: int | None = Query(None, ge=1, le=24),
    normalize_engine: str | None = None,
    normalize_threshold: float | None = Query(None, gt=0),
    output_path: Path = Depends(get_output_path),
):
    """What waits on each curate page. Each count is what its page would list, taken from the same
    cached archive frames and kept until what it depends on changes (``cache.sidebar_count``), so asking
    on every page change costs a few directory stats. Brand registry and Normalize count at their own
    sidebar settings; the parameters stand in for them unsaved, so Config can show what a setting would
    count before it is saved. Nothing here waits on a job or asks a model: an embedding count leaves out
    the names not embedded yet."""
    cfg = get_config()
    if normalize_engine is not None and normalize_engine not in ENGINES:
        raise HTTPException(status_code=422, detail=f"unknown engine: {normalize_engine}")
    min_names = brands_min_names or cfg.sidebar_brands_min_names
    marked = cache.sidebar_count(output_path, "workshop", (),
                                 lambda: len(workshop.marked_documents(output_path)))
    dedupe = cache.sidebar_count(output_path, "dedupe", (_mtime(output_path / "not_duplicates.json"),),
                                 lambda: len(dedupe_clusters(output_path=output_path).clusters))
    suggestion_settings = dict(
        boundary_only=cfg.prefix_suggestion_boundary_only if prefix_boundary_only is None else prefix_boundary_only,
        max_length=prefix_max_length or cfg.prefix_suggestion_max_length,
        min_length=prefix_min_length or cfg.prefix_suggestion_min_length,
        min_count=min_names)
    brands = cache.sidebar_count(output_path, "brands", tuple(suggestion_settings.values()),
                                 lambda: len(prefix_suggestions(output_path, **suggestion_settings)))

    engine_id = normalize_engine or (cfg.sidebar_normalize_engine if cfg.sidebar_normalize_engine in ENGINES
                                     else "string")
    embedding = engine_id == "embedding"
    threshold = float(normalize_threshold or (cfg.sidebar_normalize_embedding_threshold if embedding
                                              else cfg.sidebar_normalize_string_similarity))

    def name_groups() -> tuple[int, int]:
        by_name, canonical = name_merge.names_in_use(output_path, cache.archive_state(output_path))
        names = sorted(set(by_name) | canonical)
        if len(names) < 2:
            return 0, 0
        clusters, missing = ENGINES[engine_id].run_offline(
            output_path, names, threshold if embedding else 1.0 - threshold / 100.0)
        groups = name_merge.visible_groups(clusters, by_name, canonical, load_distinct_pairs(output_path))
        return len(groups), len(missing)

    normalize, unembedded = cache.sidebar_count(
        output_path, "normalize",
        (engine_id, threshold, *(_mtime(output_path / f) for f in
                                 ("distinct_pairs.json", "name_normalizations.json", "name_embeddings.npz"))),
        name_groups)
    return CurateCountsOut(workshop=marked, dedupe=dedupe, brands=brands,
                           brands_min_names=min_names, normalize=normalize,
                           normalize_engine=engine_id, normalize_threshold=threshold,
                           normalize_unembedded=unembedded)


@router.get("/dedupe", response_model=DedupeOut)
def dedupe_clusters(output_path: Path = Depends(get_output_path)):
    """Archived documents that look like the same purchase scanned twice: equal cost, minutes apart.

    Clustered per DOCUMENT, not per file: the pages of one multi-page receipt share a date, time and
    cost, so clustering files would report every multi-page document as its own duplicate.
    """
    # Both from the cache: reading the archive again here would re-parse every sidecar on each request.
    tossed, _accepted = cache.archive_state(output_path)
    records = cache.viz_records(output_path)
    if records.empty:
        return DedupeOut(archived=0, tossed=len(tossed), clusters=[])

    reviews: dict[str, ReviewDecision] = {}
    rows: dict[str, dict] = {}
    for row in records.to_dict("records"):
        reviews[row["filename"]] = ReviewDecision(
            verdict="accepted", document_type=row["document_type"], name=row["name"], date=row["date"],
            time=row["time"], cost=float(row["cost"]), currency=row["currency"], comment="")
        rows[row["filename"]] = row

    kept = load_kept_duplicates(output_path)
    clusters = []
    for group in find_dedupe_clusters(reviews):
        # A cluster you have already judged ("not duplicates") stops being offered.
        members = [
            DedupeMember(filename=key, path=rows[key]["path"], trim=rows[key]["trim"], name=rows[key]["name"],
                         date=rows[key]["date"], time=rows[key]["time"], cost=float(rows[key]["cost"]),
                         currency=rows[key]["currency"], pages=len(rows[key]["paths"]))
            for key in drop_confirmed_different(group, kept) if key in rows
        ]
        if len(members) >= 2:
            clusters.append(DedupeCluster(date=members[0].date, time=members[0].time, members=members))
    return DedupeOut(archived=len(records), tossed=len(tossed), clusters=clusters,
                     kept=[KeptPair(documents=sorted(pair),
                                    names=[str(rows[key]["name"]) for key in sorted(pair) if key in rows])
                           for pair in sorted(sorted(pair) for pair in kept)])


@router.post("/dedupe/keep", response_model=DedupeOut)
@one_edit_at_a_time
def keep_both(body: KeepIn, output_path: Path = Depends(get_output_path)):
    """Say these documents are different purchases, so the cluster stops coming back."""
    pairs = load_kept_duplicates(output_path)
    for first, second in combinations(dict.fromkeys(body.documents), 2):
        pairs.add(frozenset({first, second}))
    save_kept_duplicates(output_path, pairs)
    return dedupe_clusters(output_path=output_path)


@router.delete("/dedupe/keep", response_model=DedupeOut)
@one_edit_at_a_time
def consider_again(first: str, second: str, output_path: Path = Depends(get_output_path)):
    """Undo that, so the pair is offered again."""
    pairs = load_kept_duplicates(output_path)
    pairs.discard(frozenset({first, second}))
    save_kept_duplicates(output_path, pairs)
    return dedupe_clusters(output_path=output_path)


@router.post("/dedupe/toss", response_model=TossOut)
def toss_duplicate(body: TossIn, output_path: Path = Depends(get_output_path)):
    """Move one archived document's pages into ``tossed/``. The scans stay on disk, out of the archive."""
    records = cache.viz_records(output_path)
    match = records[records["path"] == body.path] if not records.empty else records
    if match.empty:
        raise HTTPException(status_code=404, detail="no archived document at that path")
    row = match.iloc[0]
    pages = [output_path / relative for relative in row["paths"]]
    previous = read_sidecar(pages[0])

    try:
        with no_job_running("toss a document", kind="archive"):
            tossed = document_files.toss_document(output_path, pages)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    cache.clear()  # it left the archive: every visualize page is stale
    return TossOut(tossed=tossed, previous_verdict=previous.review.verdict if previous else "accepted",
                   name=str(row["name"]))


@router.post("/dedupe/restore", response_model=DedupeOut)
def restore_tossed(body: RestoreIn, output_path: Path = Depends(get_output_path)):
    """Undo a toss: put the pages back in the archive under the verdict they had."""
    tossed_folder = (output_path / document_files.TOSSED).resolve()
    pages = [(output_path / relative).resolve() for relative in body.paths]
    if not pages or any(page.parent != tossed_folder for page in pages):
        raise HTTPException(status_code=404, detail="Only a document in tossed/ can be put back.")
    try:
        loaded = document_files.load_pages(pages)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    decision = loaded[0][1].review.model_copy(update={"verdict": body.verdict})
    with no_job_running("restore a document", kind="archive"):
        document_files.place_document(output_path, loaded, decision,
                                      sidecar_for=lambda sidecar: sidecar.model_copy(update={"review": decision}))
    cache.clear()
    return dedupe_clusters(output_path=output_path)


# --- Normalize: merging merchant-name variants -------------------------------------------------
@router.get("/normalize", response_model=NormalizeOut)
def normalize_clusters(
    engine: str | None = None,
    threshold: float | None = None,
    output_path: Path = Depends(get_output_path),
):
    """Names one engine thinks are the same shop. Embedding clustering asks Ollama for any new name,
    so it takes the single model slot and is refused while a job holds it."""
    cfg = get_config()
    engine_id = engine or (cfg.normalize_engine if cfg.normalize_engine in ENGINES else next(iter(ENGINES)))
    if engine_id not in ENGINES:
        raise HTTPException(status_code=422, detail=f"unknown engine: {engine_id}")
    eps = threshold if threshold is not None else (
        cfg.normalize_embedding_threshold if engine_id == "embedding" else float(cfg.normalize_string_similarity))
    update_config(normalize_engine=engine_id,
                  **({"normalize_embedding_threshold": float(eps)} if engine_id == "embedding"
                     else {"normalize_string_similarity": int(eps)}))

    # The page speaks in the units the user sees — percent similarity, or embedding distance — while
    # both engines cluster on distance, so percent similarity is converted here.
    eps_value = float(eps) if engine_id == "embedding" else 1.0 - float(eps) / 100.0

    by_name, canonical = name_merge.names_in_use(output_path, cache.archive_state(output_path))
    names = sorted(set(by_name) | canonical)
    pairs = load_distinct_pairs(output_path)
    groups: list[NameGroup] = []
    if len(names) >= 2:
        # Archive moves the sidecars the names come from; the embedding engine also needs the GPU.
        with no_job_running("cluster merchant names", **({"claim": Claim(gpu=True)} if engine_id == "embedding"
                                                         else {"kind": "archive"})):
            if engine_id == "embedding":
                models.acquire(f"embed:{EMBED_MODEL}", unload_embeddings)
            clusters = ENGINES[engine_id].run(output_path, names, eps_value)
        groups = name_merge.visible_groups(clusters, by_name, canonical, pairs)

    return NormalizeOut(
        engines=[NormalizeEngineOut(id=key, label=value.label or key) for key, value in ENGINES.items()],
        engine=engine_id, threshold=float(eps), names=len(names),
        groups=[NameGroupOut(id=group.id, names=group.names,
                             counts=[NameCount(name=name, count=group.counts[name]) for name in group.names],
                             canonical=group.canonical) for group in groups],
        distinct_pairs=[sorted(pair) for pair in sorted(sorted(pair) for pair in pairs)],
    )


def _merge_out(plan: name_merge.MergePlan) -> MergeOut:
    return MergeOut(target=plan.target, variants=plan.variants, documents=plan.documents,
                    moves=[MoveOut(source=source, destination=destination) for source, destination in plan.moves],
                    decisions=plan.decisions, smart_matches=plan.smart_matches, error=plan.error)


@router.post("/normalize/preview", response_model=MergeOut)
def preview_merge(body: MergeIn, output_path: Path = Depends(get_output_path)):
    """Exactly what the merge would rewrite and re-file, before anything is written."""
    return _merge_out(name_merge.plan_merge(output_path, body.target, body.variants, cache.archive_state(output_path)))


@router.post("/normalize/merge", response_model=MergeOut)
def merge_names(body: MergeIn, output_path: Path = Depends(get_output_path)):
    """Rewrite every variant to the chosen name and re-file the documents it renames."""
    try:
        with no_job_running("merge names", kind="archive"):   # the one job that moves archived files
            plan = name_merge.apply_merge(output_path, body.target, body.variants)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cache.clear()  # names changed and files moved: every archive-derived view is stale
    return _merge_out(plan)


@router.post("/normalize/distinct", response_model=DistinctOut)
@one_edit_at_a_time
def confirm_distinct(body: DistinctIn, output_path: Path = Depends(get_output_path)):
    """Remember that these names are different shops, so the cluster stops coming back."""
    return DistinctOut(pairs=name_merge.confirm_different(output_path, body.names))


@router.delete("/normalize/distinct", response_model=DistinctOut)
@one_edit_at_a_time
def forget_distinct(first: str, second: str, output_path: Path = Depends(get_output_path)):
    return DistinctOut(pairs=name_merge.forget_different(output_path, first, second))
