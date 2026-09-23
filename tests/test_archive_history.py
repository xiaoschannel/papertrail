"""The folder's history (archive_history): a git repository at the Papertrail folder's root, committed at
milestones with only what each produced."""

import subprocess
from pathlib import Path

import pytest

import archive_history as history


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def _folder(tmp_path: Path) -> Path:
    """A Papertrail folder: a scan in scans/, a page in archive/."""
    root = tmp_path / "papertrail"
    (root / "scans").mkdir(parents=True)
    (root / "scans" / "01102025132642_1.png").write_bytes(b"\x89PNG scan")
    (root / "archive" / "2025" / "01").mkdir(parents=True)
    (root / "archive" / "2025" / "01" / "page.json").write_bytes(b'{"a": 1}\r\n')   # a sidecar as Windows writes it
    (root / "archive" / "2025" / "01" / "page.png").write_bytes(b"\x89PNG scan")
    return root


def test_the_repository_starts_with_its_settings_committed(tmp_path):
    root = _folder(tmp_path)
    assert history.ensure_repository(root) and not history.ensure_repository(root)
    first = history.head(root)
    assert first is not None and first.subject == "History started"
    assert _git(root, "ls-files").splitlines() == [".gitattributes", ".gitignore"]
    assert _git(root, "log", "--format=%an <%ae>") == "Papertrail <papertrail@localhost>"
    assert (_git(root, "config", "core.autocrlf"), _git(root, "config", "core.quotepath")) == ("false", "false")
    assert "*.png -delta" in (root / ".gitattributes").read_text(encoding="utf-8")


def test_a_milestone_commits_only_its_own_paths(tmp_path):
    root = _folder(tmp_path)
    (root / "archive" / "decisions.json").write_bytes(b"{}")                      # someone's uncommitted work

    sha = history.commit(root, "File Index: batch 1 (1 scan)", ["scans/01102025132642_1.png", "archive/batches.json"])

    assert sha and history.head(root).sha == sha                                    # a missing path is simply nothing
    assert _git(root, "ls-files").splitlines() == [".gitattributes", ".gitignore", "scans/01102025132642_1.png"]
    assert history.changed_paths(root) == {"archive/2025/01/page.json", "archive/2025/01/page.png",
                                            "archive/decisions.json"}
    assert history.commit(root, "again", ["scans/01102025132642_1.png"]) is None   # nothing of its own changed
    assert history.status(root).changed == 3


def test_files_are_committed_byte_for_byte_and_temp_files_ignored(tmp_path):
    root = _folder(tmp_path)
    (root / "archive" / "page.json.abcd.tmp").write_bytes(b"half")               # data.atomic_write_text, mid-write
    (root / "archive" / "2025" / "01" / "x.png.partial").write_bytes(b"half")     # Archive, mid-copy
    (root / "archive" / "name_embeddings.npz").write_bytes(b"cache")

    history.commit(root, "everything")

    assert history.status(root).changed == 0
    tracked = history.tracked_paths(root, "archive")
    assert tracked == {"archive/2025/01/page.json", "archive/2025/01/page.png"}
    committed = subprocess.run(["git", "-C", str(root), "show", "HEAD:archive/2025/01/page.json"], check=True,
                               capture_output=True).stdout
    assert committed == b'{"a": 1}\r\n'                                            # line endings left alone


def test_a_page_whose_name_looks_like_a_pattern_is_one_file(tmp_path):
    root = _folder(tmp_path)
    odd = root / "archive" / "2025" / "01" / "Foo [2].png"
    odd.write_bytes(b"x")
    (root / "archive" / "2025" / "01" / "Foo 2.png").write_bytes(b"y")

    history.commit(root, "one", ["archive/2025/01/Foo [2].png"])

    assert history.tracked_paths(root, "archive") == {"archive/2025/01/Foo [2].png"}


