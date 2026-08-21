"""Query orchestration — the pipeline of docs/04 §4.1, end to end.

    query
      ├─ seed (lexical, or hybrid dense+sparse, optionally reranked)
      ├─ CLOSURE expansion   (fixed point, unbudgeted)   → soundness
      ├─ CONTEXT expansion   (best-first, budgeted)      → helpfulness
      ├─ assemble in document order + inherited context
      └─ soundness check

This module supplies context; it does not answer. Answer generation is an
explicit anti-goal (CLAUDE.md, docs/01 §1.3) — we hand an agent the assembled
context plus a soundness verdict, and it does the reasoning.

Two soundness checks exist and they are not the same thing:

  `QueryResult.soundness` is a PRE-answer self-consistency check: it asks
  whether traversal actually delivered every closure neighbour of its own
  seeds. It should essentially always pass, and a failure means the traversal
  has a bug — it is a regression guard, not the product guarantee.

  `verify_answer()` is the real, POST-answer check from docs/04 §4.7, run
  against the nodes a model actually cited. That is the guarantee.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from dge.domains.legal import DomainPack
from dge.interfaces import Reranker
from dge.l3.conflict import OverrideConflict, detect_override_conflicts
from dge.model import Node
from dge.retrieval.lexical import LexicalIndex
from dge.traversal.assemble import AssembledContext, assemble
from dge.traversal.expand import (
    ClosureResult,
    ContextResult,
    PackOverrides,
    closure_fixpoint,
    context_frontier,
)
from dge.traversal.graph import Graph
from dge.traversal.policy import Budget
from dge.traversal.soundness import SoundnessReport, check_soundness

Seeder = Callable[[str, int], Sequence[str]]


def rerank_seeder(
    base_seeder: Seeder, graph: Graph, reranker: Reranker, *, candidate_multiplier: int = 4,
) -> Seeder:
    """Wrap a `Seeder` with a cross-encoder rerank pass — docs/06 §6.3's
    "hybrid + rerank" baseline. `base_seeder` is over-called for
    `top_k * candidate_multiplier` candidates (rerank's job is to re-order a
    wider recall pool precisely, not to be the recall stage itself), resolved
    to `Node` objects, reranked, then truncated to `top_k`.

    Composable with any `Seeder` — lexical-only or the dense+sparse RRF
    fusion in `dge.cli._make_hybrid_seeder` — so rerank is an independent flag
    from `--use-vectors`, not a variant of it.
    """

    def seed(query: str, top_k: int) -> list[str]:
        candidate_ids = base_seeder(query, top_k * candidate_multiplier)
        candidates = [n for n in (graph.get_node(i) for i in candidate_ids) if n is not None]
        ranked = reranker.rerank(query, candidates, top_k)
        return [node.node_id for node, _score in ranked]

    return seed


@dataclass(frozen=True, slots=True)
class QueryResult:
    query: str
    seeds: tuple[str, ...]
    closure: ClosureResult
    context: ContextResult
    assembled: AssembledContext
    soundness: SoundnessReport
    # Competing override claims touching the assembled context. Reported, never
    # resolved (docs/07 7.2). Empty unless a pack is passed to `run_query` —
    # recognising an override claim is pack knowledge, not engine knowledge.
    conflicts: tuple[OverrideConflict, ...] = ()

    @property
    def all_node_ids(self) -> tuple[str, ...]:
        return self.assembled.node_ids

    def provenance_of(self, node_id: str) -> str:
        """Where a node came from: seed / closure / context.

        This is the three-way split docs/06 §6.1 calls the only way to tell
        whether to fix the embedder, the extractor, or the budgets.
        """
        if node_id in self.seeds:
            return "seed"
        if node_id in self.closure.arrivals:
            return "closure"
        if node_id in self.context.arrivals:
            return "context"
        return "unknown"


def run_query(
    graph: Graph,
    query: str,
    *,
    nodes: Sequence[Node] | None = None,
    top_k: int = 8,
    budget: Budget | None = None,
    pack_overrides: PackOverrides = None,
    min_closure_confidence: float = 0.0,
    glosses: Mapping[str, str] | None = None,
    seeder: Seeder | None = None,
    pack: DomainPack | None = None,
) -> QueryResult:
    """Run the full retrieve → closure → context → assemble → verify pipeline.

    `nodes` is the corpus to seed over. When omitted, it is read from the graph
    if the implementation can enumerate (BundleGraph can; a bare Protocol
    cannot), which is why the parameter exists.
    """
    budget = budget or Budget()

    if nodes is None:
        all_nodes = getattr(graph, "all_nodes", None)
        if all_nodes is None:
            raise ValueError(
                "nodes must be supplied for graphs that cannot enumerate their nodes"
            )
        nodes = all_nodes()

    index = LexicalIndex(nodes)
    if seeder is None:
        seeds = tuple(s.node_id for s in index.search(query, top_k))
    else:
        seeds = tuple(seeder(query, top_k))

    # Closure first and unbudgeted: omitting one of these makes the answer
    # wrong, so it is not allowed to compete with context for budget.
    closure = closure_fixpoint(
        graph, seeds,
        pack_overrides=pack_overrides,
        min_confidence=min_closure_confidence,
    )

    context = context_frontier(
        graph,
        seeds=seeds,
        already_included=closure.reached,
        query_relevance=index.relevance(query),
        budget=budget,
        pack_overrides=pack_overrides,
    )

    collected_ids = list(dict.fromkeys([*closure.reached, *context.reached]))
    collected = [n for n in (graph.get_node(i) for i in collected_ids) if n is not None]
    assembled = assemble(collected, glosses=glosses, arrivals=closure.arrivals)

    soundness = check_soundness(
        graph,
        cited_node_ids=seeds,
        context_node_ids=assembled.node_ids,
        pack_overrides=pack_overrides,
        min_confidence=min_closure_confidence,
    )

    # Conflict findings are derived, not stored (see dge.l3.conflict): they are
    # recomputed here over the assembled neighbourhood so they cannot go stale,
    # and reported alongside the soundness verdict because for a clause-level
    # seed they are the ONLY thing that surfaces a mutual override — closure
    # traversal reaches the competitor only from the section heading.
    conflicts: tuple[OverrideConflict, ...] = ()
    if pack is not None:
        in_context = set(assembled.node_ids)
        conflicts = tuple(
            c for c in detect_override_conflicts(nodes, pack)
            if in_context.intersection(c.node_ids)
        )

    return QueryResult(query, seeds, closure, context, assembled, soundness, conflicts)


def verify_answer(
    graph: Graph,
    *,
    cited_node_ids: Sequence[str],
    context_node_ids: Sequence[str],
    pack_overrides: PackOverrides = None,
    min_confidence: float = 0.0,
) -> SoundnessReport:
    """The post-answer soundness check of docs/04 §4.7 — the guarantee.

    Call this with the nodes the model actually cited. If it comes back
    unsound, add `report.missing_node_ids` to the context and re-run the model
    rather than shipping the answer.
    """
    return check_soundness(
        graph,
        cited_node_ids=cited_node_ids,
        context_node_ids=context_node_ids,
        pack_overrides=pack_overrides,
        min_confidence=min_confidence,
    )
