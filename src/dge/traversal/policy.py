"""Traversal policy.

Pure functions over a fixture graph — no database, no I/O. This is the
differentiating logic and must be testable in isolation.

Two rules that are easy to get wrong and are the whole point:

  1. Depth is the WRONG knob. In a dense graph depth-N reaches everything for
     small N, while still truncating the 4-hop closure chain that mattered. Use
     class-based policy + a scored frontier + saturation instead.

  2. Closure edges are traversed on the REVERSE index. An exception points AT
     the rule it modifies. Following outgoing edges never finds it, and
     retrieving a rule without its exception is worse than retrieving nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from dge.model import EdgeType


class Direction(StrEnum):
    FORWARD = "forward"    # follow src -> dst
    REVERSE = "reverse"    # follow dst -> src  (inbound; closure lives here)
    BOTH = "both"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class EdgePolicy:
    direction: Direction
    prior: float               # multiplier in the frontier score
    max_hops: int | None       # None = unbounded (closure runs to fixed point)
    mandatory: bool = False    # ignore budget; omission makes the answer wrong
    gloss_only_after_hop: int | None = None   # definitions: full span at hop 1


# Config, not code. A per-domain ontology overrides this table.
DEFAULT_POLICY: dict[EdgeType, EdgePolicy] = {
    # ---- closure: unbudgeted, reverse, mandatory ----
    EdgeType.EXCEPTION_OF:   EdgePolicy(Direction.REVERSE, 1.0, None, mandatory=True),
    EdgeType.SUPERSEDES:     EdgePolicy(Direction.REVERSE, 1.0, None, mandatory=True),
    EdgeType.AMENDS:         EdgePolicy(Direction.REVERSE, 1.0, None, mandatory=True),
    EdgeType.CONDITIONED_ON: EdgePolicy(Direction.FORWARD, 1.0, None, mandatory=True),
    EdgeType.DEFINES:        EdgePolicy(Direction.FORWARD, 0.9, 2, gloss_only_after_hop=1),
    # ---- context: budgeted, cuttable ----
    EdgeType.ELABORATES:     EdgePolicy(Direction.FORWARD, 0.5, 2),
    EdgeType.EXEMPLIFIES:    EdgePolicy(Direction.FORWARD, 0.3, 1),
    EdgeType.SUPPORTS:       EdgePolicy(Direction.FORWARD, 0.3, 1),
    EdgeType.CONTRADICTS:    EdgePolicy(Direction.BOTH,    0.6, 1),
    EdgeType.CITES:          EdgePolicy(Direction.FORWARD, 0.4, 1),
    EdgeType.PART_OF:        EdgePolicy(Direction.FORWARD, 0.4, 1),
    # Never traversed — that is what seeding was for. Following it fans out
    # combinatorially and drags in the whole corpus.
    EdgeType.SIMILAR_TO:     EdgePolicy(Direction.NONE,    0.0, 0),
    # Weak identity: surfaced at low weight, never treated as a merge.
    EdgeType.POSSIBLY_SAME_AS: EdgePolicy(Direction.BOTH,  0.2, 1),
}


@dataclass(frozen=True, slots=True)
class Budget:
    max_tokens: int = 8000
    cross_doc_penalty: float = 0.4
    hop_decay: float = 0.7
    min_edge_confidence: float = 0.5
    saturation_window: int = 5      # stop after N arrivals adding nothing new


def frontier_score(
    *,
    prior: float,
    query_relevance: float,
    hops: int,
    degree: int,
    cross_doc: bool,
    edge_confidence: float,
    budget: Budget,
) -> float:
    """Best-first priority. A highly relevant node 5 hops out should beat a
    marginal one at 2 hops — which is exactly what depth limits get wrong.

    The degree term is IDF for graphs. High fan-out nodes are hubs (boilerplate,
    'the Agreement', ubiquitous terms). Hubs are what make a graph *feel* fully
    connected while carrying almost no information; penalising them by degree
    collapses the effective branching factor more than any depth limit does.
    """
    return (
        prior
        * query_relevance
        * (budget.hop_decay ** hops)
        * (1.0 / math.log1p(1 + degree))
        * (budget.cross_doc_penalty if cross_doc else 1.0)
        * edge_confidence
    )


# ---------------------------------------------------------------------------
# Domain pack seam
#
# Packs contribute edge type NAMES (strings) plus a class. The engine must be
# able to policy them without importing anything domain-specific. Resolution
# order: explicit pack policy -> DEFAULT_POLICY by name -> class default.
#
# This is the boundary referenced in CLAUDE.md invariant 11. Adding a domain
# must never require editing the table above.
# ---------------------------------------------------------------------------

CLOSURE_DEFAULT = EdgePolicy(Direction.REVERSE, 1.0, None, mandatory=True)
CONTEXT_DEFAULT = EdgePolicy(Direction.FORWARD, 0.4, 1)


def resolve_policy(
    edge_type: str,
    edge_class_name: str,
    pack_overrides: dict[str, EdgePolicy] | None = None,
) -> EdgePolicy:
    """Policy for any edge type, including ones this module has never heard of.

    Unknown closure types default to REVERSE + mandatory + unbounded. That is the
    safe direction: a domain edge we do not recognise but that is declared
    closure must not be silently dropped from the soundness closure.
    """
    if pack_overrides and edge_type in pack_overrides:
        return pack_overrides[edge_type]
    for known in DEFAULT_POLICY:
        if known.value == edge_type:
            return DEFAULT_POLICY[known]
    return CLOSURE_DEFAULT if edge_class_name == "closure" else CONTEXT_DEFAULT


def is_saturated(new_keys_per_arrival: list[int], window: int) -> bool:
    """Stop expanding when recent arrivals stop introducing new entities, terms,
    or claims. Redundancy is the signal that the neighbourhood is covered — and
    it fires at different depths for different queries, which is the point.
    """
    if len(new_keys_per_arrival) < window:
        return False
    return sum(new_keys_per_arrival[-window:]) == 0
