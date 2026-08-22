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
from collections.abc import Sequence
from dataclasses import dataclass
from re import Match, Pattern
from typing import Literal

from dge.domains.legal import DomainPack, MarkerPattern
from dge.l3.evidence import check_evidence
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
# as "section 9" resolves against a heading written either way. Also sees
# through India Code's amendment bracket ("5[31. Hours of work...") — see
# dge.parsing's `_AMD` — otherwise every amended heading is invisible to the
# registry and every `referenced` marker aimed at it silently drops the edge.
_HEADING_KEY_RE = re.compile(
    r"^\s*(?:(?:Section|Chapter|Article)\s+)?(?:\d{1,3}\[)*(\d{1,4}[A-Z]{0,2})\.", re.IGNORECASE,
)


def _orientation(edge_type: EdgeType) -> Orientation:
    return MARKER_ORIENTATION.get(edge_type, "marker_is_src")


def validate_evidence_span(evidence_span: str | None, input_window: str) -> bool:
    """CLAUDE.md invariant 10 for the PATTERN path — one boolean over the same
    enforcement point L3 uses (`dge.l3.evidence.check_evidence`), so the two
    paths cannot drift apart in what "verbatim" means.

    Two deliberate differences from the model path, both because the input is
    different in kind:

    - `None` passes. A pattern edge's span is produced by our own regex over
      the node's own text, so its absence means "this rule cites no span",
      not "this claim has no evidence". A model candidate with no span is
      discarded outright (`check_evidence` returns REJECTED/"missing") —
      there, absence IS the failure.
    - No reflow tolerance and no minimum length: a regex match is by
      construction an exact slice of the node it matched, so anything less
      than an exact substring here is a bug in this module, not a model
      being loose with whitespace.
    """
    if evidence_span is None:
        return True
    return check_evidence(
        evidence_span, input_window, allow_reflow=False, min_chars=0
    ).ok


@dataclass
class _SectionCursor:
    """Structural position of every node, computed once in document order.

    `prev_sibling`/`next_sibling`/`parent_of` come from the real nesting
    `dge.parsing` already built (its `PART_OF` edges), not a re-derived flat
    scan — a proviso's "preceding" target is its previous sibling under the
    same immediate parent (the sub-section or clause it modifies), not
    whatever node happens to sit next to it in document order. Without
    `structural_edges`, every node falls back to one flat sibling list in
    document order — enough for small hand-written fixtures with no real
    nesting, wrong for anything with sub-sections and clauses.
    """

    section_registry: dict[str, str]        # '12' -> heading node_id (normalized, no word prefix)
    prev_sibling: dict[str, str | None]      # node_id -> preceding node under the same parent
    next_sibling: dict[str, str | None]      # node_id -> following node under the same parent
    parent_of: dict[str, str | None]         # node_id -> immediate structural parent
    # (enclosing STRUCTURAL section node_id, enumerator) -> the sub-section
    # node_id. Keyed on the PAIR, never the enumerator alone: "(1)" recurs in
    # nearly every section of every Act, so a registry keyed on the bare
    # enumerator would map every sub-section (1) in the document onto
    # whichever one was written first — the fabrication bug `LEGAL_FOREIGN_REF`
    # exists to prevent, reproduced at the scale of the whole corpus instead of
    # one marker type. See `_enclosing_section` for how the section is found.
    subsection_registry: dict[tuple[str, str], str]
    structural_ids: frozenset[str]           # which node_ids are STRUCTURAL, for `_enclosing_section`'s walk
    subsection_warnings: list[str]           # duplicate-enumerator collisions found while building the registry above


def _heading_key(text: str) -> str | None:
    m = _HEADING_KEY_RE.search(text)
    if not m:
        return None
    return m.group(1).upper()


# A sub-section node's own enumerator, from the start of its `raw` text —
# "(1) No person shall..." -> "1". Restricted to digits with an optional
# trailing letter ("(2A)") so lettered clause markers ("(a)") are never
# swept in: clauses are a different population (see `LEGAL_SUBSECTION_REF`'s
# docstring) and folding them in here would corrupt both registries.
_SUBSECTION_ENUM_RE = re.compile(r"^\s*\(\s*(\d{1,3}[A-Za-z]?)\s*\)")

