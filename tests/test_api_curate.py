"""Contract tests: Dedupe — finding likely duplicates and tossing one."""

import pytest

from papertrail.data import load_smart_match_cache, read_sidecar, write_sidecar


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
    from papertrail.curate import normalize_engines

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

    from papertrail.api.jobs import runner

    release = threading.Event()

    def hold(progress):
        release.wait(10)          # and return nothing: a job's return value is its message, a string

    runner.start(kind, f"{kind} (test)", hold, claim)
    return release


def test_names_cluster_and_merge_while_ocr_reads_another_batch(api_client):
    from papertrail.api.jobs import Claim

    release = _running("ocr", Claim(batches=frozenset({12}), gpu=True))
    try:
        assert api_client.get("/api/curate/normalize", params={"engine": "string"}).status_code == 200
    finally:
        release.set()


def test_names_cluster_with_embeddings_only_when_the_gpu_is_free(api_client, monkeypatch):
    from papertrail.api.jobs import Claim

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
    assert counts["unnamed"] == len(api_client.get("/api/curate/unnamed").json()["unnamed"]) > 0


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

    from papertrail.curate import similarity as name_similarity
    from papertrail.data import save_embeddings_cache

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
    from papertrail.curate import workshop

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
    from papertrail.api.jobs import Claim

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


# --- Unnamed ---------------------------------------------------------------------------------------
UNNAMED, BY_HAND, BIC = "11162024112000_110.png", "11232024190000_111.png", "5:106-107"


def _unnamed(api_client):
    return api_client.get("/api/curate/unnamed").json()


def _name(api_client, documents, name):
    return api_client.post("/api/curate/unnamed/name", json={"documents": documents, "name": name})


def test_the_unnamed_are_filed_under_a_placeholder_and_the_named_by_hand_were_read_without_a_name(api_client):
    body = _unnamed(api_client)

    [unnamed] = body["unnamed"]
    assert (unnamed["filename"], unnamed["name"], unnamed["read"]) == (UNNAMED, "Receipt", "")
    assert unnamed["path"].endswith("Receipt.png") and unnamed["pages"] == 1
    [by_hand] = body["named_by_hand"]
    assert (by_hand["filename"], by_hand["name"], by_hand["read"]) == (BY_HAND, "パン工房サンプル", "")
    names = [n["name"] for n in body["names"]]
    assert "Receipt" not in names and "パン工房サンプル" in names
    counts = [n["count"] for n in body["names"]]
    assert counts == sorted(counts, reverse=True)                                  # most used first


def test_naming_files_the_document_under_the_name_and_keeps_what_was_read(api_client, configured_archive):
    before = _unnamed(api_client)["unnamed"][0]

    response = _name(api_client, [UNNAMED], "  パン工房サンプル ")

    assert response.status_code == 200
    [named] = response.json()["named"]
    assert named["previous_name"] == "Receipt"
    assert named["document"]["name"] == "パン工房サンプル" and named["document"]["path"].endswith("パン工房サンプル.png")
    assert not (configured_archive / before["path"]).exists()                    # re-filed under the name
    sidecar = read_sidecar(configured_archive / named["document"]["path"])
    assert sidecar.review.name == "パン工房サンプル" and sidecar.review.cost == 1280.0
    assert sidecar.extraction.name == ""                                          # what the model read stays
    assert load_smart_match_cache(configured_archive)["5:110"] == {
        "extracted": "", "confirmed": "パン工房サンプル", "extracted_phone": ""}
    body = _unnamed(api_client)
    assert body["unnamed"] == [] and _counts(api_client)["unnamed"] == 0
    assert [d["filename"] for d in body["named_by_hand"]] == [UNNAMED, BY_HAND]   # one of the named by hand now


def test_naming_back_to_the_placeholder_undoes_it(api_client):
    _name(api_client, [UNNAMED], "Some Bakery")

    [named] = _name(api_client, [UNNAMED], "Receipt").json()["named"]

    assert named["previous_name"] == "Some Bakery"
    assert [d["filename"] for d in _unnamed(api_client)["unnamed"]] == [UNNAMED]


def test_naming_several_at_once_moves_every_page_of_each(api_client, configured_archive):
    named = _name(api_client, [UNNAMED, BIC, UNNAMED], "Quillfeather Stores").json()["named"]

    assert [n["document"]["filename"] for n in named] == [UNNAMED, BIC]         # each once, in the order asked
    bic = next(r for r in api_client.get("/api/viz/records").json() if r["filename"] == BIC)
    assert [p.rsplit("/", 1)[1] for p in bic["paths"]] == ["2025年3月2日 10：00 Quillfeather Stores.png",
                                                            "2025年3月2日 10：00 Quillfeather Stores (2).png"]
    assert {read_sidecar(configured_archive / p).review.name for p in bic["paths"]} == {"Quillfeather Stores"}
    assert not list((configured_archive / "2025" / "03").glob("*ビックカメラ*"))


