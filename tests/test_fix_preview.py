"""Fix Preview (temporary): listing must write nothing, and each clean-up applies, undoes and finalizes."""

import json
from pathlib import Path

from papertrail.archive import history as archive_history
from papertrail.data import WORKSHOP_LOG, load_smart_match_cache, load_workshop_log, read_sidecar, write_sidecar
from papertrail.models import DocumentFields, ReceiptResult, WorkshopRecord


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def _old_style_state(archive: Path) -> str:
    """What the old code left: cache entries from the tossed and the marked document, and a Workshop record that
    says "Receipt" was read where the model read no name. Committed, as the folder's history would hold it."""
    cache = load_smart_match_cache(archive)
    cache["9:201"] = {"extracted": "(blank receipt)", "confirmed": "(blank receipt)", "extracted_phone": ""}
    cache["9:202"] = {"extracted": "ローソン", "confirmed": "ローソン 池袋店", "extracted_phone": ""}
    (archive / "smart_match_cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    page = next(p for p in sorted(archive.glob("20*/*/*.png")))
    sidecar = read_sidecar(page)
    fields = dict(document_type="receipt", date="2025-01-10", time="13:00", cost=500.0, currency="JPY")
    record = WorkshopRecord(at=1.0, key="1:1", filenames=[page.name],
                            read=DocumentFields(name="Receipt", **fields),
                            accepted=DocumentFields(name="Receipt", **fields), corrected=[])
    write_sidecar(page, sidecar.model_copy(update={"workshop": record, "extraction": ReceiptResult(
        language="ja", address="", name="", **fields)}))
    (archive / WORKSHOP_LOG).write_text(record.model_dump_json(exclude_none=True) + "\n", encoding="utf-8")

    root = archive.parent
    archive_history.ensure_repository(root)
    archive_history.commit(root, "as the old code left it")
    return page.name


def test_listing_changes_nothing_on_disk(api_client, configured_archive):
    _old_style_state(configured_archive)
    before = _snapshot(configured_archive.parent)

    response = api_client.get("/api/dev/fix-preview")

    assert response.status_code == 200, response.text
    assert _snapshot(configured_archive.parent) == before


def test_the_smart_match_clean_up_removes_what_unaccepted_documents_taught(api_client, configured_archive):
    _old_style_state(configured_archive)
    listed = api_client.get("/api/dev/fix-preview").json()["smart_match"]
    assert {e["key"] for e in listed["entries"]} == {"9:201", "9:202"} and listed["clean_up"]["blocked"] == ""

    applied = api_client.post("/api/dev/fix-preview/smart-match/apply").json()["smart_match"]

    assert set(load_smart_match_cache(configured_archive)) == {"5:104"}   # the accepted document's stays
    assert applied["entries"] == [] and applied["clean_up"]["uncommitted"] == ["archive/smart_match_cache.json"]

    undone = api_client.post("/api/dev/fix-preview/smart-match/undo").json()["smart_match"]
    assert len(undone["entries"]) == 2 and undone["clean_up"]["uncommitted"] == []

    api_client.post("/api/dev/fix-preview/smart-match/apply")
    done = api_client.post("/api/dev/fix-preview/smart-match/finalize").json()["smart_match"]
    assert done["clean_up"] == {"todo": 0, "uncommitted": [], "blocked": "Nothing to do."}


def test_the_records_clean_up_keeps_what_the_model_read(api_client, configured_archive):
    name = _old_style_state(configured_archive)
    [listed] = api_client.get("/api/dev/fix-preview").json()["records"]["records"]
    assert listed["differs"] == ["name"] and listed["literal"]["name"] == ""

    api_client.post("/api/dev/fix-preview/records/apply")

    [record] = load_workshop_log(configured_archive)
    assert record.read.name == "" and record.corrected == ["name"]
    [page] = configured_archive.glob(f"20*/*/{name}")
    assert read_sidecar(page).workshop == record
    done = api_client.post("/api/dev/fix-preview/records/finalize").json()["records"]
    assert done["clean_up"]["todo"] == 0 and done["clean_up"]["uncommitted"] == []


def test_apply_refuses_while_its_files_have_uncommitted_changes(api_client, configured_archive):
    _old_style_state(configured_archive)
    cache = configured_archive / "smart_match_cache.json"
    cache.write_text(cache.read_text(encoding="utf-8") + " ", encoding="utf-8")

    refused = api_client.post("/api/dev/fix-preview/smart-match/apply")

    assert refused.status_code == 409 and "uncommitted" in refused.json()["detail"]
    assert "9:201" in load_smart_match_cache(configured_archive)
