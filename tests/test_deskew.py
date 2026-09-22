"""Tilt detection and straightening, on the tilt fixtures (tests/fixtures/tilt, from tools/build_fixture.py)."""

import pytest
from PIL import Image

import deskew
from models import Trim
from tilt_scans import BLANK_BACK, PAGES, scan, tilt

CROOKED = [name for name, page in PAGES.items() if abs(page["fed_at"]) >= deskew.MIN_DEGREES]
#: A page of each kind to turn further while a test runs: the level receipt, and the crooked ones levelled.
LEVEL = ["receipt_level.png", *CROOKED]


def level(name: str) -> Image.Image:
    return tilt(scan(name), -PAGES[name]["fed_at"])


@pytest.mark.parametrize("name", PAGES)
def test_each_fixture_is_found_as_it_was_fed_in(name):
    """The fixtures are black and white, as some scanners save them: all ink is at the threshold's value."""
    fed_at = PAGES[name]["fed_at"]
    estimate = deskew.estimate_skew(scan(name))
    assert estimate.degrees == pytest.approx(-fed_at, abs=0.15)
    assert estimate.needs_straightening == (abs(fed_at) >= deskew.MIN_DEGREES)


@pytest.mark.parametrize("name", LEVEL)
@pytest.mark.parametrize("fed_at", [-10.0, -5.0, -1.8, -1.1, 1.1, 1.6, 8.3, 12.0])
def test_a_crooked_feed_is_found_with_the_turn_that_levels_it(name, fed_at):
    estimate = deskew.estimate_skew(tilt(level(name), fed_at))
    assert estimate.degrees == pytest.approx(-fed_at, abs=0.2)
    assert estimate.needs_straightening


@pytest.mark.parametrize("name", LEVEL)
@pytest.mark.parametrize("fed_at", [0.0, 0.4, -0.5])
def test_a_level_or_barely_tilted_scan_is_left_alone(name, fed_at):
    """Under half a degree the fixtures are too small to place the lines exactly; only the verdict counts."""
    estimate = deskew.estimate_skew(tilt(level(name), fed_at))
    assert abs(estimate.degrees) < deskew.MIN_DEGREES
    assert not estimate.needs_straightening


@pytest.mark.parametrize("fed_at", [0.0, -3.7, 7.0])
def test_a_blank_page_is_never_flagged(fed_at):
    """Specks have no lines to level: whatever angle scores best, it is no better than as scanned."""
    with Image.open(BLANK_BACK) as back:
        assert not deskew.estimate_skew(tilt(back, fed_at)).needs_straightening


@pytest.mark.parametrize("size", [(1, 1), (2, 40), (400, 10)])
def test_a_scan_too_small_to_measure_has_no_tilt(size):
    """A placeholder or a sliver, even all ink, has no lines to level (and mustn't make OpenCV refuse)."""
    assert deskew.estimate_skew(Image.new("L", size, 0)) == deskew.SkewEstimate(degrees=0.0, gain=1.0, within_range=True)


def test_an_empty_page_has_no_tilt():
    assert deskew.estimate_skew(Image.new("L", (300, 600), 255)) ==         deskew.SkewEstimate(degrees=0.0, gain=1.0, within_range=True)


@pytest.mark.parametrize("name", LEVEL)
def test_a_sideways_scan_is_for_the_rotate_arrows_not_straightening(name):
    assert not deskew.estimate_skew(tilt(level(name), 90)).needs_straightening


def test_a_best_angle_at_the_edge_of_the_search_is_not_trusted():
    at_edge = deskew.estimate_skew(tilt(level("receipt_level.png"), -deskew.MAX_DEGREES - 3))
    assert not at_edge.within_range and not at_edge.needs_straightening


@pytest.mark.parametrize("name", CROOKED)
def test_straightening_by_the_estimate_levels_the_page(name):
    crooked = scan(name)
    straight = deskew.straighten(crooked, deskew.estimate_skew(crooked).degrees)
    assert straight.mode == crooked.mode
    assert abs(deskew.estimate_skew(straight).degrees) <= 0.2


@pytest.mark.parametrize("name", CROOKED)
def test_straightening_crops_to_the_page_as_it_was_printed(name):
    """A crooked feed's scan is the page's bounding box; levelled, it is the page again, its size as printed
    (the level fixture of the same page), with no corners of scanner background swung out around it."""
    crooked = scan(name)
    straight = deskew.straighten(crooked, -PAGES[name]["fed_at"])
    printed = PAGES[name]["printed"]
    assert straight.width == pytest.approx(printed[0], abs=3) and straight.height == pytest.approx(printed[1], abs=3)


def test_straightening_keeps_the_page_to_its_corners_and_drops_the_background_around_it():
    page = Image.new("L", (200, 400), 230)                # a grey page, with ink right in its corner
    page.paste(0, (0, 0, 20, 20))
    crooked = tilt(page, 5)                               # on white: the scanner's bed around the page
    straight = deskew.straighten(crooked, -5)
    assert straight.size == pytest.approx((200, 400), abs=5)   # the corner's ink keeps its margin
    assert straight.getpixel((10, 10)) < 60               # the page's own corner is kept
    w, h = straight.size
    assert all(210 <= straight.getpixel(xy) <= 245 for xy in [(w - 8, 8), (8, h - 8), (w - 8, h - 8)])   # page, not bed


def _black(img: Image.Image) -> int:
    return sum(1 for v in img.convert("L").getdata() if v < 128)


