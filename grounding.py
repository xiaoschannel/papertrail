"""Parse DeepSeek-OCR grounding output into boxes. Pure; no model or GPU imports."""

from __future__ import annotations

import ast
import re

from models import DetectedBox

_GROUNDING_RE = re.compile(
    r"<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>", re.DOTALL
)


def parse_grounding_output(raw: str) -> list[DetectedBox]:
    boxes: list[DetectedBox] = []
    for match in _GROUNDING_RE.finditer(raw):
        ref_text = match.group(1).strip()
        det_raw = match.group(2).strip()

        try:
            parsed = ast.literal_eval(det_raw)
            coords = [[int(x) for x in coord] for coord in parsed]
            boxes.append(DetectedBox(ref_type=str(len(boxes)), coords=coords, text=ref_text or None))
        except (SyntaxError, ValueError):
            print(f"Failed to parse the coordinates output: {det_raw}")
            continue
    return boxes
