"""The one-off Straighten Archive migration: finding tilted scans in the archive, straightening one with its
boxes, putting it back, and finalizing (deleting the backups)."""

import io
import json
import time

import pytest
from PIL import Image

import deskew
import straighten_migration as migration
from tilt_scans import scan as tilt_fixture

#: A filed page whose sidecar has an OCR box, in the archive fixture.
PAGE = "2025/01/2025年1月10日 13：26 セブン-イレブン 品川駅前店.png"


def _scan(client):
    state = client.post("/api/dev/straighten-archive/scan").json()
    deadline = time.monotonic() + 60
    while state["running"]:
        assert time.monotonic() < deadline, "the look through the archive didn't finish"
        time.sleep(0.1)
        state = client.get("/api/dev/straighten-archive").json()
    return state


def _boxes(page):
    return json.loads(page.with_suffix(".json").read_text(encoding="utf-8"))["ocr"]["boxes"]


def test_tilted_archived_scans_are_found_straightened_with_their_boxes_and_put_back(api_client, configured_archive):
    page = configured_archive / PAGE
    tilt_fixture("receipt_crooked.png").save(page)
    before = page.read_bytes(), page.with_suffix(".json").read_bytes()
    boxes_before = _boxes(page)

    state = _scan(api_client)
    assert state["error"] is None and state["total"] > 5 and state["done"] == state["total"]
    assert [(f["rel_path"], f["batch_id"]) for f in state["found"]] == [(PAGE, 5)]   # 1x1 placeholders aren't
    assert state["found"][0]["degrees"] == pytest.approx(3.7, abs=0.2) and state["flag_degrees"] == deskew.MIN_DEGREES
    assert page.read_bytes() == before[0]                                    # looking changes nothing
    outline = api_client.get(f"/api/dev/straighten-archive/ink-outline/{PAGE}").json()["points"]
    assert outline == [list(p) for p in deskew.ink_outline(tilt_fixture("receipt_crooked.png"))]

    done = api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": 3.7}).json()
    assert done["found"] == [] and done["straightened"] == [PAGE]
    assert abs(deskew.estimate_file_skew(page).degrees) <= 0.2                # level now
    moved = _boxes(page)
    assert [b["text"] for b in moved] == [b["text"] for b in boxes_before]  # same boxes, same order
    assert [b["coords"] for b in moved] != [b["coords"] for b in boxes_before]
    assert api_client.get(f"/api/dev/straighten-archive/thumb/{PAGE}").headers["content-type"] == "image/jpeg"

    again = api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": 1}).json()
    assert again["straightened"] == [PAGE]                                   # the backup is still the first

    put_back = api_client.post("/api/dev/straighten-archive/undo", json={"rel_path": PAGE}).json()
    assert put_back["straightened"] == [] and [f["rel_path"] for f in put_back["found"]] == [PAGE]   # offered again
    assert (page.read_bytes(), page.with_suffix(".json").read_bytes()) == before       # exactly as it was


def test_finalize_deletes_the_backups_and_keeps_the_straightened_scans(api_client, configured_archive):
    page = configured_archive / PAGE
    tilt_fixture("receipt_crooked.png").save(page)
    api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": 3.7})
    straightened = page.read_bytes()
    assert (configured_archive / migration.BACKUP_DIR).is_dir()

    assert api_client.post("/api/dev/straighten-archive/finalize").json() == {"removed": 1}
    assert not (configured_archive / migration.BACKUP_DIR).exists() and page.read_bytes() == straightened
    assert api_client.get("/api/dev/straighten-archive").json()["straightened"] == []
    assert api_client.post("/api/dev/straighten-archive/undo", json={"rel_path": PAGE}).status_code == 404
    assert api_client.post("/api/dev/straighten-archive/finalize").json() == {"removed": 0}   # nothing left


def test_only_archived_scans_can_be_straightened(api_client, configured_archive):
    for rel in ("../outside.png", "2025/01/missing.png", f"{migration.BACKUP_DIR}/{PAGE}"):
        refused = api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": rel, "degrees": 3})
        assert refused.status_code == 404
    assert api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": 0}).status_code == 422
    assert api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": 40}).status_code == 422
    assert api_client.post("/api/dev/straighten-archive/undo", json={"rel_path": PAGE}).status_code == 404   # nothing to undo


