"""Archive naming + reorganize planning — including collision suffixing."""

from organize_utils import (
    apply_reorganize,
    build_accepted_name,
    parse_scan_datetime,
    plan_accepted_destinations,
    resolve_single_accepted_destination,
    sanitize_filename,
)
from models import ReviewDecision, Sidecar

COLON = "："  # FULLWIDTH_COLON used in archive filenames


def _dec(**kw) -> ReviewDecision:
    base = dict(verdict="accepted", document_type="receipt", name="Shop",
                date="2025-01-10", time="13:26:00", cost=100.0, currency="JPY")
    base.update(kw)
    return ReviewDecision(**base)


def test_sanitize_filename_replaces_forbidden_chars():
    out = sanitize_filename('a/b:c?<d>')
    for ch in '/:?<>':
        assert ch not in out
    assert sanitize_filename("trailing. ") == "trailing"


def test_parse_scan_datetime():
    assert parse_scan_datetime("01102025132642_7.png") == (2025, 1, 10, 13, 26, 42)


def test_build_accepted_name_dated():
    folder, base, seconds = build_accepted_name(_dec(time="13:26:42", name="セブン-イレブン 品川駅前店"),
                                                "01102025132642_1.png")
    assert folder == "2025/01"
    assert base == f"2025年1月10日 13{COLON}26 セブン-イレブン 品川駅前店"
    assert seconds == 42


def test_build_accepted_name_undated_uses_scan_filename():
    folder, base, _sec = build_accepted_name(_dec(date="", time="", name="ATM"),
                                             "01012025142000_108.png")
    assert folder == "2025/undated"
    assert base == f"2025年1月1日 14{COLON}20 ATM"


def test_plan_destinations_suffixes_collisions():
    dec = _dec(name="Shop", time="13:26:00")
    dests = plan_accepted_destinations({"a.png": dec, "b.png": dec}, existing_names_by_folder={})
    base = f"2025/01/2025年1月10日 13{COLON}26 Shop"
    assert dests["a.png"] == f"{base}.png"
    assert dests["b.png"] == f"{base} (2).png"


def test_plan_destinations_continues_after_existing():
    dec = _dec(name="Shop", time="13:26:00")
    base_stem = f"2025年1月10日 13{COLON}26 Shop"
    dests = plan_accepted_destinations(
        {"a.png": dec, "b.png": dec},
        existing_names_by_folder={"2025/01": {base_stem}},
    )
    assert dests["a.png"].endswith(f"{base_stem} (2).png")
    assert dests["b.png"].endswith(f"{base_stem} (3).png")


def test_resolve_single_destination_avoids_existing(archive_dir):
    dec = _dec(time="13:26:00", name="セブン-イレブン 品川駅前店")  # base already on disk
    dest = resolve_single_accepted_destination(archive_dir, "x.png", dec)
    assert dest == f"2025/01/2025年1月10日 13{COLON}26 セブン-イレブン 品川駅前店 (2).png"


def test_apply_reorganize_noop_on_clean_archive(archive_dir):
    assert apply_reorganize(archive_dir) == []


def test_apply_reorganize_moves_misfiled_document(archive_dir):
    # Plant a doc in the wrong folder; reorganize should relocate it to its
    # canonical YYYY/MM/<base> destination.
    wrong_dir = archive_dir / "2099" / "12"
    wrong_dir.mkdir(parents=True)
    (wrong_dir / "wrongname.png").write_bytes(b"\x89PNG\r\n")
    sidecar = Sidecar(
        original_filename="x.png", batch_id=9, serial=1,
        review=ReviewDecision(verdict="accepted", document_type="receipt",
                              name="Moved Shop", date="2099-12-31", time="",
                              cost=10.0, currency="JPY"),
    )
    (wrong_dir / "wrongname.json").write_text(sidecar.model_dump_json(), encoding="utf-8")

    moves = apply_reorganize(archive_dir)
    moved = [m for m in moves if m[0] == "x.png"]
    assert moved, "expected the misfiled document to be relocated"
    _fn, _old, new = moved[0]
    assert new.startswith("2099/12/2099年12月31日 00：00 Moved Shop")
    assert (archive_dir / new).exists()
