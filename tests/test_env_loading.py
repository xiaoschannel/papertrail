"""Where API keys are read from: one file, chosen in a fixed order, never overriding the environment."""

import os

import env

KEY = "PAPERTRAIL_TEST_KEY"


def _checkout(tmp_path, monkeypatch, *, repo=None, parent=None, user=None):
    """A checkout nested under a parent checkout, plus a machine-wide file; each optionally has a .env."""
    main = tmp_path / "main"
    worktree = main / ".claude" / "worktrees" / "wt"
    worktree.mkdir(parents=True)
    (main / ".git").mkdir()                   # the main checkout: a worktree's .git is a file, this one a folder
    (worktree / ".git").write_text("gitdir: ../../../.git/worktrees/wt\n", encoding="utf-8")
    user_dir = tmp_path / "appdata" / "papertrail"
    user_dir.mkdir(parents=True)
    for folder, value in ((worktree, repo), (main, parent), (user_dir, user)):
        if value is not None:
            (folder / ".env").write_text(f"{KEY}={value}\n", encoding="utf-8")
    monkeypatch.setattr(env, "REPO_ROOT", worktree)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv(KEY, "")
    monkeypatch.delenv(KEY)
    return worktree


def test_the_checkouts_own_env_is_used_first(tmp_path, monkeypatch):
    _checkout(tmp_path, monkeypatch, repo="from-checkout", parent="from-main", user="from-appdata")
    assert env.load_env() == env.REPO_ROOT / ".env"
    assert os.environ[KEY] == "from-checkout"


def test_a_worktree_falls_back_to_the_main_checkouts_env(tmp_path, monkeypatch):
    _checkout(tmp_path, monkeypatch, parent="from-main", user="from-appdata")
    assert env.load_env() == env.REPO_ROOT.parents[2] / ".env"
    assert os.environ[KEY] == "from-main"


def test_a_clone_outside_the_tree_falls_back_to_the_machine_wide_file(tmp_path, monkeypatch):
    _checkout(tmp_path, monkeypatch, user="from-appdata")
    assert env.load_env() == env.user_env_path()
    assert os.environ[KEY] == "from-appdata"


def test_a_env_above_the_main_checkout_is_never_read(tmp_path, monkeypatch):
    _checkout(tmp_path, monkeypatch, user="from-appdata")
    (tmp_path / ".env").write_text(f"{KEY}=from-another-project\n", encoding="utf-8")
    assert env.load_env() == env.user_env_path()
    assert os.environ[KEY] == "from-appdata"


def test_an_exported_variable_beats_the_file(tmp_path, monkeypatch):
    _checkout(tmp_path, monkeypatch, repo="from-checkout")
    monkeypatch.setenv(KEY, "from-shell")
    env.load_env()
    assert os.environ[KEY] == "from-shell"


def test_a_blank_entry_does_not_count_as_a_key(tmp_path, monkeypatch):
    _checkout(tmp_path, monkeypatch, repo="", parent="from-main")
    env.load_env()  # the empty line in the checkout's own file must not shadow the one above it
    assert env.missing_keys(KEY) == [KEY]


def test_no_file_anywhere_is_not_an_error(tmp_path, monkeypatch):
    _checkout(tmp_path, monkeypatch)
    assert env.load_env() is None
    assert env.missing_keys(KEY) == [KEY]


def test_user_env_path_follows_the_platform(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    assert env.user_env_path() == tmp_path / "AppData" / "Roaming" / "papertrail" / ".env"
    monkeypatch.delenv("APPDATA", raising=False)
    assert env.user_env_path().parts[-3:] == (".config", "papertrail", ".env")
