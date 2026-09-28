"""Contract tests: Dedupe — finding likely duplicates and tossing one."""

from data import read_sidecar


def _duplicate_of(api_client, index=1):
    """Make one archived document look like a re-scan of another: same moment, same amount."""
    records = sorted(api_client.get("/api/viz/records").json(), key=lambda r: r["filename"])
    original = next(r for r in records if r["document_type"] == "receipt" and len(r["paths"]) == 1)
    copy = next(r for r in records if r["document_type"] == "receipt" and r["filename"] != original["filename"])
    refiled = api_client.patch("/api/receipt", json={
        "file": copy["filename"], "document_type": "receipt", "name": copy["name"], "date": original["date"],
        "time": original["time"], "cost": original["cost"], "currency": original["currency"],
        "address": copy["address"], "language": copy["language"], "comment": copy["comment"]}).json()
    return original, refiled     # the edit re-files it, so its path changed


def test_a_multi_page_document_is_not_a_duplicate_of_itself(api_client):
    """Its pages share a date, time and cost — clustering files would flag them."""
    body = api_client.get("/api/curate/dedupe").json()

    assert body["archived"] > 0
    assert body["clusters"] == []


def test_clusters_are_documents_with_the_same_cost_minutes_apart(api_client):
    original, copy = _duplicate_of(api_client)

    [cluster] = api_client.get("/api/curate/dedupe").json()["clusters"]

    assert {m["filename"] for m in cluster["members"]} == {original["filename"], copy["filename"]}
    assert len({m["cost"] for m in cluster["members"]}) == 1          # equal cost is what makes them candidates


def test_tossing_a_duplicate_moves_it_out_of_the_archive(api_client, configured_archive):
    _original, copy = _duplicate_of(api_client)
    [cluster] = api_client.get("/api/curate/dedupe").json()["clusters"]
    victim = next(m for m in cluster["members"] if m["filename"] == copy["filename"])

    tossed = api_client.post("/api/curate/dedupe/toss", json={"path": victim["path"]}).json()["tossed"]

    assert tossed and all(p.startswith("tossed/") for p in tossed)
    assert not (configured_archive / victim["path"]).exists()
    assert read_sidecar(configured_archive / tossed[0]).review.verdict == "tossed"
    # gone from the archive-derived views, and no longer a cluster
    assert victim["path"] not in {r["path"] for r in api_client.get("/api/viz/records").json()}
    assert api_client.get("/api/curate/dedupe").json()["clusters"] == []


def test_tossing_an_unknown_document_is_a_404(api_client):
    assert api_client.post("/api/curate/dedupe/toss", json={"path": "2025/01/nope.png"}).status_code == 404


# --- Normalize -------------------------------------------------------------------------------
def _clusters(api_client, **params):
    return api_client.get("/api/curate/normalize", params={"engine": "string", **params}).json()


def test_normalize_groups_near_identical_names(api_client):
    body = _clusters(api_client, threshold=80)

    assert body["engine"] == "string" and body["names"] > 0
    assert {e["id"] for e in body["engines"]} == {"string", "embedding"}
    assert body["groups"], "the fixture has near-duplicate merchant names"
    group = body["groups"][0]
    assert len(group["names"]) >= 2
    assert {c["name"] for c in group["counts"]} == set(group["names"])


def test_preview_says_what_a_merge_would_do_without_doing_it(api_client, configured_archive):
    group = _clusters(api_client, threshold=80)["groups"][0]
    target, *variants = group["names"]

    preview = api_client.post("/api/curate/normalize/preview",
                              json={"target": target, "variants": variants}).json()

    assert preview["error"] is None and preview["variants"] == variants
    assert all(move["source"] != move["destination"] for move in preview["moves"])
    for move in preview["moves"]:
        assert (configured_archive / move["source"]).exists()          # nothing has moved yet
        assert not (configured_archive / move["destination"]).exists()


def test_merging_rewrites_the_names_and_refiles_the_documents(api_client, configured_archive):
    group = _clusters(api_client, threshold=80)["groups"][0]
    target, *variants = group["names"]

    merged = api_client.post("/api/curate/normalize/merge",
                             json={"target": target, "variants": variants}).json()

    assert merged["error"] is None and merged["documents"] > 0
    for move in merged["moves"]:
        assert (configured_archive / move["destination"]).exists()
        assert not (configured_archive / move["source"]).exists()
    names = {r["name"] for r in api_client.get("/api/viz/records").json()}
    assert target in names and not (set(variants) & names)             # the charts see one name now


def test_a_merge_needs_a_target_and_variants(api_client):
    refused = api_client.post("/api/curate/normalize/merge", json={"target": "Shop", "variants": []})
    assert refused.status_code == 422 and "at least one" in refused.json()["detail"]


