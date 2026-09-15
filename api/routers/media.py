"""Image serving for the two archive roots, with path-traversal guards."""

from __future__ import annotations

from pathlib import Path

from functools import lru_cache
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from api.deps import get_input_path, get_output_path

router = APIRouter(prefix="/api/media", tags=["media"])


def _safe_file(root: Path, rel: str) -> Path:
    root = root.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=404, detail="not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return target


@router.get("/archived/{rel_path:path}", response_class=FileResponse)
def archived_media(rel_path: str, output_path: Path = Depends(get_output_path)):
    """Serve a file from the organized YYYY/MM (post-archive) tree."""
    return FileResponse(str(_safe_file(output_path, rel_path)))


@router.get("/input/{filename:path}", response_class=FileResponse)
def input_media(filename: str, input_path: Path = Depends(get_input_path)):
    """Serve an original scan (pre-archive) by filename."""
    return FileResponse(str(_safe_file(input_path, filename)))


@lru_cache(maxsize=512)
def _thumbnail(path: str, mtime_ns: int, width: int) -> bytes:
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((width, width * 4))
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


@router.get("/input-thumb/{filename:path}", response_class=Response,
            responses={200: {"content": {"image/jpeg": {}}, "description": "JPEG thumbnail"}})
def input_thumbnail(
    filename: str,
    width: int = Query(360, ge=64, le=1200),
    input_path: Path = Depends(get_input_path),
):
    """A scan from the input folder scaled to ``width`` px (cached until the file changes, e.g. rotated)."""
    path = _safe_file(input_path, filename)
    data = _thumbnail(str(path), path.stat().st_mtime_ns, width)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
