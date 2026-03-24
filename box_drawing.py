from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from models import DetectedBox

FIELD_COLORS: dict[str, tuple[int, int, int]] = {
    "name": (66, 133, 244),
    "date": (52, 168, 83),
    "time": (0, 188, 212),
    "cost": (234, 67, 53),
    "title": (66, 133, 244),
}

DEFAULT_FIELD_COLOR = (120, 120, 120)

EXCLUDED_SOURCE_FIELDS = {"document_type", "language", "currency", "items", "address", "phone"}

_CJK_FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/meiryo.ttc"),
    Path("C:/Windows/Fonts/YuGothR.ttc"),
    Path("C:/Windows/Fonts/msgothic.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
]


def _load_label_font(size: int = 20) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _CJK_FONT_CANDIDATES:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


_LABEL_FONT = _load_label_font(20)


def draw_field_boxes(
    img: Image.Image,
    page_num: int,
    boxes: list[DetectedBox],
    field_sources: dict[str, list[str]],
) -> Image.Image:
    page_prefix = f"{page_num}:"
    box_to_fields: dict[int, list[str]] = {}
    for field, refs in field_sources.items():
        if field in EXCLUDED_SOURCE_FIELDS:
            continue
        for ref in refs:
            if ref.startswith(page_prefix):
                box_idx = int(ref[len(page_prefix):])
                box_to_fields.setdefault(box_idx, []).append(field)

    if not box_to_fields:
        return img

    overlay = img.copy().convert("RGBA")
    draw_layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(draw_layer)
    w, h = img.size

    for box_idx, fields in box_to_fields.items():
        if box_idx >= len(boxes):
            continue
        color = FIELD_COLORS.get(fields[0], DEFAULT_FIELD_COLOR)
        for coords in boxes[box_idx].coords:
            x1 = int(coords[0] / 1000 * w)
            y1 = int(coords[1] / 1000 * h)
            x2 = int(coords[2] / 1000 * w)
            y2 = int(coords[3] / 1000 * h)
            draw.rectangle([x1, y1, x2, y2], fill=(*color, 50), outline=(*color, 200), width=2)
            label = ", ".join(fields)
            draw.text((x1 + 2, y1 - 22 if y1 > 24 else y1 + 2), label, fill=(*color, 230), font=_LABEL_FONT)

    return Image.alpha_composite(overlay, draw_layer).convert("RGB")


_BOX_COLORS = [
    (66, 133, 244),
    (234, 67, 53),
    (52, 168, 83),
    (251, 188, 4),
    (0, 188, 212),
    (156, 39, 176),
    (255, 112, 67),
    (63, 81, 181),
]


def draw_all_boxes(img: Image.Image, boxes: list[DetectedBox]) -> Image.Image:
    if not boxes:
        return img

    overlay = img.copy().convert("RGBA")
    draw_layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(draw_layer)
    w, h = img.size

    for idx, box in enumerate(boxes):
        color = _BOX_COLORS[idx % len(_BOX_COLORS)]
        for coords in box.coords:
            x1 = int(coords[0] / 1000 * w)
            y1 = int(coords[1] / 1000 * h)
            x2 = int(coords[2] / 1000 * w)
            y2 = int(coords[3] / 1000 * h)
            draw.rectangle([x1, y1, x2, y2], fill=(*color, 50), outline=(*color, 200), width=2)
            label = box.text or f"BOX-{idx}"
            draw.text((x1 + 2, y1 - 22 if y1 > 24 else y1 + 2), label, fill=(*color, 230), font=_LABEL_FONT)

    return Image.alpha_composite(overlay, draw_layer).convert("RGB")
