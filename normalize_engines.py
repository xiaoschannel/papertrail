from pathlib import Path

import numpy as np
from rapidfuzz.distance import Levenshtein
from rapidfuzz.process import cdist
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_distances

from name_similarity import DEFAULT_THRESHOLD, ensure_embeddings


class NormalizeEngine:
    label: str = ""

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

    def _dist_matrix(self, output_path: Path, all_names: list[str]) -> np.ndarray:
        cached_names, cached_matrix = ensure_embeddings(output_path, all_names)
        cached_lookup = {n: i for i, n in enumerate(cached_names)}
        indices = [cached_lookup[n] for n in all_names]
        embedding_matrix = cached_matrix[indices]
        return cosine_distances(embedding_matrix)

    def render_slider(self, st, key: str, default: float = DEFAULT_THRESHOLD, on_change=None) -> float:
        step = DEFAULT_THRESHOLD / 20
        return float(
            st.slider(
                "Distance threshold",
                min_value=step,
                max_value=step * 100,
                value=default,
                step=step,
                key=key,
                on_change=on_change,
            )
        )


class StringEngine(NormalizeEngine):
    label = "String similarity (Levenshtein)"

    def _dist_matrix(self, output_path: Path, all_names: list[str]) -> np.ndarray:
        # Every pair at once in rapidfuzz: a Python loop over every pair of names grows with the square of them.
        similarity = cdist(all_names, all_names, scorer=Levenshtein.normalized_similarity, dtype=np.float64)
        return 1.0 - similarity

    def render_slider(self, st, key: str, default: int = 80, on_change=None) -> float:
        pct = st.slider(
            "Min similarity (%)",
            min_value=50,
            max_value=100,
            value=default,
            step=1,
            key=key,
            on_change=on_change,
        )
        return 1.0 - pct / 100.0


ENGINES: dict[str, NormalizeEngine] = {
    "embedding": EmbeddingEngine(),
    "string": StringEngine(),
}
