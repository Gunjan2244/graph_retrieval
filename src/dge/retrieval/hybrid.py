"""Hybrid retrieval: dense (embeddings) + sparse (lexical) -> RRF -> seed set.

docs/04 §4.1 names this the first stage of the pipeline, ahead of any graph
work — and docs/06 §6.3 names "normalized nodes + hybrid + rerank, no
traversal" as the honest baseline the graph has to beat. This module is pure
rank-fusion logic; it takes two already-computed rankings and combines them, so
it is fully unit-testable with fake rankings and never needs a real embedder or
database in tests. The vector math (cosine over stored embeddings) lives in
`dge.bundle.BundleGraph.search_vectors` — this module only fuses ranks.

Reciprocal Rank Fusion, not score averaging: dense cosine similarity and BM25 /
TF-IDF scores live on incomparable scales, and RRF sidesteps ever having to
normalize them against each other.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RankedList:
    """One ranking source's output: node ids in rank order (best first)."""

    name: str
    node_ids: Sequence[str]


@dataclass(frozen=True, slots=True)
class FusedResult:
    node_id: str
    rrf_score: float
    sources: tuple[str, ...]  # which ranking(s) placed this node, for the eval trace


def reciprocal_rank_fusion(
    rankings: Sequence[RankedList], *, k: int = 60, top_n: int | None = None
) -> list[FusedResult]:
    """Standard RRF: score(d) = sum over rankings of 1 / (k + rank(d)).

    `k` is RRF's own damping constant (60 is the value from the original paper
    and is not sensitive to tuning — this is not one of the traversal budget
    knobs in `dge.traversal.policy`). A node absent from a ranking simply does
    not contribute a term from it; it is not penalized beyond that.
    """
    scores: dict[str, float] = {}
    sources: dict[str, list[str]] = {}

    for ranking in rankings:
        for rank, node_id in enumerate(ranking.node_ids, start=1):
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (k + rank)
            sources.setdefault(node_id, []).append(ranking.name)

    fused = [
        FusedResult(node_id, score, tuple(sources[node_id]))
        for node_id, score in scores.items()
    ]
    fused.sort(key=lambda r: (-r.rrf_score, r.node_id))
    return fused[:top_n] if top_n is not None else fused


def fuse_seeds(
    *, dense: Sequence[str], sparse: Sequence[str], top_k: int, k: int = 60
) -> list[str]:
    """Convenience wrapper for the common case: two rankings in, top-k ids out."""
    fused = reciprocal_rank_fusion(
        [RankedList("dense", dense), RankedList("sparse", sparse)],
        k=k,
        top_n=top_k,
    )
    return [r.node_id for r in fused]