def test_confirming_names_are_different_hides_the_cluster(api_client):
    group = _clusters(api_client, threshold=80)["groups"][0]

    api_client.post("/api/curate/normalize/distinct", json={"names": group["names"]})
    after = _clusters(api_client, threshold=80)
    assert group["id"] not in {g["id"] for g in after["groups"]}
    assert sorted(group["names"][:2]) in after["distinct_pairs"]

    first, second = group["names"][:2]
    api_client.delete("/api/curate/normalize/distinct", params={"first": first, "second": second})
    assert sorted([first, second]) not in _clusters(api_client, threshold=80)["distinct_pairs"]


def test_a_stricter_similarity_groups_fewer_names(api_client):
    loose = _clusters(api_client, threshold=55)
    strict = _clusters(api_client, threshold=97)

    loose_names = sum(len(g["names"]) for g in loose["groups"])
    strict_names = sum(len(g["names"]) for g in strict["groups"])
    assert loose_names > strict_names, "a low bar should group more names than a high one"
    assert all(len(g["names"]) < loose["names"] for g in strict["groups"])


def test_asking_for_clusters_saves_nothing(api_client):
    """The page saves what you settle on through the config; a request made on the way (the one a switch
    of engine once sent with the old engine's threshold) must not become your setting."""
    saved = api_client.patch("/api/config", json={"normalize_engine": "embedding",
                                                  "normalize_string_similarity": 62}).json()

    assert _clusters(api_client, threshold=70)["engine"] == "string"
    assert api_client.get("/api/config").json() == saved


def test_one_engines_threshold_with_the_other_is_refused(api_client, monkeypatch):
    """0.05 read as a similarity is 0%: every name would cluster with every other."""
    import normalize_engines

    def no_embedding(*_args):
        raise AssertionError("refused before any name is embedded (that would call Ollama)")
    monkeypatch.setattr(normalize_engines, "ensure_embeddings", no_embedding)

    as_string = api_client.get("/api/curate/normalize", params={"engine": "string", "threshold": 0.05})
    assert as_string.status_code == 422 and "between 50 and 100" in as_string.json()["detail"]
    as_embedding = api_client.get("/api/curate/normalize", params={"engine": "embedding", "threshold": 80})
    assert as_embedding.status_code == 422


def test_keeping_both_stops_the_cluster_coming_back(api_client, configured_archive):
    original, copy = _duplicate_of(api_client)
    [cluster] = api_client.get("/api/curate/dedupe").json()["clusters"]

    body = api_client.post("/api/curate/dedupe/keep",
                           json={"documents": [m["filename"] for m in cluster["members"]]}).json()

    assert body["clusters"] == []
    [kept] = body["kept"]
    assert set(kept["documents"]) == {original["filename"], copy["filename"]}
    assert all(kept["names"])                                   # named, so the undo list is readable
    assert (configured_archive / original["path"]).exists()     # nothing moved
    assert (configured_archive / copy["path"]).exists()
    assert api_client.get("/api/curate/dedupe").json()["clusters"] == []   # and it stays gone


def test_considering_a_kept_pair_again_offers_it_once_more(api_client):
    original, copy = _duplicate_of(api_client)
    api_client.post("/api/curate/dedupe/keep", json={"documents": [original["filename"], copy["filename"]]})

    body = api_client.request("DELETE", "/api/curate/dedupe/keep",
                              params={"first": original["filename"], "second": copy["filename"]}).json()

    assert body["kept"] == []
    assert len(body["clusters"]) == 1


def test_members_say_how_many_pages_they_have(api_client):
    _duplicate_of(api_client)
    [cluster] = api_client.get("/api/curate/dedupe").json()["clusters"]
    assert all(m["pages"] >= 1 for m in cluster["members"])


def test_tossing_can_be_undone(api_client, configured_archive):
    _original, copy = _duplicate_of(api_client)
    [cluster] = api_client.get("/api/curate/dedupe").json()["clusters"]
    victim = next(m for m in cluster["members"] if m["filename"] == copy["filename"])

    tossed = api_client.post("/api/curate/dedupe/toss", json={"path": victim["path"]}).json()
    assert tossed["previous_verdict"] == "accepted" and tossed["name"] == victim["name"]

    body = api_client.post("/api/curate/dedupe/restore",
                           json={"paths": tossed["tossed"], "verdict": tossed["previous_verdict"]}).json()

    restored = next(r for r in api_client.get("/api/viz/records").json() if r["filename"] == copy["filename"])
    assert (configured_archive / restored["path"]).exists()            # back in the archive
    assert not any((configured_archive / p).exists() for p in tossed["tossed"])
    assert read_sidecar(configured_archive / restored["path"]).review.verdict == "accepted"
    assert len(body["clusters"]) == 1                                   # and it is a candidate again


