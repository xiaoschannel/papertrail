"""Pure helpers for linking scanned pages into multi-page documents (File Index step).

Extracted from ``pages/ingest/file_index.py`` so the Streamlit page and the web API share them.
A batch's pages are shown in scan order; tossed pages keep their slot but never join a group, and
``links[i]`` says whether active page ``i`` continues into active page ``i + 1``.
"""

from __future__ import annotations

from PIL import Image


def compute_groups(keys: list[str], links: list[bool]) -> list[list[str]]:
    """Consecutive runs of linked keys, in order (single pages are one-element groups)."""
    if not keys:
        return []
    groups: list[list[str]] = []
    current = [keys[0]]
    for i in range(1, len(keys)):
        if i - 1 < len(links) and links[i - 1]:
            current.append(keys[i])
        else:
            groups.append(current)
            current = [keys[i]]
    groups.append(current)
    return groups


def links_from_groups(keys: list[str], groups: list[list[str]]) -> list[bool]:
    key_to_group: dict[str, int] = {}
    for gi, group in enumerate(groups):
        for key in group:
            key_to_group[key] = gi
    return [key_to_group.get(keys[i], -1) == key_to_group.get(keys[i + 1], -2) for i in range(len(keys) - 1)]


def group_containing(idx: int, keys: list[str], links: list[bool]) -> tuple[int, list[int]]:
    """(group index, key indices) of the group holding ``keys[idx]``, or (-1, [])."""
    for gi, group in enumerate(compute_groups(keys, links)):
        for key in group:
            if keys.index(key) == idx:
                return gi, [keys.index(k) for k in group]
    return -1, []


def build_display_keys(filtered_groups: list[list[str]], batch_keys: list[str], tossed: set[str]) -> list[str]:
    """Batch keys in display order: saved groups keep their (possibly swapped) internal order."""
    if not filtered_groups:
        return batch_keys
    keys_in_groups = {k for g in filtered_groups for k in g}
    batch_idx = {k: i for i, k in enumerate(batch_keys)}
    groups_by_scan = sorted(filtered_groups, key=lambda g: min(batch_idx[k] for k in g if k not in tossed))
    active_ordered = iter([k for g in groups_by_scan for k in g if k not in tossed])
    result = []
    for key in batch_keys:
        if key in tossed:
            result.append(key)
        elif key in keys_in_groups:
            result.append(next(active_ordered, key))
        else:
            result.append(key)
    return result


def split_groups_at_tossed_boundaries(groups: list[list[str]], batch_keys: list[str], tossed: set[str]) -> list[list[str]]:
    """Drop tossed pages from groups, splitting a group wherever a tossed page sat between members."""
    batch_idx = {k: i for i, k in enumerate(batch_keys)}
    result = []
    for group in groups:
        active = [k for k in group if k not in tossed]
        if not active:
            continue
        current = [active[0]]
        for i in range(1, len(active)):
            lo, hi = sorted((batch_idx[active[i - 1]], batch_idx[active[i]]))
            if any(batch_keys[j] in tossed for j in range(lo + 1, hi)):
                result.append(current)
                current = [active[i]]
            else:
                current.append(active[i])
        result.append(current)
    return result


def build_display_state(
    batch_keys: list[str],
    batch_groups: list[list[str]],
    tossed: set[str],
) -> tuple[list[str], list[str], list[bool]]:
    """(display keys incl. tossed, active keys, links between active keys) for a batch."""
    filtered = split_groups_at_tossed_boundaries(batch_groups, batch_keys, tossed)
    display_keys = build_display_keys(filtered, batch_keys, tossed)
    active_keys = [k for k in display_keys if k not in tossed]
    active_links = links_from_groups(active_keys, filtered) if filtered else [False] * max(0, len(active_keys) - 1)
    return display_keys, active_keys, active_links


#: Keyed by where the TOP of the page currently points (the File Index arrows): the transform that
#: turns it upright. Same transforms as the Streamlit page's ←, → and ↓ buttons.
ROTATIONS = {
    "left": Image.Transpose.ROTATE_270,   # top points left  -> rotate 90° clockwise
    "right": Image.Transpose.ROTATE_90,   # top points right -> rotate 90° counter-clockwise
    "down": Image.Transpose.ROTATE_180,   # upside down      -> rotate 180°
}


def rotate_upright(img: Image.Image, top_points: str) -> Image.Image:
    """Turn a scan upright given where its top currently points ("left", "right" or "down")."""
    return img.transpose(ROTATIONS[top_points]) if top_points in ROTATIONS else img
