import json
from pathlib import Path

import numpy as np

from models import OcrBatch, DocumentExtractionAdapter, DocumentExtraction, ReviewDecision


def sidecar_path_for(file_path: Path) -> Path:
    return file_path.with_suffix(".json")


def read_sidecar(file_path: Path) -> dict:
    sp = sidecar_path_for(file_path)
    if not sp.exists():
        return {}
    return json.loads(sp.read_text(encoding="utf-8"))


def write_sidecar(file_path: Path, entry: dict):
    sidecar_path_for(file_path).write_text(
        json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def delete_sidecar(file_path: Path):
    sidecar_path_for(file_path).unlink(missing_ok=True)



def load_ocr_results(output_path: Path) -> OcrBatch:
    results_file = output_path / "ocr.json"
    if results_file.exists():
        return OcrBatch.model_validate_json(results_file.read_text(encoding="utf-8"))
    return OcrBatch(results=[])


def load_extractions(output_path: Path) -> dict[str, DocumentExtraction]:
    ext_file = output_path / "extractions.json"
    if not ext_file.exists():
        return {}
    data = json.loads(ext_file.read_text(encoding="utf-8"))
    return {k: DocumentExtractionAdapter.validate_python(v) for k, v in data.items()}


def save_extractions(output_path: Path, extractions: dict[str, DocumentExtraction]):
    d = {k: v.model_dump() for k, v in extractions.items()}
    (output_path / "extractions.json").write_text(
        json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_decisions(output_path: Path) -> dict[str, ReviewDecision]:
    dec_file = output_path / "decisions.json"
    if not dec_file.exists():
        return {}
    raw = json.loads(dec_file.read_text(encoding="utf-8"))
    return {k: ReviewDecision(**v) for k, v in raw.items()}


def save_decisions(output_path: Path, decisions: dict[str, ReviewDecision]):
    d = {k: v.model_dump() for k, v in decisions.items()}
    (output_path / "decisions.json").write_text(
        json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_name_cache(output_path: Path) -> dict[str, dict]:
    cache_file = output_path / "name_cache.json"
    if not cache_file.exists():
        return {}
    return json.loads(cache_file.read_text(encoding="utf-8"))


def save_name_cache(output_path: Path, cache: dict[str, dict]):
    (output_path / "name_cache.json").write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_name_normalizations(output_path: Path) -> dict[str, str]:
    f = output_path / "name_normalizations.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def save_name_normalizations(output_path: Path, normalizations: dict[str, str]):
    (output_path / "name_normalizations.json").write_text(
        json.dumps(normalizations, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _iter_year_month_dirs(output_path: Path):
    for year_dir in output_path.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not (month_dir.name.isdigit() or month_dir.name == "undated"):
                continue
            yield month_dir


def scan_organized_filenames(output_path: Path) -> set[str]:
    organized: set[str] = set()
    tossed_dir = output_path / "tossed"
    if tossed_dir.exists():
        organized.update(p.name for p in tossed_dir.iterdir() if p.is_file() and p.suffix.lower() != ".json")
    marked_dir = output_path / "marked"
    if marked_dir.exists():
        organized.update(p.name for p in marked_dir.iterdir() if p.is_file() and p.suffix.lower() != ".json")
    for month_dir in _iter_year_month_dirs(output_path):
        for sidecar in month_dir.glob("*.json"):
            entry = json.loads(sidecar.read_text(encoding="utf-8"))
            orig = entry.get("original_filename", "")
            if orig:
                organized.add(orig)
    return organized


def load_reorganized_state(
    output_path: Path,
) -> tuple[set[str], dict[str, dict]]:
    tossed: set[str] = set()
    tossed_dir = output_path / "tossed"
    if tossed_dir.exists():
        tossed = {p.name for p in tossed_dir.iterdir() if p.is_file() and p.suffix.lower() != ".json"}

    accepted_metadata: dict[str, dict] = {}
    for month_dir in _iter_year_month_dirs(output_path):
        sidecars: list[Path] = []
        stem_to_datafile: dict[str, Path] = {}
        for p in month_dir.iterdir():
            if not p.is_file():
                continue
            if p.suffix == ".json":
                sidecars.append(p)
            else:
                stem_to_datafile[p.stem] = p
        for sidecar in sidecars:
            entry = json.loads(sidecar.read_text(encoding="utf-8"))
            orig = entry.get("original_filename", "")
            if not orig:
                continue
            data_file = stem_to_datafile.get(sidecar.stem)
            if data_file:
                entry["_path"] = data_file.relative_to(output_path).as_posix()
            accepted_metadata[orig] = entry

    return tossed, accepted_metadata


def load_embeddings_cache(output_path: Path) -> tuple[list[str], np.ndarray | None]:
    f = output_path / "name_embeddings.npz"
    if not f.exists():
        return [], None
    data = np.load(f, allow_pickle=True)
    return data["names"].tolist(), data["matrix"]


def save_embeddings_cache(output_path: Path, names: list[str], matrix: np.ndarray):
    np.savez_compressed(
        output_path / "name_embeddings.npz",
        names=np.array(names),
        matrix=matrix,
    )