def test_a_folder_inside_another_repository_gets_a_repository_of_its_own(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    root = _folder(tmp_path)

    history.commit(root, "first")

    assert (root / ".git").is_dir()
    assert _git(tmp_path, "status", "--porcelain").splitlines() == ["?? papertrail/"]   # the outer one untouched


def test_without_git_it_says_so(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    monkeypatch.setattr(history, "GIT", "git-that-is-not-installed")
    with pytest.raises(history.HistoryError, match="isn't installed"):
        history.commit(root, "first")
    assert not (root / ".git").exists()
    assert history.head(root) is None and history.status(root).repository is False


def test_parking_at_shutdown_is_undone_at_the_next_start(tmp_path):
    root = _folder(tmp_path)
    history.commit(root, "File Index: batch 1 (1 scan)", ["scans"])
    (root / "archive" / "decisions.json").write_bytes(b"{}")                      # Review, not a milestone

    assert history.park(root) and history.head(root).subject == history.PARKED
    assert history.status(root).changed == 0
    assert history.park(root) is None                                              # nothing more to park

    assert history.unpark(root) is True
    assert history.head(root).subject == "File Index: batch 1 (1 scan)"
    assert history.changed_paths(root) == {"archive/2025/01/page.json", "archive/2025/01/page.png",
                                            "archive/decisions.json"}
    assert (root / "archive" / "decisions.json").read_bytes() == b"{}"             # the work is still there
    assert history.unpark(root) is False                                           # only a parking commit is undone


# --- looking back, and taking back ----------------------------------------------------------------------------
def test_the_log_lists_commits_newest_first_with_what_each_changed(tmp_path):
    root = _folder(tmp_path)
    history.commit(root, "File Index: batch 1 (1 scan)", ["scans"])
    history.commit(root, "Archive: 2 pages", ["archive"])
    (root / "scans" / "01102025132642_1.png").unlink()
    (root / "archive" / "2025" / "01" / "page.json").write_bytes(b'{"a": 2}')
    history.commit(root, "Archive: 1 scan cleared out of the scan folder")

    commits, more = history.log(root)
    assert [(c.subject, c.files, c.first) for c in commits] == [
        ("Archive: 1 scan cleared out of the scan folder", 2, False), ("Archive: 2 pages", 2, False),
        ("File Index: batch 1 (1 scan)", 1, False), ("History started", 2, True)]
    assert more is False and commits[0].sha == history.head(root).sha
    page, more = history.log(root, skip=1, limit=2)
    assert [c.subject for c in page] == ["Archive: 2 pages", "File Index: batch 1 (1 scan)"] and more is True

    # lines added and removed in each text file; an image has none
    assert history.commit_files(root, commits[0].sha) == [
        history.Change("archive/2025/01/page.json", "modified", (1, 1)),
        history.Change("scans/01102025132642_1.png", "deleted", None)]
    assert history.commit_files(root, commits[1].sha) == [
        history.Change("archive/2025/01/page.json", "added", (1, 0)), history.Change("archive/2025/01/page.png", "added")]
    assert history.commit_files(root, commits[3].sha) == [
        history.Change(".gitattributes", "added", (len(history.GITATTRIBUTES.splitlines()), 0)),
        history.Change(".gitignore", "added", (len(history.GITIGNORE.splitlines()), 0))]
    assert history.commit_files(root, "0000000") is None and history.commit_files(root, "--all") is None


def test_uncommitted_changes_say_what_happened_to_each_file(tmp_path):
    root = _folder(tmp_path)
    assert history.changes(root) == []                              # no history yet
    history.commit(root, "everything")
    (root / "archive" / "2025" / "01" / "page.json").write_bytes(b'{"a": 2}')
    (root / "scans" / "01102025132642_1.png").unlink()
    (root / "scans" / "01102025132642_2.png").write_bytes(b"\x89PNG new scan")
    (root / "archive" / "notes.json").write_bytes(b'{\r\n  "a": 1\r\n}')          # new: every line is added
    (root / "archive" / "cache.bin").write_bytes(b"\x00\x01")                      # new, and binary by its bytes
    assert history.changes(root) == [history.Change("archive/2025/01/page.json", "modified", (1, 1)),
                                     history.Change("archive/cache.bin", "added", None),
                                     history.Change("archive/notes.json", "added", (3, 0)),
                                     history.Change("scans/01102025132642_1.png", "deleted"),
                                     history.Change("scans/01102025132642_2.png", "added")]


def test_files_a_failed_commit_left_in_the_index_are_listed_counted_and_discarded(tmp_path, monkeypatch):
    """A milestone's ``git add`` ran and its ``git commit`` didn't (a lock left behind, say): the files wait in the
    index, in no commit. Deleted since, one is in neither the last commit nor the folder."""
    root = _folder(tmp_path)
    history.commit(root, "everything")
    staged, gone = root / "archive" / "notes.json", root / "archive" / "gone.json"
    staged.write_bytes(b"a\nb\n")
    gone.write_bytes(b"a\n")
    history._git(root, "add", "--", "archive/notes.json", "archive/gone.json")
    gone.unlink()
    staged.write_bytes(b"a\nb\nc\n")                                               # and edited after the add
    asked = []
    numstat = history._numstat
    monkeypatch.setattr(history, "_numstat", lambda r, command, paths: asked.extend(paths) or numstat(r, command, paths))

    assert history.changes(root) == [history.Change("archive/gone.json", "deleted", (0, 0)),
                                     history.Change("archive/notes.json", "added", (3, 0))]
    assert asked == ["archive/gone.json"]           # a file no commit holds is counted from the file, not a diff

    assert history.discard_changes(root, ["archive/notes.json", "archive/gone.json"]) == 2
    assert not staged.exists() and history.changes(root) == []
    assert _git(root, "status", "--porcelain") == ""                                 # the index is clean again


def test_lines_are_counted_for_more_files_than_one_command_line_can_name(tmp_path):
    root = _folder(tmp_path)
    history.commit(root, "everything")
    folder = root / "archive" / "2025" / "01"
    names = [f"2025年1月{n:03d}日 00：00 A receipt from a shop with a rather long name.json" for n in range(400)]
    for name in names:
        (folder / name).write_bytes(b"{\n}\n")
    sha = history.commit(root, "400 sidecars")
    for name in names:
        (folder / name).write_bytes(b"{\n  \"a\": 1\n}\n")

    assert {c.lines for c in history.changes(root)} == {(1, 0)} and len(history.changes(root)) == 400
    assert {c.lines for c in history.commit_files(root, sha)} == {(2, 0)}


def test_uncommitting_the_last_commit_keeps_its_files_as_they_are(tmp_path):
    root = _folder(tmp_path)
    history.commit(root, "File Index: batch 1 (1 scan)", ["scans"])
    group = history.commit(root, "Group: batch 1", ["archive"])

    with pytest.raises(history.Refused, match="no longer the last commit"):
        history.uncommit(root, "0000000")
    assert history.uncommit(root, group).subject == "Group: batch 1"
    assert history.head(root).subject == "File Index: batch 1 (1 scan)"
    assert history.changed_paths(root) == {"archive/2025/01/page.json", "archive/2025/01/page.png"}
    assert (root / "archive" / "2025" / "01" / "page.json").read_bytes() == b'{"a": 1}\r\n'   # untouched

    history.uncommit(root, history.head(root).sha)
    with pytest.raises(history.Refused, match="first commit"):
        history.uncommit(root, history.head(root).sha)
    assert history.status(root).changed == 3                        # all of it waits again, nothing lost


def test_a_commit_holding_the_only_copy_of_a_file_is_not_uncommitted_until_that_copy_is_back(tmp_path):
    """Archive keeps the scans as scanned, then clears them out of the scan folder. Uncommitting the clearing is
    safe (the scans are in the commit before); uncommitting the keeping would leave a scan nowhere at all."""
    root = _folder(tmp_path)
    scan = root / "scans" / "01102025132642_1.png"
    kept = history.commit(root, "Archive: 1 scan kept as scanned", ["scans"])
    scan.unlink()
    cleared = history.commit(root, "Archive: 1 scan cleared out of the scan folder", ["scans"])

    history.uncommit(root, cleared)
    assert history.changes(root)[-1] == history.Change("scans/01102025132642_1.png", "deleted")
    with pytest.raises(history.Refused, match=r"1 file this commit holds changed or went since \(scans/01102025132642_1.png\)"):
        history.uncommit(root, kept)
    assert history.head(root).sha == kept

    assert history.discard_changes(root, ["scans/01102025132642_1.png"]) == 1
    assert scan.read_bytes() == b"\x89PNG scan"                     # brought back from the commit
    history.uncommit(root, kept)
    assert history.head(root).subject == "History started" and scan.read_bytes() == b"\x89PNG scan"


def test_discarding_changes_puts_back_what_the_last_commit_holds_and_deletes_what_it_does_not(tmp_path):
    root = _folder(tmp_path)
    history.commit(root, "everything")
    sidecar, scan = root / "archive" / "2025" / "01" / "page.json", root / "scans" / "01102025132642_1.png"
    new, other = root / "scans" / "01102025132642_2.png", root / "archive" / "decisions.json"
    sidecar.write_bytes(b'{"a": 2}')
    scan.unlink()
    new.write_bytes(b"\x89PNG new scan")
    other.write_bytes(b"{}")

    thrown = history.discard_changes(root, ["archive/2025/01/page.json", "scans/01102025132642_1.png",
                                            "scans/01102025132642_2.png", "archive/2025/01/page.png"])
    assert thrown == 3                                              # page.png hadn't changed: skipped
    assert sidecar.read_bytes() == b'{"a": 1}\r\n' and scan.read_bytes() == b"\x89PNG scan" and not new.exists()
    assert history.changes(root) == [history.Change("archive/decisions.json", "added", (1, 0))]   # not asked: kept
    with pytest.raises(history.Refused, match="no history yet"):
        history.discard_changes(_folder(tmp_path / "other"), ["archive/decisions.json"])
