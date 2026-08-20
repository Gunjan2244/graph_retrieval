"""L2 rerank wiring, against a fake reranker.

Deliberately does not import `fastembed` or hit a network API — same
discipline `test_vectors.py` follows for the embedder adapters: a fast,
deterministic stand-in that conforms to `dge.interfaces.Reranker` is enough to
test `rerank_seeder`'s composition logic (candidate over-fetch, node
resolution, final truncation). The real adapters — that fastembed loads a
cross-encoder, that Voyage's rerank endpoint contract holds — are exercised
manually with network access, not in the default fast test run.
"""

from __future__ import annotations

from collections.abc import Sequence

from dge.model import InheritedContext, Node, NodeKind
from dge.query import rerank_seeder
from dge.traversal.graph import FixtureGraph


class FakeReranker:
    """Scores a candidate by how many query words it contains — deterministic,
    and deliberately disagreeing with document order so tests can tell
    whether reranking actually ran versus the base seeder's order surviving
    untouched."""

    def rerank(
        self, query: str, candidates: Sequence[Node], top_k: int
    ) -> Sequence[tuple[Node, float]]:
        q_words = set(query.lower().split())

        def score(node: Node) -> float:
            return float(len(q_words & set(node.raw.lower().split())))

        ranked = sorted(candidates, key=score, reverse=True)
        return [(n, score(n)) for n in ranked[:top_k]]


def _node(node_id: str, text: str) -> Node:
    return Node(
        node_id=node_id, doc_id="d", kind=NodeKind.PROPOSITION, seq=int(node_id),
        raw=text, inherited=InheritedContext(),
    )


def _graph(*texts: str) -> FixtureGraph:
    nodes = [_node(str(i), t) for i, t in enumerate(texts)]
    return FixtureGraph(nodes, [])


def test_rerank_reorders_candidates_the_base_seeder_ranked_worse():
    graph = _graph(
        "irrelevant filler about mining permits",
        "the owner must pay compensation within six weeks",
        "another unrelated clause about hours of work",
    )
    # Base seeder returns in a fixed, query-blind order (node 0 first) —
    # exactly what a weak lexical/hybrid stage might hand off.
    def base_seeder(query: str, top_k: int) -> list[str]:
        return [n.node_id for n in graph.nodes][:top_k]

    seeder = rerank_seeder(base_seeder, graph, FakeReranker(), candidate_multiplier=3)
    result = seeder("owner must pay compensation", top_k=1)

    assert result == ["1"], "the reranker must promote the actually-relevant candidate"


def test_rerank_over_fetches_candidates_before_truncating():
    graph = _graph(*[f"node number {i}" for i in range(10)])
    seen_top_k: list[int] = []

    def base_seeder(query: str, top_k: int) -> list[str]:
        seen_top_k.append(top_k)
        return [n.node_id for n in graph.nodes][:top_k]

    seeder = rerank_seeder(base_seeder, graph, FakeReranker(), candidate_multiplier=4)
    seeder("node", top_k=2)

    assert seen_top_k == [8], "rerank should over-fetch top_k * candidate_multiplier candidates"


def test_rerank_drops_candidate_ids_the_graph_cannot_resolve():
    graph = _graph("a real node")

    def base_seeder(query: str, top_k: int) -> list[str]:
        return ["0", "missing-id"]

    seeder = rerank_seeder(base_seeder, graph, FakeReranker(), candidate_multiplier=1)
    result = seeder("real", top_k=5)

    assert result == ["0"]
