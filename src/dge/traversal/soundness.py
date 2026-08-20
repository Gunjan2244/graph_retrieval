"""The soundness check — the guarantee the product exists to make.

docs/04 §4.7: after the model answers with node citations, for every cited node
look up its closure neighbours. If any was not in the assembled context, the
answer is unsound: expand to include it and re-run.

This converts the architectural claim from a hope into a stateable guarantee:

    the system cannot answer from a rule while omitting a known exception to
    it, or from a version known to be superseded.

Note the honest scope of that claim — *known* exceptions. This is a guarantee
about consistency between the graph and the answer, not about the completeness
of extraction. Extraction recall is an eval number (docs/06), not a guarantee,
and saying so plainly is what makes the guarantee credible.

The neighbour lookup deliberately reuses `expand.closure_neighbors`, so the
check and the traversal can never disagree about what a closure neighbour is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dge.model import Edge
from dge.traversal.expand import PackOverrides, SkippedEdge, closure_neighbors
from dge.traversal.graph import Graph
from dge.traversal.policy import resolve_policy


@dataclass(frozen=True, slots=True)
class Violation:
    """A cited node whose closure neighbour never made it into the context."""

    cited_node_id: str
    missing_node_id: str
    edge: Edge
    mandatory: bool

    def describe(self) -> str:
        kind = "MANDATORY" if self.mandatory else "closure"
        return (
            f"{self.cited_node_id} cites a node with an un-included {kind} "
            f"neighbour {self.missing_node_id} via {self.edge.type.value} "
            f"(confidence {self.edge.confidence:.2f})"
        )


@dataclass(frozen=True, slots=True)
class SoundnessReport:
    ok: bool
    violations: tuple[Violation, ...]
    skipped: tuple[SkippedEdge, ...]
    checked: int

    @property
    def missing_node_ids(self) -> tuple[str, ...]:
        """Nodes to add before re-running. Feed straight back into traversal."""
        return tuple(dict.fromkeys(v.missing_node_id for v in self.violations))

    def summary(self) -> str:
        if self.ok and not self.skipped:
            return f"sound: {self.checked} cited node(s), no missing closure neighbours"
        parts = []
        if self.violations:
            parts.append(f"{len(self.violations)} violation(s)")
        if self.skipped:
            parts.append(f"{len(self.skipped)} low-confidence/capped edge(s) not followed")
        return f"UNSOUND: {', '.join(parts)} over {self.checked} cited node(s)"


def check_soundness(
    graph: Graph,
    *,
    cited_node_ids: Sequence[str],
    context_node_ids: Sequence[str],
    pack_overrides: PackOverrides = None,
    min_confidence: float = 0.0,
) -> SoundnessReport:
    """One index lookup per citation — cheap, and it is the whole guarantee.

    `min_confidence` filters at traversal time rather than deleting at ingest
    time (CLAUDE.md invariant 5). Anything filtered out is reported in
    `skipped` rather than dropped, so a caller can surface "there is a
    low-confidence exception here" instead of silently answering as if the
    graph were clean.
    """
    in_context = set(context_node_ids)
    violations: list[Violation] = []
    skipped: list[SkippedEdge] = []

    for cited in cited_node_ids:
        followed, skipped_here = closure_neighbors(
            graph, cited, pack_overrides=pack_overrides, min_confidence=min_confidence
        )
        skipped.extend(skipped_here)
        for edge, neighbor_id in followed:
            if neighbor_id in in_context:
                continue
            if graph.get_node(neighbor_id) is None:
                continue
            policy = resolve_policy(edge.type.value, edge.cls.value, pack_overrides)
            violations.append(
                Violation(cited, neighbor_id, edge, mandatory=policy.mandatory)
            )

    return SoundnessReport(
        ok=not violations,
        violations=tuple(violations),
        skipped=tuple(skipped),
        checked=len(cited_node_ids),
    )