def test_a_document_read_with_a_name_can_be_filed_unnamed(api_client, configured_archive):
    """The name read may be no name at all (a slogan, a garble): the page shows what it was."""
    path = configured_archive / "2023" / "11" / "2023年11月3日 08：30 スターバックス 渋谷店.png"
    sidecar = read_sidecar(path)
    write_sidecar(path, sidecar.model_copy(update={"review": sidecar.review.model_copy(update={"name": "Receipt"})}))

    unnamed = {d["filename"]: d for d in _unnamed(api_client)["unnamed"]}

    assert unnamed["11032023083000_109.png"]["read"] == "スターバックス 渋谷店"


def test_naming_refuses_a_blank_name_and_documents_not_in_the_archive(api_client, configured_archive):
    blank = _name(api_client, [UNNAMED], "   ")
    assert blank.status_code == 422 and blank.json()["detail"] == "Type a name."
    assert _name(api_client, [UNNAMED, "9:999"], "Somewhere").status_code == 404
    assert _name(api_client, [], "Somewhere").status_code == 422
    assert [d["filename"] for d in _unnamed(api_client)["unnamed"]] == [UNNAMED]   # nothing was renamed


def _second_unnamed_beside_the_first(api_client):
    """Another receipt filed unnamed in the same minute, so the two share a name: "… Receipt" and "… Receipt (2)"."""
    other = next(r for r in api_client.get("/api/viz/records").json() if r["filename"] == "11032023083000_109.png")
    _rename(api_client, {**other, "date": "2024-11-16", "time": "11:20:00"}, "Receipt")
    paths = {d["filename"]: d["path"] for d in _unnamed(api_client)["unnamed"]}
    assert paths[UNNAMED].endswith("Receipt.png") and paths[other["filename"]].endswith("Receipt (2).png")
    return other["filename"]


def test_naming_documents_that_share_a_name_moves_every_one(api_client, configured_archive):
    """Moving the first closes the gap it leaves, which renames the second before its turn comes."""
    second = _second_unnamed_beside_the_first(api_client)

    response = _name(api_client, [UNNAMED, second], "Somewhere")

    assert response.status_code == 200, response.text
    assert [n["document"]["name"] for n in response.json()["named"]] == ["Somewhere", "Somewhere"]
    assert _unnamed(api_client)["unnamed"] == []
    assert not list((configured_archive / "2024" / "11").glob("*Receipt*"))


def test_a_naming_that_fails_part_way_says_which_were_named(api_client, configured_archive, monkeypatch):
    from papertrail.curate import naming

    second = _second_unnamed_beside_the_first(api_client)
    real, calls = naming.place_document, []

    def fails_the_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("the file is in the way")
        return real(*args, **kwargs)

    monkeypatch.setattr(naming, "place_document", fails_the_second)

    response = _name(api_client, [UNNAMED, second], "Somewhere")

    assert response.status_code == 200
    body = response.json()
    assert [n["document"]["filename"] for n in body["named"]] == [UNNAMED]
    assert body["error"] == "Named 1 of 2; the next one couldn't be moved: the file is in the way"
    assert [d["filename"] for d in _unnamed(api_client)["unnamed"]] == [second]   # the other stayed as it was


def test_the_page_gets_back_the_documents_it_named_as_they_are_filed_now(api_client):
    """Naming one of two that share a name renumbers the other's files: the page asks for both again."""
    second = _second_unnamed_beside_the_first(api_client)
    _name(api_client, [UNNAMED, second], "Somewhere")
    _name(api_client, [UNNAMED], "Receipt")                      # "Somewhere (2)" moves down to "Somewhere"

    body = api_client.get("/api/curate/unnamed", params={"keep": [second, UNNAMED, "9:999"]}).json()

    assert [(d["filename"], d["unnamed"]) for d in body["kept"]] == [(second, False), (UNNAMED, True)]
    assert body["kept"][0]["path"].endswith("Somewhere.png")
    assert body["placeholders"] == ["Corrupted", "Document", "Receipt"]


def test_naming_is_refused_before_anything_moves_when_a_document_has_moved_since(configured_archive):
    from papertrail.curate import naming

    gone = "2024/11/2024年11月16日 11：20 Elsewhere.png"
    with pytest.raises(naming.NotNamed, match="Open it again"):
        naming.name_documents(configured_archive, [["2024/11/2024年11月23日 19：00 パン工房サンプル.png"], [gone]], "X")
    assert (configured_archive / "2024/11/2024年11月23日 19：00 パン工房サンプル.png").exists()   # nothing moved
