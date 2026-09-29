from pathlib import Path

import numpy as np
from rapidfuzz.distance import Levenshtein
from rapidfuzz.process import cdist
from sklearn.cluster import DBSCAN
from sklearn.metrics.pairwise import cosine_distances

from papertrail.data import load_embeddings_cache
from papertrail.curate.similarity import ensure_embeddings


class NormalizeEngine:
    label: str = ""

    def _cluster(
        self, dist_matrix: np.ndarray, eps: float, names: list[str]
    ) -> dict[int, list[str]]:
        # 100% similarity is distance 0, which DBSCAN refuses: the smallest distance above it groups only
        # names that are the same, as 100% means
        labels = DBSCAN(eps=max(eps, 1e-9), min_samples=2, metric="precomputed").fit(dist_matrix).labels_
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

    def run_offline(
        self, output_path: Path, all_names: list[str], eps: float
    ) -> tuple[dict[int, list[str]], list[str]]:
        """Like ``run``, but never asks a model: (clusters, the names it had nothing for and left out)."""
        return self.run(output_path, all_names, eps), []


class EmbeddingEngine(NormalizeEngine):
    label = "Embedding (cosine)"

    def _dist_matrix(self, output_path: Path, all_names: list[str]) -> np.ndarray:
        cached_names, cached_matrix = ensure_embeddings(output_path, all_names)
        cached_lookup = {n: i for i, n in enumerate(cached_names)}
        indices = [cached_lookup[n] for n in all_names]
        embedding_matrix = cached_matrix[indices]
        return cosine_distances(embedding_matrix)

    def run_offline(
        self, output_path: Path, all_names: list[str], eps: float
    ) -> tuple[dict[int, list[str]], list[str]]:
        """Only the names already embedded (``name_embeddings.npz``): a new name waits until the Normalize
        page embeds it, rather than asking Ollama here."""
        cached_names, cached_matrix = load_embeddings_cache(output_path)
        cached_lookup = {n: i for i, n in enumerate(cached_names)}
        known = [n for n in all_names if n in cached_lookup]
        missing = [n for n in all_names if n not in cached_lookup]
        if len(known) < 2:
            return {}, missing
        dist_matrix = cosine_distances(cached_matrix[[cached_lookup[n] for n in known]])
        return self._cluster(dist_matrix, eps, known), missing


class StringEngine(NormalizeEngine):
    label = "String similarity (Levenshtein)"

    def _dist_matrix(self, output_path: Path, all_names: list[str]) -> np.ndarray:
        # Every pair at once in rapidfuzz: a Python loop over every pair of names grows with the square of them.
        similarity = cdist(all_names, all_names, scorer=Levenshtein.normalized_similarity, dtype=np.float64)
        return 1.0 - similarity


ENGINES: dict[str, NormalizeEngine] = {
    "embedding": EmbeddingEngine(),
    "string": StringEngine(),
}
