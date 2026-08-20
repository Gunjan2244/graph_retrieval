"""L3a: deterministic + pattern edges (BUILD_PLAN Phase 3, first two bullets).

No model call. Two pack-declared sources, both free:

  - `pack.structural_units` — genre-specific structural elements (proviso,
    explanation, illustration, ...) matched at the start of a node's text.
  - `pack.markers` — lexical markers anywhere in a node's text.

Orientation (which node becomes `src`, which becomes `dst`) is not carried by
`MarkerPattern`/`StructuralUnit` — packs only declare an edge *type* and a
confidence. The engine derives orientation from what the edge type means: for
most CLOSURE edges the modifier is `src` and the modified provision is `dst`
(`dge.traversal.policy` — "an exception points AT the rule it modifies"), the
one exception being `defines`, which runs the other way: the *usage* is `src`
and the definition is `dst`, so a traversal that starts at a node mentioning a
term can walk forward to its meaning. `MARKER_ORIENTATION` below is core-engine
knowledge about `dge.model.EdgeType`, not a pack concern, so it stays out of
`dge/domains/legal.py` (CLAUDE.md invariant 11).

Every edge produced here carries an `evidence_span` that is required to be a
verbatim substring of the node it was matched on; `validate_evidence_span`
enforces CLAUDE.md invariant 10 and is reused as-is by any future model-backed
`EdgeExtractor` — the check must live in code, not in a prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence

from dge.domains.legal import DomainPack, MarkerPattern
from dge.model import Edge, EdgeType, Node, NodeKind, Provenance

Orientation = Literal["marker_is_src", "marker_is_dst"]

# Default is "marker_is_src" (modifier -> modified), matching CLOSURE_DEFAULT's
# REVERSE convention in dge.traversal.policy. DEFINES is the one core type
# whose traversal policy runs forward (usage -> definition), so it inverts.
MARKER_ORIENTATION: dict[EdgeType, Orientation] = {
    EdgeType.DEFINES: "marker_is_dst",
}

# Section number extraction, from either heading style: the explicit-word
# form ("Section 186.") or the bare form real Indian bare acts actually use
# ("186. Obstructing public servant..."). Both key the registry on the same
# normalized number+suffix ("186", "498A"), so a `referenced` marker written
# as "section 9" resolves against a heading written either way.
_HEADING_KEY_RE = re.compile(
    r"^\s*(?:(?:Section|Chapter|Article)\s+)?(\d{1,4}[A-Z]{0,2})\.", re.IGNORECASE,
)
_REF_RE = re.compile(r"section\s+(\d+[A-Za-z]*)", re.IGNORECASE)


def _orientation(edge_type: EdgeType) -> Orientation:
    return MARKER_ORIENTATION.get(edge_type, "marker_is_src")


def validate_evidence_span(evidence_span: str | None, input_window: str) -> bool:
    """CLAUDE.md invariant 10: an edge whose evidence_span is not verbatim in
    the input window is discarded. Enforced here in code, applied uniformly to
    pattern edges and (by any future adapter) model-extracted candidates."""
    if evidence_span is None:
        return True
    return evidence_span in input_window


@dataclass
class _SectionCursor:
    """Structural position of every node, computed once in document order."""

    section_registry: dict[str, str]        # '12' -> heading node_id (normalized, no word prefix)
    prev_in_section: dict[str, str | None]   # node_id -> preceding node_id (same section)
    next_in_section: dict[str, str | None]   # node_id -> following node_id (same section)
    section_of: dict[str, str | None]        # node_id -> enclosing section heading node_id


def _heading_key(text: str) -> str | None:
    m = _HEADING_KEY_RE.search(text)
    if not m:
        return None
    return m.group(1).upper()


def _build_cursor(nodes: Sequence[Node]) -> _SectionCursor:
    section_registry: dict[str, str] = {}
    prev_in_section: dict[str, str | None] = {}
    next_in_section: dict[str, str | None] = {}
    section_of: dict[str, str | None] = {}

    current_section: str | None = None
    prev_node: str | None = None

    for node in nodes:
        if node.kind is NodeKind.STRUCTURAL:
            key = _heading_key(node.raw)
            if key:
                section_registry[key] = node.node_id
            current_section = node.node_id
            prev_node = None

        section_of[node.node_id] = current_section
        prev_in_section[node.node_id] = prev_node
        next_in_section[node.node_id] = None
        if prev_node is not None:
            next_in_section[prev_node] = node.node_id
        if node.kind is not NodeKind.STRUCTURAL:
            prev_node = node.node_id

    return _SectionCursor(section_registry, prev_in_section, next_in_section, section_of)


def _orient(marker_node: str, target_node: str, edge_type: EdgeType) -> tuple[str, str]:
    if _orientation(edge_type) == "marker_is_dst":
        return target_node, marker_node
    return marker_node, target_node


def extract_structural_edges(nodes: Sequence[Node], pack: DomainPack) -> list[Edge]:
    """Free edges from `pack.structural_units` (proviso/explanation/illustration/...)."""
    cursor = _build_cursor(nodes)
    edges: list[Edge] = []
    for node in nodes:
        if node.kind is NodeKind.STRUCTURAL:
            continue
        for unit in pack.structural_units:
            if not unit.regex.match(node.raw):
                continue
            target = cursor.prev_in_section.get(node.node_id) or cursor.section_of.get(node.node_id)
            if target is None or target == node.node_id:
                break
            src, dst = _orient(node.node_id, target, unit.edge_type)
            evidence = node.raw[:200]
            if not validate_evidence_span(evidence, node.raw):
                break  # unreachable by construction, kept for parity with the model path
            edges.append(Edge(
                edge_id=f"struct:{unit.name}:{node.node_id}:{target}",
                src=src, dst=dst, type=unit.edge_type,
                provenance=Provenance.STRUCTURAL,
                confidence=1.0,
                evidence_span=evidence,
            ))
            break  # one structural classification per node
    return edges


def _resolve_target(node: Node, marker: MarkerPattern, cursor: _SectionCursor) -> str | None:
    hint = marker.target_hint
    if hint == "preceding":
        return cursor.prev_in_section.get(node.node_id) or cursor.section_of.get(node.node_id)
    if hint == "following":
        return cursor.next_in_section.get(node.node_id)
    if hint == "referenced":
        ref = _REF_RE.search(node.raw)
        if not ref:
            return None
        return cursor.section_registry.get(ref.group(1).upper())
    return None


def extract_marker_edges(nodes: Sequence[Node], pack: DomainPack) -> tuple[list[Edge], list[str]]:
    """Pattern edges from `pack.markers`.

    STRONG hits get full confidence; MEDIUM hits are still written (lower
    confidence) rather than dropped, because there is no LLM verifier wired
    into the offline core path here and CLAUDE.md invariant 5 says: filter at
    traversal time, never delete at ingest time. A `referenced` hint that
    cannot be resolved to a node in THIS corpus (e.g. a citation to an
    external Act, or a section number outside the ingested set) produces no
    edge — fabricating a link to a node that doesn't exist would violate
    invariant 10 in spirit even though no model was involved.
    """
    cursor = _build_cursor(nodes)
    edges: list[Edge] = []
    warnings: list[str] = []
    for node in nodes:
        if node.kind is NodeKind.STRUCTURAL:
            continue
        for marker in pack.markers:
            m = marker.regex.search(node.raw)
            if not m:
                continue
            target = _resolve_target(node, marker, cursor)
            if target is None or target == node.node_id:
                warnings.append(
                    f"{node.node_id}: marker '{marker.name}' matched but target "
                    f"(hint={marker.target_hint}) could not be resolved; edge skipped"
                )
                continue
            evidence = m.group(0)
            if not validate_evidence_span(evidence, node.raw):
                warnings.append(
                    f"{node.node_id}: marker '{marker.name}' evidence span not verbatim; discarded"
                )
                continue
            src, dst = _orient(node.node_id, target, marker.edge_type)
            confidence = 1.0 if marker.confidence.value == "strong" else 0.6
            edges.append(Edge(
                edge_id=f"marker:{marker.name}:{node.node_id}:{dst}",
                src=src, dst=dst, type=marker.edge_type,
                provenance=Provenance.PATTERN,
                confidence=confidence,
                evidence_span=evidence,
            ))
    return edges, warnings
