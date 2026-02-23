from pathlib import Path

import numpy as np
from ollama import embed
from sklearn.metrics.pairwise import cosine_distances

from data import load_embeddings_cache, save_embeddings_cache

EMBED_MODEL = "nomic-embed-text"
DEFAULT_THRESHOLD = 0.05

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
