"""Hybrid retrieval: pure RRF logic, no model or database required.

docs/06 §6.3 names hybrid+rerank the honest baseline the graph has to beat.
This tests only the fusion math — `reciprocal_rank_fusion` takes two already
computed rankings, so a real embedder is never needed here.
"""

from __future__ import annotations

from dge.retrieval.hybrid import RankedList, fuse_seeds, reciprocal_rank_fusion


def test_node_ranked_first_in_both_sources_wins():
    dense = RankedList("dense", ["a", "b", "c"])
    sparse = RankedList("sparse", ["a", "c", "b"])
    fused = reciprocal_rank_fusion([dense, sparse])
    assert fused[0].node_id == "a"
    assert fused[0].sources == ("dense", "sparse")


def test_node_present_in_only_one_ranking_still_scores():
    dense = RankedList("dense", ["a", "b"])
    sparse = RankedList("sparse", ["c"])
    fused = reciprocal_rank_fusion([dense, sparse])
    ids = {r.node_id for r in fused}
    assert ids == {"a", "b", "c"}


def test_rrf_score_decreases_with_rank():
    dense = RankedList("dense", ["a", "b", "c", "d"])
    fused = reciprocal_rank_fusion([dense])
    scores = [r.rrf_score for r in fused]
    assert scores == sorted(scores, reverse=True)


def test_top_n_truncates():
    dense = RankedList("dense", ["a", "b", "c", "d", "e"])
    fused = reciprocal_rank_fusion([dense], top_n=2)
    assert len(fused) == 2


def test_fuse_seeds_disagreement_still_surfaces_both_signals():
    """A node the sparse ranker misses entirely but dense ranks #1 should
    still make the seed set — that disagreement is exactly why hybrid beats
    either signal alone (docs/04 §4.1)."""
    dense = ["exception_node", "x", "y"]
    sparse = ["x", "y", "z"]
    seeds = fuse_seeds(dense=dense, sparse=sparse, top_k=4)
    assert "exception_node" in seeds


def test_fuse_seeds_respects_top_k():
    dense = [f"d{i}" for i in range(10)]
    sparse = [f"s{i}" for i in range(10)]
    seeds = fuse_seeds(dense=dense, sparse=sparse, top_k=3)
    assert len(seeds) == 3
