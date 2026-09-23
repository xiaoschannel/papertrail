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
