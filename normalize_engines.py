from pathlib import Path

import numpy as np
from rapidfuzz.distance import Levenshtein
from rapidfuzz.process import cdist
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_distances

from name_similarity import DEFAULT_THRESHOLD, ensure_embeddings


#: The Normalize slider's step for embedding distance; its range is one step to a hundred of them.
EMBEDDING_THRESHOLD_STEP = DEFAULT_THRESHOLD / 20


class NormalizeEngine:
    label: str = ""
    #: The thresholds this engine takes, in the unit the page shows it in (lowest, highest).
    threshold_range: tuple[float, float]

    def _cluster(
        self, dist_matrix: np.ndarray, eps: float, names: list[str]
    ) -> dict[int, list[str]]:
        labels = DBSCAN(eps=eps, min_samples=2, metric="precomputed").fit(dist_matrix).labels_
        cluster_map: dict[int, list[str]] = {}
        for i, label in enumerate(labels):
            if label == -1:
                continue
            cluster_map.setdefault(label, []).append(names[i])
        return cluster_map

    def _dist_matrix(self, output_path: Path, all_names: list[str]) -> np.ndarray:
        raise NotImplementedError

    def run(
        self, output_path: Path, all_names: list[str], eps: float
    ) -> dict[int, list[str]]:
        dist_matrix = self._dist_matrix(output_path, all_names)
        return self._cluster(dist_matrix, eps, all_names)


class EmbeddingEngine(NormalizeEngine):
    label = "Embedding (cosine)"
    threshold_range = (EMBEDDING_THRESHOLD_STEP, EMBEDDING_THRESHOLD_STEP * 100)

    def _dist_matrix(self, output_path: Path, all_names: list[str]) -> np.ndarray:
        cached_names, cached_matrix = ensure_embeddings(output_path, all_names)
        cached_lookup = {n: i for i, n in enumerate(cached_names)}
        indices = [cached_lookup[n] for n in all_names]
        embedding_matrix = cached_matrix[indices]
        return cosine_distances(embedding_matrix)


class StringEngine(NormalizeEngine):
    label = "String similarity (Levenshtein)"
    threshold_range = (50.0, 100.0)  # percent similarity

    def _dist_matrix(self, output_path: Path, all_names: list[str]) -> np.ndarray:
        # Every pair at once in rapidfuzz: a Python loop over every pair of names grows with the square of them.
        similarity = cdist(all_names, all_names, scorer=Levenshtein.normalized_similarity, dtype=np.float64)
        return 1.0 - similarity


ENGINES: dict[str, NormalizeEngine] = {
    "embedding": EmbeddingEngine(),
    "string": StringEngine(),
}
