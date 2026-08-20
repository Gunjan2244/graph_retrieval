"""Term symbol table (BUILD_PLAN Phase 2, minimal slice): definition-site
detection and mention linking. Deterministic, no model call
(docs/05-engine-implementation.md 5.3: "Lexicon / link: None (regex +
Aho-Corasick)").

`pyahocorasick` (see pyproject `[symbols]` extra) is the intended accelerator
for the mention pass at corpus scale — a single streaming automaton pass
instead of N compiled regexes. `link_mentions` below does the regex version;
swapping in an Aho-Corasick automaton is a same-signature replacement for this
function once corpus size makes it worth the dependency, not a design change.

Scope resolution here is intentionally the minimal case the BUILD_PLAN calls
out as the headline feature: the nearest enclosing section heading is the
scope a definition binds in, so a `"for the purposes of this section"`
definition never leaks into a sibling section's glossary.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from dge.domains.legal import DomainPack
from dge.edges import validate_evidence_span
from dge.model import Edge, EdgeType, Node, NodeKind, Provenance, Term


def _definition_kind(pattern_src: str) -> str | None:
    if "means" in pattern_src:
        return "means"
    if "includes" in pattern_src:
        return "includes"
    return None


def extract_terms(nodes: Sequence[Node], pack: DomainPack) -> list[Term]:
    """One term per definition site. `definition_kind` distinguishes 'means'
    (exhaustive) from 'includes' (illustrative) — CLAUDE.md/BUILD_PLAN Phase 2:
    flattening that distinction produces confidently wrong scope answers."""
    terms: list[Term] = []
    current_scope: str | None = None
    for node in nodes:
        if node.kind is NodeKind.STRUCTURAL:
            current_scope = node.node_id
            continue
        if node.kind is NodeKind.FOOTNOTE:
            continue
        for idx, pattern in enumerate(pack.definition_patterns):
            m = pattern.search(node.raw)
            if not m or not m.groups():
                continue
            surface = (m.group(1) or "").strip()
            if not surface:
                continue
            terms.append(Term(
                term_id=f"{node.node_id}:term:{idx}",
                surface_form=surface,
                canonical=surface.lower(),
                scope_node_id=current_scope,
                definition_node_id=node.node_id,
                definition_kind=_definition_kind(pattern.pattern),
                gloss=node.raw[:200],
                provenance=Provenance.PATTERN,
                confidence=0.9,
            ))
            break  # one definition classification per node
    return terms


def _mention_regexes(terms: Sequence[Term]) -> list[tuple[Term, re.Pattern[str]]]:
    return [
        (t, re.compile(r"\b" + re.escape(t.surface_form) + r"\b", re.IGNORECASE))
        for t in terms
        if t.surface_form and t.definition_node_id
    ]


def link_mentions(nodes: Sequence[Node], terms: Sequence[Term]) -> list[Edge]:
    """DEFINES edges: src = the node that uses a term, dst = its definition
    node. This is the forward direction `dge.traversal.policy.DEFAULT_POLICY`
    expects for DEFINES (start at a usage, walk to the meaning) — the reverse
    of the exception/supersedes convention used elsewhere in `dge.edges`."""
    edges: list[Edge] = []
    if not terms:
        return edges
    compiled = _mention_regexes(terms)
    for node in nodes:
        if node.kind is NodeKind.STRUCTURAL or node.kind is NodeKind.FOOTNOTE:
            continue
        for term, rx in compiled:
            if node.node_id == term.definition_node_id:
                continue
            m = rx.search(node.raw)
            if not m:
                continue
            evidence = m.group(0)
            if not validate_evidence_span(evidence, node.raw):
                continue
            edges.append(Edge(
                edge_id=f"mention:{node.node_id}:{term.term_id}",
                src=node.node_id,
                dst=term.definition_node_id,  # type: ignore[arg-type]  # filtered above
                type=EdgeType.DEFINES,
                provenance=Provenance.PATTERN,
                confidence=0.8,
                evidence_span=evidence,
            ))
    return edges
