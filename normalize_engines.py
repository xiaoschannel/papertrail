from pathlib import Path

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_distances

from name_similarity import DEFAULT_THRESHOLD, ensure_embeddings, levenshtein_similarity


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

    def render_slider(self, st, key: str) -> float:
        step = DEFAULT_THRESHOLD / 20
        return float(
            st.slider(
                "Distance threshold",
                min_value=step,
                max_value=step * 100,
                value=DEFAULT_THRESHOLD,
                step=step,
                key=key,
            )
        )


class StringEngine(NormalizeEngine):
    label = "String similarity (Levenshtein)"

    def _dist_matrix(self, output_path: Path, all_names: list[str]) -> np.ndarray:
        n = len(all_names)
        dist_matrix = np.zeros((n, n), dtype=np.float64)
        for i in range(n):
            for j in range(i + 1, n):
                d = 1.0 - levenshtein_similarity(all_names[i], all_names[j])
                dist_matrix[i, j] = d
                dist_matrix[j, i] = d
        return dist_matrix

    def render_slider(self, st, key: str) -> float:
        pct = st.slider(
            "Min similarity (%)",
            min_value=50,
            max_value=100,
            value=80,
            step=1,
            key=key,
        )
        return 1.0 - pct / 100.0


ENGINES: dict[str, NormalizeEngine] = {
    "embedding": EmbeddingEngine(),
    "string": StringEngine(),
}
