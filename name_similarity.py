from pathlib import Path

import numpy as np
from ollama import embed
from rapidfuzz.distance import Levenshtein
from sklearn.metrics.pairwise import cosine_distances

from data import load_embeddings_cache, save_embeddings_cache
from models import SmartMatchCandidate, SmartMatchHistoryRow

EMBED_MODEL = "nomic-embed-text"
DEFAULT_THRESHOLD = 0.05

SMART_MATCH_THRESHOLD = 0.25
PHONE_BOOST_MIN = 0.90
PHONE_WEIGHT = 0.35
QUICK_APPLY_THRESHOLD = 0.85
MIN_PHONE_DIGITS = 7


def levenshtein_similarity(a: str, b: str) -> float:
    return Levenshtein.normalized_similarity(a, b)


def normalize_phone_for_match(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def phone_matchable(s: str) -> bool:
    return len(normalize_phone_for_match(s)) >= MIN_PHONE_DIGITS


def _row_scores(query_name: str, query_phone: str, row: SmartMatchHistoryRow) -> tuple[float, float]:
    name_score = levenshtein_similarity(query_name, row.extracted) if query_name else 0.0
    if (
        phone_matchable(query_phone)
        and phone_matchable(row.extracted_phone)
    ):
        phone_score = levenshtein_similarity(
            normalize_phone_for_match(query_phone),
            normalize_phone_for_match(row.extracted_phone),
        )
    else:
        phone_score = 0.0
    return name_score, phone_score


def get_smart_match_candidates(
    query_name: str,
    query_phone: str,
    history: list[SmartMatchHistoryRow],
) -> list[SmartMatchCandidate]:
    if not history or (not query_name and not phone_matchable(query_phone)):
        return []
    best_name: dict[str, float] = {}
    best_phone: dict[str, float] = {}
    for row in history:
        ns, ps = _row_scores(query_name, query_phone, row)
        c = row.confirmed
        if c not in best_name or ns > best_name[c]:
            best_name[c] = ns
        if c not in best_phone or ps > best_phone[c]:
            best_phone[c] = ps
    out: list[SmartMatchCandidate] = []
    for confirmed in best_name:
        name_score = best_name[confirmed]
        phone_score = best_phone.get(confirmed, 0.0)
        included = name_score >= SMART_MATCH_THRESHOLD or phone_score >= PHONE_BOOST_MIN
        if not included:
            continue
        phone_contrib = phone_score if phone_score >= PHONE_BOOST_MIN else 0.0
        combined_score = name_score + PHONE_WEIGHT * phone_contrib
        quick_apply = max(name_score, phone_score) >= QUICK_APPLY_THRESHOLD
        out.append(
            SmartMatchCandidate(
                confirmed_name=confirmed,
                name_score=name_score,
                phone_score=phone_score,
                combined_score=combined_score,
                quick_apply=quick_apply,
            )
        )
    out.sort(key=lambda x: (-x.combined_score, -max(x.name_score, x.phone_score), x.confirmed_name))
    return out


def quick_apply_label(c: SmartMatchCandidate) -> str:
    bits: list[str] = []
    if c.name_score >= QUICK_APPLY_THRESHOLD:
        bits.append(f"name {c.name_score:.0%}")
    if c.phone_score >= QUICK_APPLY_THRESHOLD:
        bits.append(f"phone {c.phone_score:.0%}")
    suffix = ", ".join(bits)
    return f"{c.confirmed_name} — {suffix}" if suffix else c.confirmed_name


def ensure_embeddings(
    output_path: Path,
    names: list[str],
) -> tuple[list[str], np.ndarray]:
    cached_names, cached_matrix = load_embeddings_cache(output_path)
    cached_lookup = {n: i for i, n in enumerate(cached_names)}

    new_names = [n for n in names if n not in cached_lookup]
    if not new_names:
        return cached_names, cached_matrix

    response = embed(model=EMBED_MODEL, input=new_names)
    new_vectors = np.array(response.embeddings, dtype=np.float32)

    if cached_matrix is not None:
        updated_names = cached_names + new_names
        updated_matrix = np.vstack([cached_matrix, new_vectors])
    else:
        updated_names = new_names
        updated_matrix = new_vectors

    save_embeddings_cache(output_path, updated_names, updated_matrix)
    return updated_names, updated_matrix


def find_similar_names(
    query_name: str,
    all_names: list[str],
    cached_names: list[str],
    cached_matrix: np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
    top_n: int = 10,
) -> list[tuple[str, float]]:
    if not query_name or not all_names or cached_matrix is None:
        return []

    cached_lookup = {n: i for i, n in enumerate(cached_names)}
    if query_name not in cached_lookup:
        return []

    query_idx = cached_lookup[query_name]
    query_vec = cached_matrix[query_idx : query_idx + 1]

    other_names = [n for n in all_names if n != query_name and n in cached_lookup]
    if not other_names:
        return []

    other_indices = [cached_lookup[n] for n in other_names]
    other_matrix = cached_matrix[other_indices]
    distances = cosine_distances(query_vec, other_matrix)[0]

    results: list[tuple[str, float]] = []
    for i, dist in enumerate(distances):
        if dist <= threshold:
            results.append((other_names[i], float(dist)))

    results.sort(key=lambda x: x[1])
    return results[:top_n]