_MAX_ANCESTOR_WALK = 12  # generous over the deepest real nesting (chapter>section>sub-section>clause)


def _enclosing_section(
    node_id: str, parent_of: dict[str, str | None], structural_ids: frozenset[str],
) -> str | None:
    """Walk `parent_of` upward from `node_id` to the nearest STRUCTURAL
    ancestor (a section heading). Returns None if the chain ends, cycles, or
    runs past `_MAX_ANCESTOR_WALK` without finding one — silence, not a guess
    (CLAUDE.md invariant 9): a sub-section citation with no resolvable
    section produces no edge rather than an edge scoped to the wrong one.
    """
    seen: set[str] = set()
    cur: str | None = node_id
    for _ in range(_MAX_ANCESTOR_WALK):
        cur = parent_of.get(cur) if cur is not None else None
        if cur is None or cur in seen:
            return None
        seen.add(cur)
        if cur in structural_ids:
            return cur
    return None


def _build_cursor(nodes: Sequence[Node], structural_edges: Sequence[Edge] = ()) -> _SectionCursor:
    section_registry: dict[str, str] = {}
    structural_ids: set[str] = set()
    for node in nodes:
        if node.kind is NodeKind.STRUCTURAL:
            structural_ids.add(node.node_id)
            key = _heading_key(node.raw)
            if key:
                section_registry[key] = node.node_id
    frozen_structural_ids = frozenset(structural_ids)

    parent_of: dict[str, str | None] = {
        e.src: e.dst for e in structural_edges if e.type is EdgeType.PART_OF
    }

    # Sub-section registry: (enclosing section, enumerator) -> node_id.
    # First writer wins on a collision — a repeated enumerator within one
    # section is a parser artifact (a mis-classified amendment-bracketed
    # heading collapsing two real sections into one ancestor is the shape
    # seen on the real corpus), never a correction, so later duplicates are
    # dropped with a warning rather than overwriting the first.
    subsection_registry: dict[tuple[str, str], str] = {}
    subsection_warnings: list[str] = []
    for node in nodes:
        if node.kind in (NodeKind.STRUCTURAL, NodeKind.FOOTNOTE):
            continue
        m = _SUBSECTION_ENUM_RE.match(node.raw)
        if not m:
            continue
        section = _enclosing_section(node.node_id, parent_of, frozen_structural_ids)
        if section is None:
            continue
        subsection_key = (section, m.group(1).upper())
        if subsection_key in subsection_registry:
            subsection_warnings.append(
                f"{node.node_id}: duplicate enumerator {m.group(1)!r} in section "
                f"{section!r} (first writer kept, this one skipped)"
            )
            continue
        subsection_registry[subsection_key] = node.node_id
    # FOOTNOTE nodes (editorial/amendment apparatus) are excluded from the
    # sibling chain entirely, not just skipped as a match source below: a
    # footnote sitting between a proviso and the provision it modifies would
    # otherwise become that proviso's "preceding sibling" and turn into a
    # closure-edge target, which is exactly the bug this guards against.
    children: dict[str | None, list[str]] = {}
    for node in nodes:
        if node.kind is NodeKind.FOOTNOTE:
            continue
        children.setdefault(parent_of.get(node.node_id), []).append(node.node_id)

    prev_sibling: dict[str, str | None] = {}
    next_sibling: dict[str, str | None] = {}
    for siblings in children.values():
        for i, node_id in enumerate(siblings):
            prev_sibling[node_id] = siblings[i - 1] if i > 0 else None
            next_sibling[node_id] = siblings[i + 1] if i + 1 < len(siblings) else None

    return _SectionCursor(
        section_registry, prev_sibling, next_sibling, parent_of,
        subsection_registry, frozen_structural_ids, subsection_warnings,
    )


