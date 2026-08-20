"""Lexical seeding — the honest placeholder until L2 lands.

BUILD_PLAN Phase 1 calls for hybrid retrieval (dense + sparse, RRF, then a
cross-encoder rerank). None of that exists yet: there is no embedder, no vector
column, no reranker. This module is the sparse half only, so that traversal —
which is the actual differentiator and the thing Stage B exists to build — can
be exercised end-to-end against a real bundle before Stage C wires embeddings.

It is deliberately a plain TF-IDF cosine rather than anything cleverer:

  - It must never be mistaken for the product's retrieval quality. docs/06 §6.3
    names "normalized nodes + inherited context + hybrid + rerank" as the
    honest baseline the graph has to beat; this is weaker than that baseline,
    not stronger, so any traversal win measured against it is provisional.
  - It has no dependencies, matching the stdlib-only core.

Swap this for the real seeder in Stage C by passing a different `seeder` to
`dge.query.run_query` — nothing in traversal depends on this module.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from dge.model import Node

_WORD = re.compile(r"[a-z0-9][a-z0-9\-']*")
_STOPWORDS = frozenset(["a", "an", "the", "and", "or", "but", "if", "of", "in", "on", "at", "to", "for", "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "being", "this", "that", "these", "those", "it", "its", "shall", "may", "must", "not", "no", "any", "such", "other", "under", "over", "per", "than", "then", "there", "here", "which", "who", "whom", "whose", "what", "when", "where", "how"])


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 1]


@dataclass(frozen=True, slots=True)
class Scored:
    node_id: str
    score: float


class LexicalIndex:
    """In-memory TF-IDF over node text. Built once per query session."""

    __slots__ = ("_idf", "_node_ids", "_norm", "_tf")

    def __init__(self, nodes: Iterable[Node]) -> None:
        self._tf: dict[str, Counter[str]] = {}
        self._node_ids: list[str] = []
        df: Counter[str] = Counter()

        for node in nodes:
            # Index the normalized restatement when L1 has run, else raw.
            # `raw` is never mutated (invariant 2), so this is a read of two
            # separate columns, not a fallback that loses information.
            text = node.normalized or node.raw
            counts = Counter(tokenize(text))
            if not counts:
                continue
            self._tf[node.node_id] = counts
            self._node_ids.append(node.node_id)
            df.update(counts.keys())

        n_docs = max(1, len(self._node_ids))
        self._idf: dict[str, float] = {
            term: math.log(1 + n_docs / (1 + freq)) for term, freq in df.items()
        }
        self._norm: dict[str, float] = {}
        for node_id, counts in self._tf.items():
            weights = [c * self._idf.get(t, 0.0) for t, c in counts.items()]
            self._norm[node_id] = math.sqrt(sum(w * w for w in weights)) or 1.0

    def score(self, query: str, node_id: str) -> float:
        counts = self._tf.get(node_id)
        if counts is None:
            return 0.0
        q_terms = tokenize(query)
        if not q_terms:
            return 0.0
        dot = 0.0
        for term in set(q_terms):
            if term in counts:
                w = self._idf.get(term, 0.0)
                dot += (counts[term] * w) * w
        return dot / self._norm.get(node_id, 1.0)

    def search(self, query: str, top_k: int = 10) -> list[Scored]:
        scored = [
            Scored(node_id, s)
            for node_id in self._node_ids
            if (s := self.score(query, node_id)) > 0.0
        ]
        scored.sort(key=lambda x: (-x.score, x.node_id))
        return scored[:top_k]

    def relevance(self, query: str) -> Callable[[Node], float]:
        """A `query_relevance` callable for `context_frontier`, normalized to
        roughly [0, 1] against the best-scoring node for this query."""
        best = max((s.score for s in self.search(query, top_k=1)), default=0.0) or 1.0

        def fn(node: Node) -> float:
            return min(1.0, self.score(query, node.node_id) / best)

        return fn


def seed_nodes(nodes: Sequence[Node], query: str, top_k: int = 10) -> list[str]:
    """Convenience: build an index and return the top-k node ids."""
    index = LexicalIndex(nodes)
    return [s.node_id for s in index.search(query, top_k)]
