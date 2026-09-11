"""Archive/index integrity checks (extracted from the Dev pages)."""

from archive_audit import (
    batch_coverage,
    batch_statistics,
    check_archive_sidecars,
    count_duplicate_filenames,
    disk_vs_index_delta,
)
from data import scan_organized_filenames
from models import ScanBatch, ScanIndex, load_scan_index


def test_batch_coverage_full(archive_dir):
    index = load_scan_index(archive_dir)
    organized = scan_organized_filenames(archive_dir)
    rows = batch_coverage(index, organized)
    assert len(rows) == 1
    row = rows[0]
    assert row["batch_id"] == 5 and row["in_batch"] == 9
    assert row["organized"] == 9 and row["missing"] == []


def test_check_archive_sidecars_clean_then_broken(archive_dir):
    assert check_archive_sidecars(archive_dir) == []

    # Remove a sidecar -> its data file becomes orphaned.
    (archive_dir / "2025/01/2025年1月15日 09：05 セブン-イレブン 上野店.json").unlink()
    failures = check_archive_sidecars(archive_dir)
    assert len(failures) == 1
    folder, missing_sidecar, extra_sidecar = failures[0]
    assert folder == "2025/01"
    assert missing_sidecar == ["2025年1月15日 09：05 セブン-イレブン 上野店"]
    assert extra_sidecar == []


def test_batch_statistics(archive_dir):
    stats = batch_statistics(load_scan_index(archive_dir))
    assert stats["total_batches"] == 1
    assert stats["archived"] == 1 and stats["non_archived"] == 0
    assert stats["total_entries"] == 9 and stats["unique_filenames"] == 9
    assert stats["lost_to_dedup"] == 0
    assert stats["per_batch"][0]["running_total"] == 9


def test_count_duplicate_filenames():
    index = ScanIndex(batches=[
        ScanBatch(batch_id=1, start_datetime="x", end_datetime="y", files={1: "a.png", 2: "b.png"}),
        ScanBatch(batch_id=2, start_datetime="x", end_datetime="y", files={1: "a.png"}),
    ])
    assert count_duplicate_filenames(index) == {"a.png": 2}


def test_disk_vs_index_delta():
    indexed_only, disk_only = disk_vs_index_delta({"on_disk.png", "shared.png"}, {"shared.png", "indexed.png"})
    assert indexed_only == {"indexed.png"}
    assert disk_only == {"on_disk.png"}