def _orient(marker_node: str, target_node: str, edge_type: EdgeType) -> tuple[str, str]:
    if _orientation(edge_type) == "marker_is_dst":
        return target_node, marker_node
    return marker_node, target_node


def extract_structural_edges(
    nodes: Sequence[Node], pack: DomainPack, structural_edges: Sequence[Edge] = (),
) -> list[Edge]:
    """Free edges from `pack.structural_units` (proviso/explanation/illustration/...).

    `structural_edges` should be the `PART_OF` edges `dge.parsing` emitted for
    these same nodes — see `_SectionCursor`.
    """
    cursor = _build_cursor(nodes, structural_edges)
    edges: list[Edge] = []
    for node in nodes:
        if node.kind is NodeKind.STRUCTURAL or node.kind is NodeKind.FOOTNOTE:
            continue
        for unit in pack.structural_units:
            if not unit.regex.match(node.raw):
                continue
            target = cursor.prev_sibling.get(node.node_id) or cursor.parent_of.get(node.node_id)
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


_LIST_SPLIT_RE = re.compile(r"\s*(?:,|and|&|or)\s*", re.IGNORECASE)

# Extracts each enumerator from a `LEGAL_SUBSECTION_REF` group-1 match, e.g.
# "(1), (2) or (3) " -> "1", "2", "3". A separate extractor from
# `_LIST_SPLIT_RE` because the list items are parenthesised here rather than
# bare, comma/and/or-joined numbers — the two citation forms are not the same
# shape and conflating them silently drops every sub-section citation with
# more than one member.
_SUBSECTION_LIST_ENUM_RE = re.compile(r"\(\s*(\d{1,3}[A-Za-z]?)\s*\)")

# Text between two consecutive enumerators that means "everything from the
# first to the second inclusive", not "these two items". Checked against the
# gap between matches, same discipline as the clause-break scan above.
_RANGE_JOIN_RE = re.compile(r"\bto\b", re.IGNORECASE)


def _expand_subsection_enumerators(group1: str) -> list[str]:
    """Every enumerator named in a `LEGAL_SUBSECTION_REF` group-1 match,
    with "(N) to (M)" ranges expanded — "(2) to (4)" -> ["2", "3", "4"], not
    just the two endpoints. 6 occurrences in the 62-act corpus, all bare
    digits.

    A range where either endpoint carries a letter suffix ("(2A) to (4)") is
    NOT numerically expanded — "2A" has no defined successor — and only the
    two named endpoints are kept. Silence on the middle, not a guess
    (CLAUDE.md invariant 9): none of the 6 corpus occurrences hit this case,
    so it is deliberately conservative rather than tuned to a sample.
    """
    matches = list(_SUBSECTION_LIST_ENUM_RE.finditer(group1))
    out: list[str] = []
    for i, m in enumerate(matches):
        enum = m.group(1)
        if i > 0:
            gap = group1[matches[i - 1].end():m.start()]
            if _RANGE_JOIN_RE.search(gap):
                prev = matches[i - 1].group(1)
                if prev.isdigit() and enum.isdigit() and int(enum) > int(prev):
                    out.extend(str(n) for n in range(int(prev) + 1, int(enum)))
        out.append(enum)
    return out


def _scan_until_clause_break(
    pattern: Pattern[str], window: str, clause_break: Pattern[str] | None,
) -> list[Match[str]]:
    """Every match of `pattern` in `window`, stopping at the first one
    preceded (since the last accepted match) by a clause break.

    Shared by the section-citation and sub-section-citation walks in
    `_referenced_targets` so both patterns are scanned by the identical rule
    over the identical window — see that function's `after`-side branch for
    the measurement that motivated it. A citation naming several provisions
    ("sections 3, 4 and 5") is one match with internal commas, so the break
    is looked for in the GAPS between matches, never inside one.
    """
    accepted: list[Match[str]] = []
    cursor_pos = 0
    for ref in pattern.finditer(window):
        if clause_break is not None and clause_break.search(window, cursor_pos, ref.start()):
            break
        accepted.append(ref)
        cursor_pos = ref.end()
    return accepted