def test_restoring_something_that_is_not_there_is_a_404(api_client):
    assert api_client.post("/api/curate/dedupe/restore",
                           json={"paths": ["tossed/nope.png"], "verdict": "accepted"}).status_code == 404


def test_only_a_document_in_tossed_can_be_restored(api_client, configured_archive, tmp_path):
    record = api_client.get("/api/viz/records").json()[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.png").write_bytes(b"x")

    for paths in ([record["path"]], ["../outside/victim.png"], [str(outside / "victim.png")], []):
        response = api_client.post("/api/curate/dedupe/restore", json={"paths": paths, "verdict": "accepted"})
        assert response.status_code == 404, paths
    assert (outside / "victim.png").exists() and (configured_archive / record["path"]).exists()


def _running(kind, claim):
    """A job of ``kind`` that holds ``claim`` until the returned event is set."""
    import threading

    from api.jobs import runner

    release = threading.Event()

    def hold(progress):
        release.wait(10)          # and return nothing: a job's return value is its message, a string

    runner.start(kind, f"{kind} (test)", hold, claim)
    return release


def test_names_cluster_and_merge_while_ocr_reads_another_batch(api_client):
    from api.jobs import Claim

    release = _running("ocr", Claim(batches=frozenset({12}), gpu=True))
    try:
        assert api_client.get("/api/curate/normalize", params={"engine": "string"}).status_code == 200
    finally:
        release.set()


def test_names_cluster_with_embeddings_only_when_the_gpu_is_free(api_client, monkeypatch):
    from api.jobs import Claim

    release = _running("ocr", Claim(batches=frozenset({12}), gpu=True))
    try:
        assert api_client.get("/api/curate/normalize", params={"engine": "embedding"}).status_code == 409
    finally:
        release.set()


def test_pairs_kept_apart_by_two_clicks_at_once_are_both_kept(api_client):
    import threading

    records = api_client.get("/api/viz/records").json()
    a, b, c = (r["filename"] for r in records[:3])
    threads = [threading.Thread(target=api_client.post, args=("/api/curate/dedupe/keep",),
                                kwargs={"json": {"documents": pair}}) for pair in ([a, b], [a, c])]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    kept = {tuple(p["documents"]) for p in api_client.get("/api/curate/dedupe").json()["kept"]}
    assert kept == {tuple(sorted([a, b])), tuple(sorted([a, c]))}


# --- The sidebar's counts --------------------------------------------------------------------
def _counts(api_client):
    return api_client.get("/api/curate/counts").json()


def _rename(api_client, record, name):
    """Give an archived receipt another merchant name (the edit re-files it)."""
    return api_client.patch("/api/receipt", json={
        "file": record["filename"], "document_type": record["document_type"], "name": name,
        "date": record["date"], "time": record["time"], "cost": record["cost"], "currency": record["currency"],
        "address": record["address"], "language": record["language"], "comment": record["comment"]}).json()


def test_each_count_is_what_its_page_lists(api_client):
    counts = _counts(api_client)

    assert counts["workshop"] == len(api_client.get("/api/curate/workshop").json()["documents"]) > 0
    assert counts["dedupe"] == len(api_client.get("/api/curate/dedupe").json()["clusters"])
    assert counts["normalize_engine"] == "string" and counts["normalize_threshold"] == 90
    assert counts["normalize"] == len(_clusters(api_client, threshold=90)["groups"]) > 0
    assert counts["brands_min_names"] == 4


def test_the_dedupe_count_follows_the_page(api_client):
    original, copy = _duplicate_of(api_client)
    assert _counts(api_client)["dedupe"] == 1

    api_client.post("/api/curate/dedupe/keep", json={"documents": [original["filename"], copy["filename"]]})
    assert _counts(api_client)["dedupe"] == 0


def test_the_brands_count_takes_prefixes_shared_by_enough_names(api_client):
    records = [r for r in api_client.get("/api/viz/records").json() if r["document_type"] == "receipt"]
    for number, record in zip(("One", "Two", "Three"), records):
        _rename(api_client, record, f"Quillfeather Stores {number}")
    shared = [s for s in api_client.get("/api/brands/suggestions", params={"min_count": 2}).json()
              if s["prefix"].startswith("quillfeather")]
    assert shared and shared[0]["count"] == 3          # the page, at its own setting, offers it

    assert _counts(api_client)["brands"] == 0          # three names are below the sidebar's four
    api_client.patch("/api/config", json={"sidebar_brands_min_names": 3})
    assert _counts(api_client)["brands"] == len(api_client.get(
        "/api/brands/suggestions", params={"min_count": 3}).json()) >= 1


def test_the_normalize_count_takes_its_own_threshold_and_forgets_ruled_apart_names(api_client):
    loose = _counts(api_client)["normalize"]
    api_client.patch("/api/config", json={"sidebar_normalize_string_similarity": 99})
    assert _counts(api_client)["normalize"] == len(_clusters(api_client, threshold=99)["groups"]) < loose

    api_client.patch("/api/config", json={"sidebar_normalize_string_similarity": 90})
    for group in _clusters(api_client, threshold=90)["groups"]:
        api_client.post("/api/curate/normalize/distinct", json={"names": group["names"]})
    assert _counts(api_client)["normalize"] == 0


def test_an_embedding_count_uses_only_the_names_already_embedded(api_client, configured_archive, monkeypatch):
    import numpy as np

    import name_similarity
    from data import save_embeddings_cache

    def no_model(**_kwargs):
        raise AssertionError("the sidebar must not ask Ollama")
    monkeypatch.setattr(name_similarity, "embed", no_model)
    names = sorted({r["name"] for r in api_client.get("/api/viz/records").json()})
    first, second, *_rest = names
    save_embeddings_cache(configured_archive, [first, second], np.ones((2, 8), dtype=np.float32))   # one direction
    api_client.patch("/api/config", json={"sidebar_normalize_engine": "embedding",
                                          "sidebar_normalize_embedding_threshold": 0.05})

    counts = _counts(api_client)

    assert counts["normalize_engine"] == "embedding" and counts["normalize_threshold"] == 0.05
    assert counts["normalize"] == 1
    assert counts["normalize_unembedded"] >= len(names) - 2         # every other name waits for the page


def test_counts_are_kept_until_the_archive_changes(api_client, monkeypatch):
    import workshop

    calls = []
    real = workshop.marked_documents
    monkeypatch.setattr(workshop, "marked_documents", lambda path: calls.append(path) or real(path))
    _counts(api_client)
    _counts(api_client)
    assert len(calls) == 1

    _duplicate_of(api_client)                                       # re-files a document
    _counts(api_client)
    assert len(calls) == 2


def test_counts_do_not_wait_for_a_job(api_client):
    from api.jobs import Claim

    api_client.patch("/api/config", json={"sidebar_normalize_engine": "embedding"})
    release = _running("archive", Claim(gpu=True))
    try:
        assert api_client.get("/api/curate/counts").status_code == 200
    finally:
        release.set()


def test_counts_can_be_previewed_at_settings_not_saved(api_client):
    saved = _counts(api_client)
    strict = api_client.get("/api/curate/counts", params={"normalize_engine": "string", "normalize_threshold": 99,
                                                          "brands_min_names": 1}).json()

    assert strict["normalize"] == len(_clusters(api_client, threshold=99)["groups"]) < saved["normalize"]
    assert strict["brands_min_names"] == 1 and strict["brands"] >= saved["brands"]
    assert _counts(api_client) == saved                                  # nothing was saved
    assert api_client.get("/api/config").json()["sidebar_normalize_string_similarity"] == 90
    assert api_client.get("/api/curate/counts", params={"normalize_engine": "nope"}).status_code == 422
    mismatched = api_client.get("/api/curate/counts", params={"normalize_engine": "string", "normalize_threshold": 0.05})
    assert mismatched.status_code == 422 and "between 50 and 100" in mismatched.json()["detail"]


def test_a_normalize_count_at_full_similarity_groups_only_identical_names(api_client):
    """100% is distance 0, which the clustering refuses as such: the count still answers."""
    response = api_client.get("/api/curate/counts", params={"normalize_engine": "string", "normalize_threshold": 100})

    assert response.status_code == 200 and response.json()["normalize"] == 0
    assert _clusters(api_client, threshold=100)["groups"] == []          # and the page's own list likewise


def test_a_preview_takes_the_unsaved_prefix_settings_too(api_client):
    records = [r for r in api_client.get("/api/viz/records").json() if r["document_type"] == "receipt"]
    for number, record in zip(("One", "Two"), records):
        _rename(api_client, record, f"Quillfeather Stores {number}")
    long_only = {"brands_min_names": 2, "prefix_min_length": 20}          # "quillfeather stores" is 19

    assert api_client.get("/api/curate/counts", params={"brands_min_names": 2}).json()["brands"] >= 1
    assert api_client.get("/api/curate/counts", params=long_only).json()["brands"] == len(
        api_client.get("/api/brands/suggestions", params={"min_count": 2, "min_length": 20}).json())
