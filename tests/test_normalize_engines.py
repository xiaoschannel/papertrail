"""Merchant-name clustering. The string engine is fully deterministic; the
embedding engine's clustering core is tested with a precomputed matrix (no Ollama)."""

from pathlib import Path

import numpy as np

from normalize_engines import EmbeddingEngine, StringEngine


def test_string_engine_clusters_near_duplicates():
    names = ["Family Mart SS2", "Family Mart KL", "Shanghai Dumplings"]
    clusters = StringEngine().run(Path("."), names, eps=0.4)
    assert len(clusters) == 1
    (members,) = clusters.values()
    assert set(members) == {"Family Mart SS2", "Family Mart KL"}


def test_string_engine_no_cluster_when_all_distinct():
    names = ["Alpha", "Bravo", "Charlie"]
    assert StringEngine().run(Path("."), names, eps=0.1) == {}


def test_cluster_core_on_precomputed_distance_matrix():
    dist = np.array([
        [0.0, 0.1, 0.9],
        [0.1, 0.0, 0.9],
        [0.9, 0.9, 0.0],
    ])
    clusters = EmbeddingEngine()._cluster(dist, eps=0.3, names=["a", "b", "c"])
    assert len(clusters) == 1
    (members,) = clusters.values()
    assert set(members) == {"a", "b"}
