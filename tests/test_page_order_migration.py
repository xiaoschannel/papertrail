"""The one-off Page Order migration: archived multi-page documents filed in scan order before sidecars
recorded page order are put in the order they were grouped in, put back, and finalized."""

import json
import shutil

import pytest

import page_order_migration as migration
from data import read_sidecar, save_document_groups, write_sidecar
from models import DocumentGroups
from viz_records import build_viz_records

FOLDER = "2025/03"
PLAIN = f"{FOLDER}/2025年3月2日 10：00 ビックカメラ 新宿店.png"
SECOND = f"{FOLDER}/2025年3月2日 10：00 ビックカメラ 新宿店 (2).png"
KEY = "5:106-107"


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _file_as_before_page_order(archive_dir):
    """The fixture's two-page document (grouped 107 then 106) as Archive filed it before page order: in scan
    order (106 under the plain name) and no page in either sidecar. Each scan's bytes say which it is."""
    plain, second = archive_dir / PLAIN, archive_dir / SECOND
    sidecars = {read_sidecar(p).serial: read_sidecar(p) for p in (plain, second)}
    for path, serial in ((plain, 106), (second, 107)):
        path.write_bytes(f"scan {serial}".encode())
        write_sidecar(path, sidecars[serial])
        raw = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        del raw["page"]
        path.with_suffix(".json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return archive_dir


@pytest.fixture
def legacy(archive_dir):
    return _file_as_before_page_order(archive_dir)


def test_an_archive_already_in_page_order_has_nothing_to_do(archive_dir):
    assert migration.plan(archive_dir) == migration.Plan(documents=[], problems=[])


def test_a_document_filed_in_scan_order_is_put_in_its_grouped_order_and_put_back(legacy):
    before = _files(legacy)
    assert [read_sidecar(legacy / p).serial for p in build_viz_records(legacy).set_index("filename").loc[KEY, "paths"]] \
        == [106, 107]

    [document] = migration.plan(legacy).documents
    assert document.key == KEY and document.renames
    assert [(p.serial, p.page, p.rel_path, p.target) for p in document.pages] == [
        (107, 1, SECOND, PLAIN), (106, 2, PLAIN, SECOND)]

    migration.put_in_order(legacy, KEY)

    assert (legacy / PLAIN).read_bytes() == b"scan 107" and (legacy / SECOND).read_bytes() == b"scan 106"
    assert [(read_sidecar(legacy / p).serial, read_sidecar(legacy / p).page) for p in (PLAIN, SECOND)] == [
        (107, 1), (106, 2)]
    assert build_viz_records(legacy).set_index("filename").loc[KEY, "paths"] == [PLAIN, SECOND]
    assert migration.plan(legacy).documents == []                   # nothing left to do
    assert [d.key for d in migration.put_in_order_documents(legacy)] == [KEY]
    with pytest.raises(KeyError):
        migration.put_in_order(legacy, KEY)

    migration.undo(legacy, KEY)
    assert _files(legacy) == before                                  # byte for byte, backup gone
    assert [d.key for d in migration.plan(legacy).documents] == [KEY]   # and offered again


def test_a_document_already_in_scan_order_only_gets_its_page_numbers(legacy):
    _set_groups(legacy, [["5:106", "5:107"]])

    [document] = migration.plan(legacy).documents
    assert not document.renames
    migration.put_in_order(legacy, KEY)

    assert (legacy / PLAIN).read_bytes() == b"scan 106"
    assert [(read_sidecar(legacy / p).serial, read_sidecar(legacy / p).page) for p in (PLAIN, SECOND)] == [
        (106, 1), (107, 2)]


def test_marked_pages_keep_their_names(legacy):
    marked = legacy / "marked"
    for rel in (PLAIN, SECOND):
        sidecar = read_sidecar(legacy / rel)
        target = marked / sidecar.original_filename
        shutil.move(legacy / rel, target)
        shutil.move((legacy / rel).with_suffix(".json"), target.with_suffix(".json"))

    [document] = migration.plan(legacy).documents
    assert not document.renames
    migration.put_in_order(legacy, KEY)

    assert (marked / "03022025100000_107.png").read_bytes() == b"scan 107"
    assert read_sidecar(marked / "03022025100000_107.png").page == 1
    assert read_sidecar(marked / "03022025100000_106.png").page == 2


def test_a_document_edited_since_can_t_be_undone(legacy):
    migration.put_in_order(legacy, KEY)
    (legacy / SECOND).rename(legacy / f"{FOLDER}/renamed.png")

    with pytest.raises(ValueError, match="changed since"):
        migration.undo(legacy, KEY)


def test_finalize_deletes_the_backups(legacy):
    migration.put_in_order(legacy, KEY)

    assert migration.finalize(legacy) == 1
    assert not (legacy / migration.BACKUP_DIR).exists() and migration.put_in_order_documents(legacy) == []
    assert (legacy / PLAIN).read_bytes() == b"scan 107"                # what was done stays done
    with pytest.raises(KeyError):
        migration.undo(legacy, KEY)


def test_a_document_with_a_page_missing_is_a_problem(legacy):
    (legacy / SECOND).unlink()

    result = migration.plan(legacy)
    assert result.documents == [] and result.problems == [f"{KEY}: 1 archived pages for its 2 scans"]


def test_the_dev_page_s_api_puts_in_order_undoes_and_finalizes(api_client, configured_archive):
    legacy_state = _file_as_before_page_order(configured_archive)
    state = api_client.get("/api/dev/page-order-archive").json()
    assert [d["key"] for d in state["to_do"]] == [KEY] and state["to_do"][0]["renames"] and state["done"] == []

    state = api_client.post("/api/dev/page-order-archive/apply-all").json()
    assert state["to_do"] == [] and [d["key"] for d in state["done"]] == [KEY]
    assert (legacy_state / PLAIN).read_bytes() == b"scan 107"
    receipt = api_client.get("/api/receipt/pages", params={"file": KEY}).json()
    assert [p["filename"] for p in receipt] == [PLAIN, SECOND]

    state = api_client.post("/api/dev/page-order-archive/undo", json={"key": KEY}).json()
    assert [d["key"] for d in state["to_do"]] == [KEY] and state["done"] == []
    assert api_client.post("/api/dev/page-order-archive/undo", json={"key": KEY}).status_code == 404

    state = api_client.post("/api/dev/page-order-archive/apply", json={"key": KEY}).json()
    before = state["done"][0]["before"]
    assert [api_client.get(f"/api/media/archived/{rel}").content for rel in before] == [b"scan 106", b"scan 107"]
    redo = api_client.post("/api/dev/page-order-archive/redo-all").json()
    assert redo["redone"] == 1 and redo["state"]["done"][0]["before"] == before
    assert api_client.post("/api/dev/page-order-archive/finalize").json() == {"removed": 1}
    assert api_client.get("/api/dev/page-order-archive").json() == {"to_do": [], "problems": [], "done": []}


def test_redo_puts_each_document_back_from_its_backup_and_does_it_again(legacy):
    migration.put_in_order(legacy, KEY)
    [document] = migration.put_in_order_documents(legacy)
    backup = migration.BACKUP_DIR + "/5_106-107/"
    assert migration.before(legacy, document) == [backup + PLAIN, backup + SECOND]   # as filed: 106, then 107
    assert (legacy / backup / PLAIN).read_bytes() == b"scan 106"
    # a result the migration of the time got wrong: the page numbers written the wrong way round
    for rel, page in ((PLAIN, 2), (SECOND, 1)):
        write_sidecar(legacy / rel, read_sidecar(legacy / rel).model_copy(update={"page": page}))

    assert migration.redo_all(legacy) == 1

    assert (legacy / PLAIN).read_bytes() == b"scan 107" and (legacy / SECOND).read_bytes() == b"scan 106"
    assert [read_sidecar(legacy / p).page for p in (PLAIN, SECOND)] == [1, 2]
    assert (legacy / backup / PLAIN).read_bytes() == b"scan 106"     # the backup is still the original
    migration.undo(legacy, KEY)
    assert (legacy / PLAIN).read_bytes() == b"scan 106" and read_sidecar(legacy / PLAIN).page == 1
    assert "page" not in json.loads((legacy / PLAIN).with_suffix(".json").read_text(encoding="utf-8"))


def _groups(root):
    return json.loads((root / "documents.json").read_text(encoding="utf-8"))["groups"]


def _set_groups(root, groups):
    save_document_groups(root, DocumentGroups(groups=groups))


def test_a_group_saved_in_the_wrong_order_can_be_put_in_the_order_chosen(legacy):
    _set_groups(legacy, [["5:106", "5:107"]])
    before = _files(legacy)
    [document] = migration.plan(legacy).documents
    assert not document.renames and document.slots == [PLAIN, SECOND]

    migration.put_in_order(legacy, KEY, order=[107, 106])

    assert (legacy / PLAIN).read_bytes() == b"scan 107" and read_sidecar(legacy / PLAIN).page == 1
    assert (legacy / SECOND).read_bytes() == b"scan 106" and read_sidecar(legacy / SECOND).page == 2
    assert _groups(legacy) == [["5:107", "5:106"]]                  # the order chosen is the group's now
    assert migration.plan(legacy).documents == []

    migration.redo_all(legacy)                                       # redo keeps the order chosen
    assert (legacy / PLAIN).read_bytes() == b"scan 107" and _groups(legacy) == [["5:107", "5:106"]]

    migration.undo(legacy, KEY)
    assert _files(legacy) == before                                  # the group too


def test_an_order_chosen_must_be_the_document_s_own_scans(legacy):
    with pytest.raises(ValueError, match="aren't the scans"):
        migration.put_in_order(legacy, KEY, order=[106, 108])
    assert _groups(legacy) == [["5:107", "5:106"]] and not (legacy / migration.BACKUP_DIR).exists()


def test_choosing_the_order_the_pages_are_filed_in_already_changes_nothing(archive_dir):
    _set_groups(archive_dir, [["5:106", "5:107"]])                   # filed 107 then 106, grouped the other way
    with pytest.raises(ValueError, match="in that order already"):
        migration.put_in_order(archive_dir, KEY, order=[107, 106])
    assert _groups(archive_dir) == [["5:106", "5:107"]] and not (archive_dir / migration.BACKUP_DIR).exists()


def test_the_plan_sees_a_page_changed_outside_it(legacy):
    [document] = migration.plan(legacy).documents
    assert document.pages[0].rel_path == SECOND
    moved = f"{FOLDER}/moved.png"
    (legacy / SECOND).rename(legacy / moved)
    (legacy / SECOND).with_suffix(".json").rename((legacy / moved).with_suffix(".json"))

    result = migration.plan(legacy)
    assert result.documents == [] and result.problems == [f"{KEY}: its pages are filed under different names"]


def test_the_dev_page_s_api_takes_an_order_chosen_for_a_document(api_client, configured_archive):
    root = _file_as_before_page_order(configured_archive)
    _set_groups(root, [["5:106", "5:107"]])
    [document] = api_client.get("/api/dev/page-order-archive").json()["to_do"]
    assert document["slots"] == [PLAIN, SECOND] and not document["renames"]

    state = api_client.post("/api/dev/page-order-archive/apply-all", json={"orders": {KEY: [107, 106]}}).json()
    assert state["to_do"] == [] and (root / PLAIN).read_bytes() == b"scan 107"
    assert api_client.post("/api/dev/page-order-archive/undo", json={"key": KEY}).status_code == 200
    bad = api_client.post("/api/dev/page-order-archive/apply", json={"key": KEY, "order": [106, 999]})
    assert bad.status_code == 409 and "aren't the scans" in bad.json()["detail"]
