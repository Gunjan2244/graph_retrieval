"""The graph access seam.

Traversal logic is pure and must be testable against a fixture graph with no
database (CLAUDE.md code conventions). Everything in `expand`, `assemble`, and
`soundness` depends only on this Protocol, so the same code runs over an
in-memory fixture in unit tests and over a SQLite bundle in production.

`incoming()` is the reverse index and is load-bearing, not an optimisation
(CLAUDE.md invariant 7): an exception points AT the rule it modifies, so
following outgoing edges never finds it. Any implementation of this Protocol
must back `incoming()` with something equivalent to `idx_edges_dst`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Protocol

from dge.model import Edge, Node


class Graph(Protocol):
    """Read access to a node/edge graph.

    Implementations must be side-effect free: traversal calls these repeatedly
    and assumes a stable view for the duration of one query.
    """

    def get_node(self, node_id: str) -> Node | None: ...

    def outgoing(self, node_id: str) -> Sequence[Edge]:
        """Edges whose `src` is `node_id`. Forward traversal."""
        ...

    def incoming(self, node_id: str) -> Sequence[Edge]:
        """Edges whose `dst` is `node_id`. REVERSE traversal — this is where
        closure lives. See the module docstring."""
        ...

    def degree(self, node_id: str) -> int:
        """Total edges touching `node_id`, in either direction.

        Feeds the degree penalty in `policy.frontier_score` — 'IDF for graphs'.
        Hubs (boilerplate, 'the Agreement') are what make a graph feel fully
        connected while carrying almost no information.
        """
        ...


class FixtureGraph:
    """In-memory `Graph`, built from plain lists. No I/O, no database.

    This is the test substrate for all traversal behaviour, and the reference
    implementation the SQLite-backed reader must agree with.
    """

    __slots__ = ("_edges", "_in", "_nodes", "_out")

    def __init__(self, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        self._nodes: dict[str, Node] = {n.node_id: n for n in nodes}
        self._edges: list[Edge] = list(edges)
        self._out: dict[str, list[Edge]] = defaultdict(list)
        self._in: dict[str, list[Edge]] = defaultdict(list)
        for e in self._edges:
            self._out[e.src].append(e)
            self._in[e.dst].append(e)

    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def outgoing(self, node_id: str) -> Sequence[Edge]:
        return self._out.get(node_id, ())

    def incoming(self, node_id: str) -> Sequence[Edge]:
        return self._in.get(node_id, ())

    def degree(self, node_id: str) -> int:
        return len(self._out.get(node_id, ())) + len(self._in.get(node_id, ()))

    # -- conveniences used by assembly and tests; not part of the Protocol ---

    @property
    def nodes(self) -> Sequence[Node]:
        return list(self._nodes.values())

    @property
    def edges(self) -> Sequence[Edge]:
        return list(self._edges)
