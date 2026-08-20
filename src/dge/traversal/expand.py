"""Expansion: closure to a fixed point, context on a budget.

These are deliberately two functions, not one function with a depth parameter
(CLAUDE.md invariant 6). Depth is the wrong knob: in a dense graph depth-N
reaches everything for small N while still truncating the 4-hop closure chain
that actually mattered.

    closure_fixpoint()  unbudgeted, policy-directed, runs until nothing new
    context_frontier()  best-first, token-budgeted, saturation-terminated

Both share `closure_neighbors()` so the traversal and the soundness check can
never drift apart in what they consider "a closure neighbour" — if they did,
the guarantee in `soundness.py` would be checking a different graph than the
one traversal walked.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from dge.model import Edge, EdgeClass, Node
from dge.traversal.graph import Graph
from dge.traversal.policy import (
    Budget,
    Direction,
    EdgePolicy,
    frontier_score,
    is_saturated,
    resolve_policy,
)

PackOverrides = dict[str, EdgePolicy] | None  # matches policy.resolve_policy's own param type

# Rough token estimate. Deliberately crude and swappable: the budget is a
# policy dial, and over-estimating spends the budget conservatively, which is
# the safe direction. Callers with a real tokenizer pass their own.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class Arrival:
    """How a node entered the collected set. `via` is None for seeds."""

    node_id: str
    hops: int
    via: Edge | None = None
    score: float = 1.0


@dataclass(frozen=True, slots=True)
class SkippedEdge:
    """A closure edge that existed but was not followed, and why.

    Never silently dropped: CLAUDE.md invariant 9 is "degrade to labeled low
    confidence, never to confident wrong". A caller that ignores these is
    choosing to, and the soundness report can still surface them.
    """

    edge: Edge
    neighbor_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ClosureResult:
    reached: tuple[str, ...]
    arrivals: Mapping[str, Arrival]
    skipped: tuple[SkippedEdge, ...] = ()

    @property
    def expanded_only(self) -> tuple[str, ...]:
        """Nodes reached by traversal, excluding the seeds (hops == 0)."""
        return tuple(n for n in self.reached if self.arrivals[n].hops > 0)


@dataclass(frozen=True, slots=True)
class ContextResult:
    reached: tuple[str, ...]
    arrivals: Mapping[str, Arrival]
    tokens_used: int
    stopped_because: str          # 'budget' | 'saturated' | 'frontier_empty'
    edge_types_fired: tuple[str, ...] = ()


def _policy_for(edge: Edge, pack_overrides: PackOverrides) -> EdgePolicy:
    return resolve_policy(edge.type.value, edge.cls.value, pack_overrides)


def closure_neighbors(
    graph: Graph,
    node_id: str,
    *,
    pack_overrides: PackOverrides = None,
    min_confidence: float = 0.0,
) -> tuple[list[tuple[Edge, str]], list[SkippedEdge]]:
    """One-hop closure neighbours of `node_id`, honouring each edge type's own
    policy direction.

    Direction is per edge type, not global, and getting this wrong is the
    single most consequential bug available in this file:

      - `exception_of` / `supersedes` / `amends` are REVERSE. The exception is
        the `src` and points at the rule; we arrive from the rule, so the
        neighbour is `edge.src` found via `incoming()`.
      - `defines` is FORWARD. We arrive at a usage and walk to the definition,
        so the neighbour is `edge.dst` found via `outgoing()`.

    Returns (followed, skipped). Only edges whose CLASS is closure are
    considered here; context edges are `context_frontier`'s job.
    """
    followed: list[tuple[Edge, str]] = []
    skipped: list[SkippedEdge] = []

    def consider(edge: Edge, neighbor_id: str, wanted: Direction) -> None:
        if edge.cls is not EdgeClass.CLOSURE:
            return
        policy = _policy_for(edge, pack_overrides)
        if policy.direction is Direction.NONE:
            return
        if policy.direction is not wanted and policy.direction is not Direction.BOTH:
            return
        if edge.confidence < min_confidence:
            skipped.append(SkippedEdge(
                edge, neighbor_id,
                f"confidence {edge.confidence:.2f} < min_confidence {min_confidence:.2f}",
            ))
            return
        followed.append((edge, neighbor_id))

    for e in graph.incoming(node_id):
        consider(e, e.src, Direction.REVERSE)
    for e in graph.outgoing(node_id):
        consider(e, e.dst, Direction.FORWARD)

    return followed, skipped


def closure_fixpoint(
    graph: Graph,
    seeds: Sequence[str],
    *,
    pack_overrides: PackOverrides = None,
    min_confidence: float = 0.0,
    max_iterations: int = 10_000,
) -> ClosureResult:
    """Traverse closure edges until nothing new arrives. No budget, no depth
    limit (CLAUDE.md invariant 6) — except where a specific edge type declares
    its own `max_hops`, which is how `defines` stays at 2 hops per
    docs/04 §4.4 while `exception_of` runs unbounded.

    Cycles are real in legal text ("A qualifies B, B qualifies A"), so the
    visited set is mandatory, not an optimisation.
    """
    arrivals: dict[str, Arrival] = {}
    order: list[str] = []
    skipped: list[SkippedEdge] = []

    queue: list[str] = []
    for s in seeds:
        if s not in arrivals:
            arrivals[s] = Arrival(s, hops=0, via=None)
            order.append(s)
            queue.append(s)

    iterations = 0
    while queue:
        iterations += 1
        if iterations > max_iterations:
            # A malformed graph should not hang a query. Report what we have.
            break
        current = queue.pop(0)
        current_hops = arrivals[current].hops

        followed, skipped_here = closure_neighbors(
            graph, current, pack_overrides=pack_overrides, min_confidence=min_confidence
        )
        skipped.extend(skipped_here)

        for edge, neighbor_id in followed:
            policy = _policy_for(edge, pack_overrides)
            next_hops = current_hops + 1
            if policy.max_hops is not None and next_hops > policy.max_hops:
                skipped.append(SkippedEdge(
                    edge, neighbor_id,
                    f"{edge.type.value} policy caps at {policy.max_hops} hops",
                ))
                continue
            if neighbor_id in arrivals:
                continue
            if graph.get_node(neighbor_id) is None:
                # Dangling edge — record it rather than crashing the query.
                skipped.append(SkippedEdge(edge, neighbor_id, "node not in graph"))
                continue
            arrivals[neighbor_id] = Arrival(neighbor_id, hops=next_hops, via=edge)
            order.append(neighbor_id)
            queue.append(neighbor_id)

    return ClosureResult(tuple(order), arrivals, tuple(skipped))


_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")
_STOPWORDS = frozenset(["the", "and", "for", "that", "this", "with", "shall", "any", "such", "other", "under", "section", "act", "sub", "not", "been", "are", "was", "were", "has", "have", "had", "its", "his", "her", "they", "them", "their", "which", "who", "whom"])


def default_key_extractor(node: Node) -> frozenset[str]:
    """Content keys used for saturation detection.

    docs/04 §4.5 says to track "entities, terms, and claims" already present
    and stop when new arrivals stop introducing new ones. Without NER wired
    (that is L1/L4), content words are the honest proxy: crude, but it fires
    on redundancy rather than on depth, which is the property that matters.
    """
    text = (node.normalized or node.raw).lower()
    return frozenset(w for w in _WORD.findall(text) if w not in _STOPWORDS)


def context_frontier(
    graph: Graph,
    *,
    seeds: Sequence[str],
    already_included: Sequence[str] = (),
    query_relevance: Callable[[Node], float],
    budget: Budget | None = None,
    pack_overrides: PackOverrides = None,
    token_estimator: Callable[[str], int] = estimate_tokens,
    key_extractor: Callable[[Node], frozenset[str]] = default_key_extractor,
) -> ContextResult:
    """Best-first, budgeted expansion over CONTEXT edges.

    Replaces BFS-by-depth with a scored priority frontier so that a highly
    relevant node 5 hops out beats a marginal one at 2 hops — the behaviour
    depth limits get exactly backwards (docs/04 §4.4).

    `already_included` is the seed set plus everything closure pulled in. Those
    nodes are not re-emitted, but they DO seed the frontier and they DO consume
    the token budget, because the budget is on the assembled context as a whole
    and closure is non-optional. Context gets whatever is left.
    """
    budget = budget or Budget()
    included: set[str] = set(seeds) | set(already_included)

    tokens_used = 0
    seen_keys: set[str] = set()
    for node_id in included:
        node = graph.get_node(node_id)
        if node is None:
            continue
        tokens_used += token_estimator(node.for_assembly())
        seen_keys |= key_extractor(node)

    heap: list[tuple[float, int, str, int, Edge]] = []
    counter = 0

    def push_neighbors(node_id: str, hops: int) -> None:
        nonlocal counter
        for edge, neighbor_id, _direction in _context_neighbors(graph, node_id, pack_overrides):
            if neighbor_id in included:
                continue
            neighbor = graph.get_node(neighbor_id)
            if neighbor is None:
                continue
            policy = _policy_for(edge, pack_overrides)
            next_hops = hops + 1
            if policy.max_hops is not None and next_hops > policy.max_hops:
                continue
            if edge.confidence < budget.min_edge_confidence:
                continue
            score = frontier_score(
                prior=policy.prior,
                query_relevance=query_relevance(neighbor),
                hops=next_hops,
                degree=graph.degree(neighbor_id),
                cross_doc=edge.cross_doc,
                edge_confidence=edge.confidence,
                budget=budget,
            )
            counter += 1
            heapq.heappush(heap, (-score, counter, neighbor_id, next_hops, edge))

    for node_id in list(included):
        push_neighbors(node_id, 0)

    arrivals: dict[str, Arrival] = {}
    order: list[str] = []
    edge_types: list[str] = []
    new_keys_per_arrival: list[int] = []
    stopped = "frontier_empty"

    while heap:
        neg_score, _tie, node_id, hops, edge = heapq.heappop(heap)
        if node_id in included:
            continue
        node = graph.get_node(node_id)
        if node is None:
            continue

        cost = token_estimator(node.for_assembly())
        if tokens_used + cost > budget.max_tokens:
            stopped = "budget"
            break

        included.add(node_id)
        arrivals[node_id] = Arrival(node_id, hops=hops, via=edge, score=-neg_score)
        order.append(node_id)
        edge_types.append(edge.type.value)
        tokens_used += cost

        keys = key_extractor(node)
        new_keys_per_arrival.append(len(keys - seen_keys))
        seen_keys |= keys

        if is_saturated(new_keys_per_arrival, budget.saturation_window):
            stopped = "saturated"
            break

        push_neighbors(node_id, hops)

    return ContextResult(
        tuple(order), arrivals, tokens_used, stopped, tuple(dict.fromkeys(edge_types))
    )


def _context_neighbors(
    graph: Graph, node_id: str, pack_overrides: PackOverrides
) -> list[tuple[Edge, str, Direction]]:
    """One-hop CONTEXT neighbours, honouring policy direction.

    `similar_to` resolves to Direction.NONE and is never traversed — that is
    what seeding was for, and following it fans out into the whole corpus
    (docs/04 §4.4).
    """
    out: list[tuple[Edge, str, Direction]] = []
    for e in graph.incoming(node_id):
        if e.cls is EdgeClass.CLOSURE:
            continue
        policy = _policy_for(e, pack_overrides)
        if policy.direction in (Direction.REVERSE, Direction.BOTH):
            out.append((e, e.src, Direction.REVERSE))
    for e in graph.outgoing(node_id):
        if e.cls is EdgeClass.CLOSURE:
            continue
        policy = _policy_for(e, pack_overrides)
        if policy.direction in (Direction.FORWARD, Direction.BOTH):
            out.append((e, e.dst, Direction.FORWARD))
    return out