def _referenced_targets(
    node: Node, marker: MarkerPattern, match: Match[str],
    cursor: _SectionCursor, pack: DomainPack,
) -> list[str]:
    """Every provision in THIS document that `marker`'s citation names.

    Three properties, each of which was a defect measured on the real corpus
    rather than a hypothetical:

    1. **Search one side of the marker, not the whole node.** A node routinely
       cites several provisions in unrelated clauses; taking the first hit
       anywhere gave a target the marker does not govern in 8 of 54 sites.
       Which side is pack data (`MarkerPattern.ref_side`) because it is a fact
       about the drafting formula, not about graphs.
    2. **A citation to another instrument resolves to nothing.** Not to a
       best guess. `pack.foreign_ref_pattern` is checked against the text
       immediately after the number list, and a hit ends resolution for that
       citation — a `supersedes` edge is CLOSURE-class, so traversal follows it
       unbudgeted and a fabricated one is worse than silence (invariant 9).
    3. **A citation may name several provisions, and all of them are targets.**
       "section 28, section 30, section 31, section 34" is one relation
       expressed four times, and a single-target resolver kept a quarter of it.

    Returns targets in citation order, de-duplicated, never including `node`
    itself. An empty list means "this marker cites nothing reachable here",
    which is a legitimate answer and the caller reports it as a skip.
    """
    pattern = pack.section_ref_pattern
    if pattern is None:
        return []
    subpattern = pack.subsection_ref_pattern

    if marker.ref_side == "within":
        # The citation sits inside the matched phrase, so the match IS the
        # window and no side-search is needed. Used where the marker has to
        # span the citation to make a usable evidence span: "Nothing in" alone
        # proves nothing, "Nothing in Secs . 7, 8 and 9" proves the claim.
        window = match.group(0)
        refs = list(pattern.finditer(window))
        subrefs = list(subpattern.finditer(window)) if subpattern is not None else []
    elif marker.ref_side == "after":
        # Everything the marker governs lies in its own clause, so accept
        # citations only up to the first clause break — a citation past it
        # belongs to a different clause (see `pack.clause_break_pattern`).
        #
        # The break has to be looked for in the GAPS between citations, never
        # inside one: "sections 3, 4 and 5" is a single citation that happens
        # to contain commas, and truncating the window at the first comma would
        # keep s.3 and silently drop s.4 and s.5 — re-introducing the
        # under-resolution this function exists to fix.
        window = node.raw[match.end():]
        refs = _scan_until_clause_break(pattern, window, pack.clause_break_pattern)
        # Same rule, same window, the SUB-SECTION pattern — walked
        # independently rather than derived from `refs`'s cutoff, because the
        # two patterns can match at different positions in the same window.
        subrefs = (
            _scan_until_clause_break(subpattern, window, pack.clause_break_pattern)
            if subpattern is not None else []
        )
    else:
        # "in section 1, in sub-section (2), for the words ... shall be
        # substituted" — the amendment formula puts clause breaks BETWEEN the
        # citation and the operative phrase, so the clause rule above cannot
        # apply here. Nearness does the same job: the target is the closest
        # citation to the left, not the first one in the node.
        window = node.raw[: match.start()]
        refs = list(pattern.finditer(window))[-1:]
        subrefs = list(subpattern.finditer(window))[-1:] if subpattern is not None else []

    out: list[str] = []
    for ref in refs:
        if pack.foreign_ref_pattern is not None and pack.foreign_ref_pattern.match(
            window[ref.end():ref.end() + 160]
        ):
            continue
        for number in _LIST_SPLIT_RE.split(ref.group(1)):
            target = cursor.section_registry.get(number.strip().upper())
            if target is not None and target != node.node_id and target not in out:
                out.append(target)

    # Sub-section citations are a SEPARATE population from the section
    # citations above (see `LEGAL_SUBSECTION_REF`'s docstring): a sub-section
    # is not interchangeable with the section that contains it, so they are
    # resolved through `subsection_registry`, keyed on (enclosing section,
    # enumerator), never through `section_registry`.
    for ref in subrefs:
        if pack.foreign_ref_pattern is not None and pack.foreign_ref_pattern.match(
            window[ref.end():ref.end() + 160]
        ):
            continue
        section_group = ref.group(2)
        if section_group:
            # "sub-section (1) of section 12" — the scope is the NAMED
            # section, resolved through the same registry the section-ref
            # path above uses.
            scope = cursor.section_registry.get(section_group.strip().upper())
        else:
            # "sub-section (1)" alone — the scope is the CITING node's own
            # enclosing section, not any resolved target's.
            scope = _enclosing_section(node.node_id, cursor.parent_of, cursor.structural_ids)
        if scope is None:
            continue
        for enum in _expand_subsection_enumerators(ref.group(1)):
            target = cursor.subsection_registry.get((scope, enum.upper()))
            if target is not None and target != node.node_id and target not in out:
                out.append(target)
    return out


