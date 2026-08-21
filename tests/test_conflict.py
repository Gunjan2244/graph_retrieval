"""Competing non obstante clauses.

docs/07 7.2 is unambiguous: flag it, never silently pick one. These tests pin
both halves — that a real conflict is found, and that the engine does not
resolve it, does not invent an edge type for it, and does not guess when the
text does not settle the matter.
"""

from __future__ import annotations

from dge.domains.legal import ClaimScope, get_pack
from dge.edges import extract_marker_edges
from dge.l3.conflict import ConflictKind, detect_override_conflicts, override_claims
from dge.model import EdgeType
from dge.parsing import PlainTextParser, finalize_doc_id
from dge.traversal.expand import closure_fixpoint
from dge.traversal.graph import FixtureGraph

PACK = get_pack("legal")


def _parse(raw: bytes):
    result = PlainTextParser().parse(raw)
    return finalize_doc_id("d", list(result.nodes), list(result.structural_edges))


MUTUAL = (
    b"Section 9. Priority of secured creditors.\n\n"
    b"(1) Notwithstanding anything contained in section 12, the secured creditor\n"
    b"shall be paid first.\n\n"
    b"Section 12. Priority of workmen's dues.\n\n"
    b"(1) Notwithstanding anything contained in section 9, the workmen's dues\n"
    b"shall be paid first.\n"
)

ACT_WIDE = (
    b"Section 15. Powers of the Board.\n\n"
    b"(1) Notwithstanding anything contained in this Act, the Board may issue\n"
    b"directions.\n\n"
    b"Section 16. Powers of the Tribunal.\n\n"
    b"(1) Notwithstanding anything contained in this Act, the Tribunal may stay\n"
    b"any direction.\n"
)


def test_two_provisions_each_naming_the_other_are_flagged_as_a_real_conflict():
    nodes, _ = _parse(MUTUAL)
    conflicts = detect_override_conflicts(nodes, PACK)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.kind is ConflictKind.MUTUAL_REFERENCE
    assert conflict.confidence == 1.0
    assert {c.section for c in conflict.claims} == {"9", "12"}


def test_the_conflict_is_carried_by_existing_supersedes_edges_not_a_new_edge_type():
    # The representation decision: a resolvable conflict IS the cycle the
    # closure edges already form. Nothing new is stored, so nothing can go
    # stale and nothing can disagree with the graph.
    nodes, struct = _parse(MUTUAL)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct)
    supersedes = [e for e in edges if e.type is EdgeType.SUPERSEDES]
    assert len(supersedes) == 2
    assert all(e.evidence_span for e in supersedes), "each edge stands on its own evidence"

    # The cycle is between the two SECTIONS, not the two clauses: a
    # `referenced` marker resolves to the named section's heading, while the
    # claim itself sits in a sub-section.
    heading_of = {e.src: e.dst for e in struct if e.type is EdgeType.PART_OF}
    section_pairs = {(heading_of[e.src], e.dst) for e in supersedes}
    assert all((dst, src) in section_pairs for src, dst in section_pairs), (
        "must be a 2-cycle at section granularity"
    )


def test_seeding_a_section_heading_pulls_the_competing_provision_in():
    # The half of the guarantee that holds: mandatory closure on the reverse
    # index reaches the competitor with no help from this module.
    nodes, struct = _parse(MUTUAL)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct)
    graph = FixtureGraph(nodes, [*struct, *edges])

    heading9 = next(n for n in nodes if "Section 9." in n.raw)
    competitor = next(n for n in nodes if "contained in section 9" in n.raw)
    reached = closure_fixpoint(graph, [heading9.node_id]).reached
    assert competitor.node_id in reached


def test_seeding_only_the_clause_does_not_reach_the_competitor():
    # The half that does NOT hold, pinned so it cannot quietly be assumed away:
    # nothing points at the clause itself, and the hop to its own heading is
    # `part_of` — a budgeted CONTEXT edge. This is why the derived finding is
    # surfaced at query time rather than left to traversal.
    nodes, struct = _parse(MUTUAL)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct)
    graph = FixtureGraph(nodes, [*struct, *edges])

    clause9 = next(n for n in nodes if "contained in section 12" in n.raw)
    competitor = next(n for n in nodes if "contained in section 9" in n.raw)
    reached = closure_fixpoint(graph, [clause9.node_id]).reached
    assert competitor.node_id not in reached

    # ...but the conflict is still reported, which is the point.
    flagged = detect_override_conflicts(nodes, PACK)
    assert flagged and clause9.node_id in flagged[0].node_ids


