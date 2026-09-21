"""The one-off Turn Archive migration: finding turned scans in the archive, turning one, putting it back."""

import json
import time
from pathlib import Path

import pytest
from PIL import Image

import orientation
import orientation_migration as migration

PRINTED = Path(__file__).resolve().parent / "fixtures" / "orientation"
#: A filed page whose sidecar has an OCR box, in the archive fixture.
PAGE = "2025/01/2025年1月10日 13：26 セブン-イレブン 品川駅前店.png"
BOX = [80, 40, 880, 104]


def _scan(client):
    state = client.post("/api/dev/turn-archive/scan").json()
    deadline = time.monotonic() + 60
    while state["running"]:
        assert time.monotonic() < deadline, "the look through the archive didn't finish"
        time.sleep(0.1)
        state = client.get("/api/dev/turn-archive").json()
    return state


@pytest.mark.parametrize("coords", [[80, 40, 880, 104], [0, 0, 1000, 1000], [123, 456, 789, 999]])
def test_a_box_turned_each_way_and_back_is_where_it_was(coords):
    back = {"left": "right", "right": "left", "down": "down"}
    for top, undo in back.items():
        assert migration.turned_box(migration.turned_box(coords, top), undo) == coords


def test_turned_archived_scans_are_found_turned_with_their_boxes_and_put_back(api_client, configured_archive):
    page = configured_archive / PAGE
    with Image.open(PRINTED / "receipt.png") as upright:
        upright.transpose(Image.Transpose.ROTATE_180).save(page)
    before = page.read_bytes(), page.with_suffix(".json").read_bytes()

    state = _scan(api_client)
    assert state["error"] is None and state["total"] > 5 and state["done"] == state["total"]
    assert [(f["rel_path"], f["top_points"]) for f in state["found"]] == [(PAGE, "down")]   # 1x1 placeholders aren't
    assert page.read_bytes() == before[0]                                                  # looking changes nothing

    turned = api_client.post("/api/dev/turn-archive/turn", json={"rel_path": PAGE, "top_points": "down"}).json()
    assert turned["found"] == [] and turned["turned"] == [PAGE]
    assert orientation.estimate_file_orientation(page).top_points is None                 # upright now
    sidecar = json.loads(page.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["ocr"]["boxes"][0]["coords"] == [[120, 896, 920, 960]]                 # the box turned with it
    assert api_client.get(f"/api/dev/turn-archive/thumb/{PAGE}").headers["content-type"] == "image/jpeg"

    put_back = api_client.post("/api/dev/turn-archive/undo", json={"rel_path": PAGE}).json()
    assert put_back["turned"] == [] and [f["rel_path"] for f in put_back["found"]] == [PAGE]   # offered again
    assert (page.read_bytes(), page.with_suffix(".json").read_bytes()) == before           # exactly as it was
    assert not any((configured_archive / migration.BACKUP_DIR).rglob("*.*"))


def test_only_archived_scans_can_be_turned(api_client, configured_archive):
    for rel in ("../outside.png", "2025/01/missing.png", f"{migration.BACKUP_DIR}/{PAGE}"):
        assert api_client.post("/api/dev/turn-archive/turn", json={"rel_path": rel, "top_points": "down"}).status_code == 404
    assert api_client.post("/api/dev/turn-archive/undo", json={"rel_path": PAGE}).status_code == 404   # nothing to undo
