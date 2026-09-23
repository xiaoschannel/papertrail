import json
import logging
import threading
import time
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ValidationError, field_validator, model_validator

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

#: The Papertrail folder's layout: ``root_path`` holds the scan folder (where the scanner drops images) and
#: the archive (batches, mid-ingest files, YYYY/MM folders) side by side, and its history (archive_history).
SCANS_DIR, ARCHIVE_DIR = "scans", "archive"


def scans_path(root: str | Path) -> Path:
    return Path(root) / SCANS_DIR


def archive_path(root: str | Path) -> Path:
    return Path(root) / ARCHIVE_DIR


def root_of(archive: Path) -> Path:
    """The Papertrail folder an archive belongs to: the archive is ``<root>/archive``."""
    return archive.parent


def ensure_layout(root: str | Path) -> None:
    """Give an existing Papertrail folder its two subfolders, named exactly ``scans`` and ``archive``.

    A subfolder named the same but for case (``Archive``) is renamed: Windows finds it either way, but the
    folder's history compares paths as git reports them, as they are on disk, so a milestone would match
    none of its files. A missing one is made. A folder that isn't there yet is left alone.
    """
    root = Path(root)
    if not root.is_dir():
        return
    for name in (SCANS_DIR, ARCHIVE_DIR):
        children = {p.name: p for p in root.iterdir() if p.is_dir()}
        if name not in children:
            other = [p for n, p in children.items() if n.lower() == name]
            if len(other) == 1:
                other[0].rename(root / name)          # a case-only rename: the same folder, renamed
        (root / name).mkdir(exist_ok=True)


#: The tilt share the Config page offers (``AppConfig.tilt_share``).
TILT_SHARE_RANGE = (0.005, 0.1)


#: Keys a shortcut can be besides one character: the names `KeyboardEvent.key` gives them (" " is Space).
#: Tab moves focus and the modifiers only change other keys, so neither can be a shortcut.
NAMED_KEYS = (" ", "Enter", "Escape", "Backspace", "Delete", "Insert", "Home", "End", "PageUp", "PageDown",
              "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown")


class Shortcuts(BaseModel):
    """Every single-key shortcut in the app, one key per action. The defaults sit under the left hand
    (A S D side by side, X C below, E Q above), so nothing needs a reach across the keyboard; there is
    no second set built in, so a key does only what is chosen here (Esc still leaves a text field).
    """

    # Review; the Workshop takes accept, toss, prev and next too
    accept: str = "a"
    mark: str = "s"
    toss: str = "d"
    prev: str = "x"
    next: str = "c"
    undo: str = "z"
    # The first three quick matches, on Review and in the Marked Workshop (they share the form)
    quick_1: str = "1"
    quick_2: str = "2"
    quick_3: str = "3"
    # Every scan with boxes on it: Review, the Marked Workshop, Experiment and Receipt Detail
    hide_boxes: str = "b"
    # The Marked Workshop and Experiment
    hold_original: str = "r"
    # Every confirmation dialog, and every scan viewer (cancel closes it). Nothing behind a dialog
    # listens while it is open, so these may repeat a page's keys.
    confirm: str = "e"
    cancel: str = "q"
    # Fix Rotation's scan viewer, beside cancel: W E R above, D below
    leave_as_is: str = "w"
    turn_upright: str = "e"
    toggle_guides: str = "r"
    straighten: str = "d"

    #: Actions live at the same time, so no two of them may share a key.
    QUICK: ClassVar = ("quick_1", "quick_2", "quick_3")
    GROUPS: ClassVar = (("accept", "mark", "toss", "prev", "next", "undo", "hide_boxes", *QUICK),
                        ("accept", "toss", "prev", "next", "hold_original", "hide_boxes", *QUICK),
                        ("confirm", "cancel"),
                        ("leave_as_is", "turn_upright", "toggle_guides", "straighten", "cancel"))

    @field_validator("*", mode="before")
    @classmethod
    def _one_key(cls, value: object) -> object:
        if not isinstance(value, str) or value in NAMED_KEYS:
            return value
        if len(value) != 1 or value.isspace():
            raise ValueError(f"{value!r} is not a key a shortcut can be")
        return value.lower()

    @model_validator(mode="after")
    def _no_two_actions_share_a_key(self) -> "Shortcuts":
        for group in self.GROUPS:
            seen: dict[str, str] = {}
            for action in group:
                key = getattr(self, action)
                if key in seen:
                    raise ValueError(f"{seen[key]} and {action} are both on {key!r}")
                seen[key] = action
        return self


