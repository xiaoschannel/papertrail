"""Image serving for the two archive roots, with path-traversal guards."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

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


@router.get("/archived/{rel_path:path}")
def archived_media(rel_path: str, output_path: Path = Depends(get_output_path)):
    """Serve a file from the organized YYYY/MM (post-archive) tree."""
    return FileResponse(str(_safe_file(output_path, rel_path)))


@router.get("/input/{filename:path}")
def input_media(filename: str, input_path: Path = Depends(get_input_path)):
    """Serve an original scan (pre-archive) by filename."""
    return FileResponse(str(_safe_file(input_path, filename)))