def _resolve_targets(
    node: Node, marker: MarkerPattern, match: Match[str],
    cursor: _SectionCursor, pack: DomainPack,
) -> list[str]:
    hint = marker.target_hint
    if hint == "preceding":
        target = cursor.prev_sibling.get(node.node_id) or cursor.parent_of.get(node.node_id)
        return [target] if target is not None and target != node.node_id else []
    if hint == "following":
        target = cursor.next_sibling.get(node.node_id)
        return [target] if target is not None and target != node.node_id else []
    if hint == "referenced":
        return _referenced_targets(node, marker, match, cursor, pack)
    return []


def extract_marker_edges(
    nodes: Sequence[Node], pack: DomainPack, structural_edges: Sequence[Edge] = (),
) -> tuple[list[Edge], list[str]]:
    """Pattern edges from `pack.markers`.

    STRONG hits get full confidence; MEDIUM hits are still written (lower
    confidence) rather than dropped, because there is no LLM verifier wired
    into the offline core path here and CLAUDE.md invariant 5 says: filter at
    traversal time, never delete at ingest time. A `referenced` hint that
    cannot be resolved to a node in THIS corpus (e.g. a citation to an
    external Act, or a section number outside the ingested set) produces no
    edge — fabricating a link to a node that doesn't exist would violate
    invariant 10 in spirit even though no model was involved.

    `structural_edges` should be the `PART_OF` edges `dge.parsing` emitted for
    these same nodes — see `_SectionCursor`.
    """
    cursor = _build_cursor(nodes, structural_edges)
    edges: list[Edge] = []
    warnings: list[str] = list(cursor.subsection_warnings)
    for node in nodes:
        if node.kind is NodeKind.STRUCTURAL or node.kind is NodeKind.FOOTNOTE:
            continue
        for marker in pack.markers:
            m = marker.regex.search(node.raw)
            if not m:
                continue
            targets = _resolve_targets(node, marker, m, cursor, pack)
            if not targets:
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
            confidence = 1.0 if marker.confidence.value == "strong" else 0.6
            # One citation can name several provisions ("sections 7, 8 and 9").
            # That is one relation asserted about each of them, so it is several
            # edges — they share an evidence span, and `dst` keeps the ids apart.
            for target in targets:
                src, dst = _orient(node.node_id, target, marker.edge_type)
                edges.append(Edge(
                    # Keyed on the RESOLVED TARGET, not on `dst`. For most
                    # edge types `dst` IS the target so this is identical, but
                    # `MARKER_ORIENTATION` inverts DEFINES — there `dst` is the
                    # citing node itself, so every target from one node
                    # produced the same id and a multi-target DEFINES citation
                    # ("for the purposes of sub-sections (1) and (2)") raised
                    # UNIQUE constraint failed on `edges.edge_id` at bundle
                    # write. Latent until a DEFINES marker first resolved
                    # `referenced` with more than one target.
                    edge_id=f"marker:{marker.name}:{node.node_id}:{target}",
                    src=src, dst=dst, type=marker.edge_type,
                    provenance=Provenance.PATTERN,
                    confidence=confidence,
                    evidence_span=evidence,
                ))
    return edges, warnings