class AppConfig(BaseModel):
    #: the Papertrail folder: ``scans/`` and ``archive/`` inside it, and its history at its root
    root_path: str = ""
    extract_structured: bool = True
    ocr_model: str = ""
    workshop_ocr_model: str = ""
    extractor_model: str = ""
    workshop_extractor_model: str = ""
    parse_custom_instruction: str = ""
    normalize_engine: str = "embedding"
    normalize_embedding_threshold: float = 0.05
    normalize_string_similarity: int = 80
    indexing_scheme: str = ""
    dashboard_rank_by: str = "Total Spend"
    prefix_suggestion_boundary_only: bool = True
    prefix_suggestion_max_length: int = 24
    prefix_suggestion_min_length: int = 3
    prefix_suggestion_min_count: int = 2
    #: A tilt worth fixing on Fix Rotation, as a share of the page's short side (deskew.DEFAULT_TILT_SHARE).
    tilt_share: float = 0.03
    calendar_period: str = "week"
    calendar_date: str = ""
    shortcuts: Shortcuts = Shortcuts()

    @field_validator("tilt_share")
    @classmethod
    def _a_share_the_page_can_show(cls, value: float) -> float:
        if not TILT_SHARE_RANGE[0] <= value <= TILT_SHARE_RANGE[1]:
            raise ValueError(f"a tilt share must be between {TILT_SHARE_RANGE[0]} and {TILT_SHARE_RANGE[1]}")
        return value


def get_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()
    # Windows refuses to open the file for the instant a save swaps the new one in: try again then.
    for attempt in range(50):
        try:
            text = CONFIG_PATH.read_text(encoding="utf-8")
            break
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.01)
    data = {**AppConfig().model_dump(), **json.loads(text)}
    # A section that no longer validates (hand-edited, or a new action's default now clashing with a key
    # chosen earlier) falls back to its defaults: one bad section must not take every page down, the
    # Config page that could fix it included. Saving that section again writes a good one.
    for name, field in AppConfig.model_fields.items():
        section = field.annotation
        if isinstance(section, type) and issubclass(section, BaseModel):
            try:
                section.model_validate(data[name])
            except ValidationError as error:
                log.warning("config.json: %s is not valid, using its defaults: %s", name, error)
                data[name] = section().model_dump()
    return AppConfig.model_validate(data)


#: One change to the config at a time, so two changes made together can't undo each other.
_changing = threading.RLock()


def save_config(cfg: AppConfig) -> None:
    """Write the whole config; a reader sees the old file or the new one, never half of one."""
    from data import atomic_write_text

    with _changing:
        atomic_write_text(CONFIG_PATH, json.dumps(cfg.model_dump(), indent=2))


def update_config(**kwargs) -> None:
    """Change only these settings (and write nothing when they already hold these values)."""
    with _changing:
        before = get_config().model_dump()
        # A section (the shortcuts) arrives as a dict of only the keys that change: laid over the saved one,
        # then validated as a whole, so a clash is judged against the keys actually in use.
        changes = {k: ({**before[k], **v} if isinstance(v, dict) and isinstance(before[k], dict) else v)
                   for k, v in kwargs.items() if k in AppConfig.model_fields}
        cfg = AppConfig.model_validate({**before, **changes})
        if cfg.model_dump() != before:
            save_config(cfg)
