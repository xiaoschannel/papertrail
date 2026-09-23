"""Contract tests: media serving + path-traversal guard."""

import shutil
from urllib.parse import quote

import pytest
from fastapi import HTTPException

from api.routers.media import _safe_file


def test_serve_archived_image(api_client, configured_archive):
    rel = "2025/01/2025年1月10日 13：26 セブン-イレブン 品川駅前店.png"
    resp = api_client.get(f"/api/media/archived/{quote(rel)}")
    assert resp.status_code == 200
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature


def test_archived_missing_is_404(api_client):
    assert api_client.get("/api/media/archived/2025/01/does-not-exist.png").status_code == 404


def test_serve_input_image(api_client, configured_archive):
    scans = configured_archive.parent / "scans"  # the scan folder of the Papertrail folder conftest configures
    scans.mkdir()
    shutil.copy(configured_archive / "tossed" / "08102025143000_201.png", scans / "01102025132642_1.png")
    resp = api_client.get("/api/media/input/01102025132642_1.png")
    assert resp.status_code == 200
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert api_client.get("/api/media/input/missing.png").status_code == 404


def test_safe_file_blocks_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        _safe_file(root, "../secret.txt")
    assert exc.value.status_code == 404