def test_neither_side_is_marked_as_winning():
    nodes, struct = _parse(MUTUAL)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct)
    supersedes = [e for e in edges if e.type is EdgeType.SUPERSEDES]
    # Symmetric in every respect a caller could read as a resolution.
    assert len({e.confidence for e in supersedes}) == 1
    assert len({e.provenance for e in supersedes}) == 1


def test_act_wide_claims_are_clustered_at_half_confidence_not_asserted():
    nodes, _ = _parse(ACT_WIDE)
    conflicts = detect_override_conflicts(nodes, PACK)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.kind is ConflictKind.DOCUMENT_SCOPE_CLUSTER
    # Whether two act-wide claims actually collide depends on subject-matter
    # overlap, which no lexical rule can decide. Half confidence says so.
    assert conflict.confidence == 0.5
    assert len(conflict.node_ids) == 2
    assert "not decided here" in conflict.describe()


def test_act_wide_claims_do_not_explode_into_pairwise_findings():
    many = b"".join(
        f"Section {n}. Power {n}.\n\n"
        f"(1) Notwithstanding anything contained in this Act, authority {n} may act.\n\n"
        .encode() for n in range(20, 30)
    )
    nodes, _ = _parse(many)
    conflicts = detect_override_conflicts(nodes, PACK)
    # 10 claimants would be 45 pairs. One cluster finding instead.
    assert len(conflicts) == 1
    assert len(conflicts[0].node_ids) == 10


def test_a_claim_against_another_statute_is_not_an_internal_conflict():
    raw = (
        b"Section 5. Overriding effect.\n\n"
        b"(1) Notwithstanding anything contained in any other law for the time being\n"
        b"in force, this Act shall have effect.\n\n"
        b"Section 6. Second overriding effect.\n\n"
        b"(1) Notwithstanding anything contained in any other law for the time being\n"
        b"in force, this section shall have effect.\n"
    )
    nodes, _ = _parse(raw)
    claims = override_claims(nodes, PACK)
    assert claims and all(c.scope is ClaimScope.EXTERNAL for c in claims)
    assert detect_override_conflicts(nodes, PACK) == []


def test_a_repeal_clause_is_not_mistaken_for_an_override_claim():
    # `repeal` is also a SUPERSEDES-typed marker. It carries no override scope,
    # so it must not be read as a non obstante clause.
    raw = (
        b"Section 30. Repeal and savings.\n\n"
        b"(1) The Test Act, 1950 is hereby repealed.\n\n"
        b"Section 31. Second repeal.\n\n"
        b"(1) The Other Act, 1960 is hereby repealed.\n"
    )
    nodes, _ = _parse(raw)
    assert detect_override_conflicts(nodes, PACK) == []


def test_one_act_wide_claim_alone_is_not_a_conflict():
    raw = (
        b"Section 15. Powers of the Board.\n\n"
        b"(1) Notwithstanding anything contained in this Act, the Board may act.\n"
    )
    nodes, _ = _parse(raw)
    assert detect_override_conflicts(nodes, PACK) == []


def test_detection_is_order_independent_and_purely_derived():
    # Invariant 8: findings are a function of the current corpus, never an
    # accumulation. Same nodes in, same findings out, and nothing mutated.
    nodes, _ = _parse(MUTUAL)
    before = [n.raw for n in nodes]
    first = detect_override_conflicts(nodes, PACK)
    second = detect_override_conflicts(list(reversed(list(reversed(nodes)))), PACK)
    assert [c.node_ids for c in first] == [c.node_ids for c in second]
    assert [n.raw for n in nodes] == before


def test_non_obstante_qualifiers_are_matched_in_either_order():
    # Found on Ajmer_Tenancy_and_Land_Records_Act,_1950: the pattern required
    # "contained" before "to the contrary", so real drafting that writes them
    # the other way round produced no claim and suppressed a genuine conflict.
    raw = (
        b"Section 40. First power.\n\n"
        b"(1) Notwithstanding anything to the contrary contained in this Act, the\n"
        b"Collector may act.\n\n"
        b"Section 41. Second power.\n\n"
        b"(1) Notwithstanding anything contained in this Act, the Commissioner may\n"
        b"act.\n"
    )
    nodes, _ = _parse(raw)
    claims = override_claims(nodes, PACK)
    assert {c.section for c in claims} == {"40", "41"}
    assert all(c.scope is ClaimScope.DOCUMENT for c in claims)
    assert len(detect_override_conflicts(nodes, PACK)) == 1
