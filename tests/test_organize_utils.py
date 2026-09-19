"""Archive naming + reorganize planning — including collision suffixing."""

from organize_utils import (
    apply_reorganize,
    build_accepted_name,
    move_to_accepted_destination,
    parse_scan_datetime,
    plan_accepted_destinations,
    resolve_single_accepted_destination,
    sanitize_filename,
)
from data import sidecar_path_for
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


# --- move_to_accepted_destination (used by the Streamlit workshop and receipt pages) ---------
def _decision(**changes):
    values = dict(verdict="accepted", document_type="receipt", name="Some Shop", date="2025-03-15",
                  time="12:30", cost=100.0, currency="JPY", comment="")
    values.update(changes)
    return ReviewDecision(**values)


def test_move_to_accepted_destination_files_the_scan_and_drops_the_old_sidecar(tmp_path):
    marked = tmp_path / "marked"
    marked.mkdir()
    source = marked / "03152025123000_7.png"
    source.write_bytes(b"scan")
    sidecar_path_for(source).write_text("{}", encoding="utf-8")

    target = move_to_accepted_destination(tmp_path, source.name, source, _decision())

    assert target.parent == tmp_path / "2025" / "03"
    assert target.exists() and not source.exists()
    assert not sidecar_path_for(source).exists()     # the old sidecar never lingers in marked/
    assert "Some Shop" in target.name


def test_move_to_accepted_destination_is_a_noop_when_it_is_already_there(tmp_path):
    month = tmp_path / "2025" / "03"
    month.mkdir(parents=True)
    decision = _decision()
    _folder, base, _ = build_accepted_name(decision, "03152025123000_7.png")
    resident = month / f"{base}.png"
    resident.write_bytes(b"scan")
    sidecar_path_for(resident).write_text("{}", encoding="utf-8")

    target = move_to_accepted_destination(tmp_path, "03152025123000_7.png", resident, decision)

    assert target == resident and resident.exists() and sidecar_path_for(resident).exists()


def test_move_to_accepted_destination_steps_around_a_name_in_use(tmp_path):
    month = tmp_path / "2025" / "03"
    month.mkdir(parents=True)
    decision = _decision()
    _folder, base, _ = build_accepted_name(decision, "03152025123000_7.png")
    other = month / f"{base}.png"
    other.write_bytes(b"another document")
    sidecar_path_for(other).write_text("{}", encoding="utf-8")

    source = tmp_path / "marked" / "03152025124500_8.png"
    source.parent.mkdir()
    source.write_bytes(b"scan")

    target = move_to_accepted_destination(tmp_path, source.name, source, decision)

    assert target.name == f"{base} (2).png"
    assert other.read_bytes() == b"another document"


def test_reorganize_never_overwrites_a_scan_it_cannot_see(tmp_path):
    """A file with no sidecar is invisible to the archive, but its bytes still matter."""
    from data import write_sidecar
    from models import OcrResult

    month = tmp_path / "2025" / "03"
    month.mkdir(parents=True)
    stale = month / "wrong name.png"
    stale.write_bytes(b"the document")
    decision = _decision()
    write_sidecar(stale, Sidecar(original_filename="03152025123000_7.png", batch_id=1, serial=7, review=decision,
                                 ocr=OcrResult(markdown="x", succeeded=True)))
    _folder, base, _ = build_accepted_name(decision, "03152025123000_7.png")
    orphan = month / f"{base}.png"                     # exactly where the document wants to go
    orphan.write_bytes(b"an orphan nobody can see")

    moves = apply_reorganize(tmp_path)

    assert orphan.read_bytes() == b"an orphan nobody can see"
    [(_fn, _old, new)] = moves
    assert new.endswith("(2).png") and (tmp_path / new).read_bytes() == b"the document"
    assert (tmp_path / new).with_suffix(".json").exists()   # its sidecar came along