def test_a_trimmed_archived_page_keeps_its_trim_and_read_band_on_the_moved_scan(api_client, configured_archive):
    page = configured_archive / PAGE
    tilt_fixture("receipt_crooked.png").save(page)
    sidecar = json.loads(page.with_suffix(".json").read_text(encoding="utf-8"))
    sidecar["trim"] = {"top": 0.1, "bottom": 0.7}
    sidecar["ocr"]["trim"] = {"top": 0.1, "bottom": 0.7}
    page.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": 3.7})
    moved = json.loads(page.with_suffix(".json").read_text(encoding="utf-8"))
    assert moved["trim"] == moved["ocr"]["trim"] != sidecar["trim"]          # both moved, the same way
    assert moved["trim"]["top"] < 0.1 + 0.05 and moved["trim"]["bottom"] > 0.7 - 0.05


def test_straightening_again_turns_the_original_by_the_total(api_client, configured_archive, tmp_path):
    """A second turn starts again from the backup: the scan is as if turned once by both, not resampled twice."""
    page = configured_archive / PAGE
    tilt_fixture("receipt_crooked.png").save(page)
    once = tmp_path / "once.png"
    tilt_fixture("receipt_crooked.png").save(once)
    deskew.straighten_file(once, 4.7)
    for degrees in (3.7, 1.0):
        api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": degrees})
    assert page.read_bytes() == once.read_bytes()
    backup = api_client.get(f"/api/dev/straighten-archive/backup/{PAGE}")
    assert backup.status_code == 200 and backup.content == (configured_archive / migration.BACKUP_DIR / PAGE).read_bytes()
    assert api_client.get("/api/dev/straighten-archive/backup/2025/01/missing.png").status_code == 404


@pytest.mark.parametrize("degrees", [3.7, -2.4])
def test_a_scan_straightened_before_turns_were_kept_is_redone_cropped_from_its_backup(api_client, configured_archive,
                                                                                    degrees):
    """The first straightenings grew the canvas and kept no turn: Redo tells the turn from the backup and the
    scan, and straightens the backup again the way it is done now, cropped to the page."""
    page = configured_archive / PAGE
    crooked = tilt_fixture("receipt_crooked.png")
    crooked.save(page)
    boxes_before = _boxes(page)
    api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": degrees})
    cropped, boxes_cropped = page.read_bytes(), _boxes(page)
    # as the first version left it: the canvas grown to keep every corner, and no turn written down
    (configured_archive / migration.BACKUP_DIR / migration.TURNS).unlink()
    crooked.rotate(degrees, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255).save(page)
    assert migration.recovered_turn(configured_archive / migration.BACKUP_DIR / PAGE, page) == pytest.approx(degrees, abs=0.05)

    done = api_client.post("/api/dev/straighten-archive/redo").json()
    assert done == {"redone": [PAGE], "skipped": []}
    with Image.open(page) as img:
        assert img.size == pytest.approx(tuple(Image.open(io.BytesIO(cropped)).size), abs=1)
    assert [b["text"] for b in _boxes(page)] == [b["text"] for b in boxes_before]
    flat = lambda boxes: [v for b in boxes for c in b["coords"] for v in c]
    assert flat(_boxes(page)) == pytest.approx(flat(boxes_cropped), abs=2)   # the boxes as a cropped straightening puts them


def test_a_scan_whose_turn_cant_be_told_is_skipped_and_left_as_it_is(api_client, configured_archive):
    page = configured_archive / PAGE
    tilt_fixture("receipt_crooked.png").save(page)
    api_client.post("/api/dev/straighten-archive/straighten", json={"rel_path": PAGE, "degrees": 3.7})
    (configured_archive / migration.BACKUP_DIR / migration.TURNS).unlink()
    tilt_fixture("flyer_crooked.png").save(page)                  # no turn of the backup is this size
    before = page.read_bytes()
    assert api_client.post("/api/dev/straighten-archive/redo").json() == {"redone": [], "skipped": [PAGE]}
    assert page.read_bytes() == before