@pytest.mark.parametrize("fed_at", [-3.0, 2.5])
@pytest.mark.parametrize("cut", ["left", "right"])
def test_a_scan_cut_through_its_page_keeps_all_its_ink(tmp_path, fed_at, cut):
    """A receipt wider than the scanner took: the scan cuts through the page, so it isn't the page's bounding
    box, and the page it seems to hold is narrower than the ink. The crop stops at the ink, whatever the
    trim then has to follow."""
    page = Image.new("L", (480, 1000), 255)
    for y in range(40, 960, 40):
        page.paste(0, (4, y, 200, y + 14))              # text from the page's very edge, both sides
        page.paste(0, (280, y, 476, y + 14))
    crooked = tilt(page, fed_at)
    crooked = crooked.crop((24, 0, crooked.width, crooked.height) if cut == "left"
                           else (0, 0, crooked.width - 24, crooked.height))
    path = tmp_path / "scan.png"
    crooked.save(path)
    ink = _black(crooked)
    _, _, shift = deskew.straighten_file(path, -fed_at)
    with Image.open(path) as img:
        assert _black(img) >= 0.97 * ink                     # nothing cut off (resampling blurs a little)
    assert shift != (0.0, 0.0)                               # held back on one side: not centred


def test_the_ink_outline_holds_the_ink_and_leaves_out_specks():
    page = Image.new("L", (400, 600), 255)
    page.paste(0, (100, 150, 300, 450))
    page.putpixel((10, 10), 0)                              # a speck of dust
    outline = deskew.ink_outline(page)
    xs, ys = [x for x, _ in outline], [y for _, y in outline]
    assert min(xs) == pytest.approx(0.25, abs=0.01) and max(xs) == pytest.approx(0.75, abs=0.01)
    assert min(ys) == pytest.approx(0.25, abs=0.01) and max(ys) == pytest.approx(0.75, abs=0.01)
    assert deskew.ink_outline(Image.new("L", (400, 600), 255)) == []


def test_a_scan_no_page_fits_keeps_every_corner_and_fills_with_the_scanner_background():
    """A sliver turned further than its shape allows can't be a crooked page's bounding box: nothing is cut."""
    grey_bed = Image.new("L", (20, 400), 230)
    grey_bed.paste(0, (0, 0, 20, 20))
    assert deskew.levelled_size(grey_bed.size, 5) is None
    turned = deskew.straighten(grey_bed, 5)
    assert turned.width > 20 and turned.height > 400
    assert turned.getpixel((0, 0)) == 230                # a new corner: background, not black or white
    assert min(turned.getdata()) == 0                    # the corner's ink is still there


@pytest.mark.parametrize(("suffix", "image_format"), [(".png", "PNG"), (".jpg", "JPEG")])
def test_straighten_file_rewrites_the_scan_in_its_own_format(tmp_path, suffix, image_format):
    path = tmp_path / f"scan{suffix}"
    scan("receipt_crooked.png").save(path, format=image_format)
    deskew.straighten_file(path, 3.7)
    with Image.open(path) as img:
        assert img.format == image_format
        assert abs(deskew.estimate_skew(img).degrees) <= 0.2
    assert [p.name for p in tmp_path.iterdir()] == [path.name]   # no temporary file left behind


def test_straighten_file_refuses_a_sideways_turn_and_skips_a_zero_one(tmp_path):
    path = tmp_path / "scan.png"
    scan("receipt_level.png").save(path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        deskew.straighten_file(path, 90)
    deskew.straighten_file(path, 0)
    assert path.read_bytes() == before


def test_a_zero_turn_changes_nothing_and_reports_no_sizes(tmp_path):
    path = tmp_path / "scan.png"
    scan("receipt_level.png").save(path)
    assert deskew.straighten_file(path, 0) is None


def _ink_rows(img: Image.Image, start: float = 0.0, end: float = 1.0) -> tuple[float, float]:
    """Where a scan's ink starts and ends between two heights, as fractions of the whole scan's height."""
    part = img.convert("L")
    part.paste(255, (0, 0, img.width, round(start * img.height)))
    part.paste(255, (0, round(end * img.height), img.width, img.height))
    _, top, _, bottom = Image.eval(part, lambda v: 255 if v < 128 else 0).getbbox()
    return top / img.height, bottom / img.height


@pytest.mark.parametrize("fed_at", [-8.0, -2.5, 3.0, 7.0])
def test_a_trim_moves_with_the_scan_and_keeps_all_it_held(tmp_path, fed_at):
    """A receipt with a coupon under it, trimmed just below the receipt: once straightened, the band still
    holds the whole receipt and still leaves the coupon out."""
    page = Image.new("L", (480, 1000), 255)
    page.paste(0, (10, 150, 470, 520))            # the receipt, near the full width, where a sloping cut differs most
    page.paste(0, (60, 800, 420, 900))            # a coupon under it
    path = tmp_path / "scan.png"
    tilt(page, fed_at).save(path)
    with Image.open(path) as img:
        receipt_rows = _ink_rows(img, end=0.66)
    band = Trim(top=0.0, bottom=round(receipt_rows[1] + 0.005, 4))  # cut just below the receipt, top uncut
    before, after, shift = deskew.straighten_file(path, -fed_at)
    moved = deskew.straightened_trim(band, -fed_at, before, after, shift)
    with Image.open(path) as img:
        receipt = _ink_rows(img, end=0.66)
        coupon_top = _ink_rows(img, start=0.66)[0]
    assert moved.top == 0.0                                       # an uncut edge stays the page's edge
    assert receipt[1] <= moved.bottom < coupon_top                # all of the receipt, none of the coupon
