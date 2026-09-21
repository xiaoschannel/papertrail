"""Telling sideways and upside-down scans apart, on the printed pages in tests/fixtures/orientation.

These run the real orientation model, fetched into the user cache the first time (as the app does).
"""

import hashlib
import http.client
import io
import json
import urllib.error
import zipfile
from pathlib import Path

import pytest
from PIL import Image

import orientation
from document_grouping import ROTATIONS, rotate_upright

PAGES = Path(__file__).resolve().parent / "fixtures" / "orientation"
KINDS = json.loads((PAGES / "pages.json").read_text(encoding="utf-8"))
#: How to make an upright page face each way: the turn its rotate arrow undoes.
FACING = {"left": Image.Transpose.ROTATE_90, "right": Image.Transpose.ROTATE_270, "down": Image.Transpose.ROTATE_180}


def page(name: str) -> Image.Image:
    with Image.open(PAGES / name) as img:
        img.load()
        return img


@pytest.mark.parametrize("name", KINDS["printed"])
@pytest.mark.parametrize("top_points", FACING)
def test_a_turned_scan_is_found_with_the_arrow_that_turns_it_upright(name, top_points):
    upright = page(name)
    turned = upright.transpose(FACING[top_points])
    estimate = orientation.estimate_orientation(turned)
    assert estimate.top_points == top_points and estimate.needs_turning
    assert rotate_upright(turned, estimate.top_points).tobytes() == upright.tobytes()   # the arrow undoes it


@pytest.mark.parametrize("name", KINDS["printed"])
def test_faded_print_is_still_read(name):
    """Thermal paper fades to grey on grey: without stretching its contrast there is no ink to go by."""
    faded = page(name).convert("L").point(lambda v: 150 + v * 80 // 255)          # ink 150, paper 230
    estimate = orientation.estimate_orientation(faded.transpose(FACING["down"]))
    assert estimate.top_points == "down" and estimate.needs_turning


@pytest.mark.parametrize("name", KINDS["printed"])
def test_an_upright_scan_is_left_alone(name):
    estimate = orientation.estimate_orientation(page(name))
    assert estimate.top_points is None and not estimate.needs_turning


@pytest.mark.parametrize("name", KINDS["printed"])
def test_a_slightly_tilted_upright_scan_is_still_upright(name):
    """A crooked feed is levelled, not turned: an upright page a few degrees off wants no arrow."""
    tilted = page(name).convert("L").rotate(5, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    assert not orientation.estimate_orientation(tilted).needs_turning


@pytest.mark.parametrize("name", KINDS["blank"])
@pytest.mark.parametrize("turn", [None, *FACING.values()])
def test_the_back_of_a_page_is_never_flagged(name, turn):
    """Faint shading and specks: stretched to full contrast they would read as ink, so they aren't."""
    blank = page(name)
    assert not orientation.estimate_orientation(blank if turn is None else blank.transpose(turn)).needs_turning


def test_an_empty_page_has_no_orientation():
    assert orientation.estimate_orientation(Image.new("L", (300, 600), 255)) == \
        orientation.OrientationEstimate(None, 0.0)


def test_every_answer_is_a_rotate_arrow():
    assert {t for t in orientation._TOP_POINTS if t} == set(ROTATIONS)


def test_estimate_file_orientation_reads_the_scan(tmp_path):
    path = tmp_path / "scan.png"
    page("receipt.png").transpose(FACING["down"]).save(path)
    assert orientation.estimate_file_orientation(path).top_points == "down"


# --- getting the model -----------------------------------------------------------------------------------
@pytest.fixture
def empty_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(orientation, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache"


def _wheel(tmp_path, member=orientation.MODEL_MEMBER, model=b"not a model"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as wheel:
        wheel.writestr(member, model)
    path = tmp_path / "fake.whl"
    path.write_bytes(buffer.getvalue())
    return path


def test_the_model_is_downloaded_checked_and_cached_once(empty_cache, monkeypatch):
    path = orientation.fetch_model()
    assert path.parent.parent == empty_cache
    assert hashlib.sha256(path.read_bytes()).hexdigest() == orientation.MODEL_SHA256
    assert [p.name for p in path.parent.iterdir()] == [path.name]      # no partial file left beside it

    def offline(*args, **kwargs):
        raise AssertionError("fetched again")
    monkeypatch.setattr(orientation.urllib.request, "urlopen", offline)
    assert orientation.fetch_model() == path


def test_a_download_that_fails_says_why_and_caches_nothing(empty_cache, monkeypatch):
    def unreachable(*args, **kwargs):
        raise urllib.error.URLError("no network")
    monkeypatch.setattr(orientation.urllib.request, "urlopen", unreachable)
    with pytest.raises(orientation.ModelUnavailable) as refused:
        orientation.fetch_model()
    assert str(refused.value) == "couldn't download the orientation model: no network"
    assert not empty_cache.exists()


def test_a_download_cut_short_says_so(empty_cache, monkeypatch):
    class CutShort:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"partial", 100)
    monkeypatch.setattr(orientation.urllib.request, "urlopen", lambda *args, **kwargs: CutShort())
    with pytest.raises(orientation.ModelUnavailable, match="couldn't download"):
        orientation.fetch_model()
    assert not empty_cache.exists()


@pytest.mark.parametrize("content", [b"", b"not a png"])
def test_a_scan_that_cant_be_read_says_so(tmp_path, content):
    path = tmp_path / "scan.png"
    path.write_bytes(content)
    with pytest.raises(orientation.UNREADABLE):
        orientation.estimate_file_orientation(path)


def test_a_download_that_is_not_the_pinned_wheel_is_refused(empty_cache, tmp_path):
    with pytest.raises(orientation.ModelUnavailable, match="isn't the expected file"):
        orientation.fetch_model(_wheel(tmp_path).as_uri())
    assert not empty_cache.exists()


@pytest.mark.parametrize("member", [orientation.MODEL_MEMBER, "elsewhere.onnx"])
def test_a_wheel_without_the_pinned_model_is_refused(empty_cache, tmp_path, monkeypatch, member):
    """Past the wheel's own hash: the model inside must be the pinned one too, and be there at all."""
    wheel = _wheel(tmp_path, member)
    monkeypatch.setattr(orientation, "WHEEL_SHA256", hashlib.sha256(wheel.read_bytes()).hexdigest())
    with pytest.raises(orientation.ModelUnavailable, match="isn't the expected file"):
        orientation.fetch_model(wheel.as_uri())
    assert not empty_cache.exists()
