from dge.domains.legal import get_pack
from dge.edges import extract_marker_edges, extract_structural_edges, validate_evidence_span
from dge.model import EdgeClass, EdgeType, NodeKind
from dge.parsing import PlainTextParser, finalize_doc_id

PACK = get_pack("legal")


def _parse(raw: bytes):
    result = PlainTextParser().parse(raw)
    nodes, _struct_edges = finalize_doc_id("d", list(result.nodes), list(result.structural_edges))
    return nodes


def test_evidence_span_must_be_verbatim_in_input_window():
    assert validate_evidence_span("shall not apply", "this shall not apply to anyone") is True
    assert validate_evidence_span("shall NOT apply", "this shall not apply to anyone") is False
    assert validate_evidence_span(None, "anything") is True


def test_proviso_points_at_the_rule_it_modifies_not_the_reverse():
    raw = (
        b"Section 1. Rule.\n\n"
        b"(1) No person shall do X.\n\n"
        b"Provided that nothing in this section shall apply to Y."
    )
    nodes = _parse(raw)
    rule, proviso = nodes[1], nodes[2]
    edges = extract_structural_edges(nodes, PACK)
    proviso_edges = [e for e in edges if e.type is EdgeType.EXCEPTION_OF]
    assert len(proviso_edges) == 1
    edge = proviso_edges[0]
    # "an exception points AT the rule it modifies" (dge.traversal.policy) —
    # so src is the proviso/exception, dst is the rule, and a reverse-index
    # lookup on dst=rule finds it.
    assert edge.src == proviso.node_id
    assert edge.dst == rule.node_id


def test_chained_provisos_form_a_reverse_traversable_chain():
    raw = (
        b"Section 1. Rule.\n\n"
        b"(1) No person shall do X.\n\n"
        b"Provided that nothing in this section shall apply to Y.\n\n"
        b"Provided further that Z is also excepted."
    )
    nodes = _parse(raw)
    rule, proviso1, proviso2 = nodes[1], nodes[2], nodes[3]
    edges = extract_structural_edges(nodes, PACK)
    by_dst: dict[str, list[str]] = {}
    for e in edges:
        by_dst.setdefault(e.dst, []).append(e.src)
    # Reverse lookup on the rule finds its direct exception.
    assert proviso1.node_id in by_dst[rule.node_id]
    # Reverse lookup on the first proviso finds the exception to the exception.
    assert proviso2.node_id in by_dst[proviso1.node_id]


def test_unresolvable_referenced_marker_produces_no_fabricated_edge():
    # "section 9" is not part of this corpus, so the non-obstante marker must
    # not invent an edge to a node that doesn't exist.
    raw = b"Section 1. Rule.\n\n(1) Notwithstanding anything contained in section 9, X applies."
    nodes = _parse(raw)
    edges, warnings = extract_marker_edges(nodes, PACK)
    assert edges == []
    assert any("non_obstante" in w for w in warnings)


def test_resolvable_referenced_marker_links_to_the_real_section():
    raw = (
        b"Section 9. Base rule.\n\n"
        b"(1) Everyone must comply.\n\n"
        b"Section 12. Override.\n\n"
        b"(1) Notwithstanding anything contained in section 9, this rule governs."
    )
    nodes = _parse(raw)
    section9 = nodes[0]
    override_clause = nodes[3]
    edges, _warnings = extract_marker_edges(nodes, PACK)
    supersedes = [e for e in edges if e.type is EdgeType.SUPERSEDES]
    assert len(supersedes) == 1
    assert supersedes[0].src == override_clause.node_id
    assert supersedes[0].dst == section9.node_id


def test_footnote_node_is_never_a_closure_edge_target():
    # Reproduces the exact Mines_Act,_1952 pathology (HANDOFF.md / task 1):
    # an amendment footnote line sits between a rule and the proviso that
    # modifies it. Before the fix, `_build_cursor`'s sibling chain included
    # the footnote, so `prev_sibling` resolved the proviso's "preceding"
    # target to the footnote instead of the rule it actually modifies.
    raw = (
        b"Section 1. Rule.\n\n"
        b"(1) No person shall do X unless he pays compensation.\n\n"
        b"1. Subs. by Act 42 of 1983, s. 11, for certain words (w.e.f. 31-5-1984).\n\n"
        b"Provided that the owner has not paid the amount within six weeks."
    )
    result = PlainTextParser().parse(raw)
    nodes, struct_edges = finalize_doc_id("d", list(result.nodes), list(result.structural_edges))
    footnote = next(n for n in nodes if n.kind is NodeKind.FOOTNOTE)
    rule = next(n for n in nodes if "No person shall do X" in n.raw)

    struct = extract_structural_edges(nodes, PACK, struct_edges)
    marker, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    closure_edges = [e for e in [*struct, *marker] if e.cls is EdgeClass.CLOSURE]

    assert closure_edges  # sanity: the fixture must actually exercise a closure edge
    assert all(e.src != footnote.node_id and e.dst != footnote.node_id for e in closure_edges)

    proviso_edge = next(e for e in closure_edges if e.type is EdgeType.EXCEPTION_OF)
    assert proviso_edge.dst == rule.node_id


def test_definition_marker_runs_usage_to_definition_not_definition_to_usage():
    raw = (
        b"Section 1. Rule.\n\n"
        b"(1) A specified asset may not be transferred.\n\n"
        b'Explanation.--For the purposes of this section, "specified asset" means land.'
    )
    nodes = _parse(raw)
    usage, definition = nodes[1], nodes[2]
    edges = extract_structural_edges(nodes, PACK)
    defines = [e for e in edges if e.type is EdgeType.DEFINES]
    assert len(defines) == 1
    # DEFAULT_POLICY[DEFINES] is FORWARD: start at a usage, walk to the
    # definition — the opposite orientation from EXCEPTION_OF/SUPERSEDES.
    assert defines[0].src == usage.node_id
    assert defines[0].dst == definition.node_id


# ---------------------------------------------------------------------------
# `referenced` target resolution.
#
# Each of these pins one mechanism measured on the 63-act corpus, where the old
# single-target, whole-node resolver got 14 of 54 sites wrong (26%) — all of
# them CLOSURE-class edges, which traversal follows unbudgeted and mandatorily.
# Break the corresponding rule in `dge.edges._referenced_targets` and the test
# fails; that is the point of writing them this way.
# ---------------------------------------------------------------------------


def test_a_citation_to_another_act_resolves_to_nothing():
    # "section 12 of the Central Goods and Services Tax Act" was resolving to
    # THIS Act's section 12 — a STRONG-confidence `supersedes` edge against a
    # provision the sentence never mentions. Silence is the correct degradation
    # (CLAUDE.md invariant 9): a fabricated closure edge is worse than a gap.
    raw = (
        b"Section 12. Local rule.\n\n"
        b"(1) Everyone must comply.\n\n"
        b"Section 20. Tax.\n\n"
        b"(1) Notwithstanding anything contained in section 12 of the Central Goods and"
        b" Services Tax Act, no tax shall be payable."
    )
    nodes = _parse(raw)
    edges, warnings = extract_marker_edges(nodes, PACK)
    assert [e for e in edges if e.type is EdgeType.SUPERSEDES] == []
    assert any("non_obstante" in w for w in warnings)


def test_a_citation_in_a_later_clause_is_not_borrowed_by_the_marker():
    # The non obstante clause's own scope is "this Act", which names no
    # section. Section 30 belongs to a different clause entirely, two commas
    # away. Taking the first citation anywhere in the node claimed it.
    raw = (
        b"Section 30. Confirmation.\n\n"
        b"(1) Every record must be confirmed.\n\n"
        b"Section 40. Quashing.\n\n"
        b"(1) Notwithstanding anything contained in this Act, the record of every case"
        b" so quashed shall be submitted for confirmation under the provisions of section 30."
    )
    nodes = _parse(raw)
    edges, _warnings = extract_marker_edges(nodes, PACK)
    assert [e for e in edges if e.type is EdgeType.SUPERSEDES] == []


def test_a_path_expression_before_the_citation_does_not_break_the_link():
    # The clause rule must not be a distance rule. "the first proviso to
    # section 57" puts 21 characters between marker and citation and is still
    # the same clause, so the edge must survive.
    raw = (
        b"Section 57. Surrender.\n\n"
        b"(1) A tenant may surrender.\n\n"
        b"Section 60. Exception.\n\n"
        b"(1) Notwithstanding anything contained in the first proviso to section 57, X applies."
    )
    nodes = _parse(raw)
    section57 = nodes[0]
    edges, _warnings = extract_marker_edges(nodes, PACK)
    supersedes = [e for e in edges if e.type is EdgeType.SUPERSEDES]
    assert len(supersedes) == 1
    assert supersedes[0].dst == section57.node_id


def test_a_citation_naming_several_provisions_writes_an_edge_to_each():
    # "Nothing in section 28, section 30, section 31, section 34 ... shall
    # apply to persons employed in a supervising capacity" (Mines Act 1952
    # s.37) is one relation asserted about four provisions. A single-target
    # resolver kept one of them and dropped the rest.
    raw = (
        b"Section 57. Surrender.\n\n(1) A tenant may surrender.\n\n"
        b"Section 58. Abandonment.\n\n(1) A tenant may abandon.\n\n"
        b"Section 70. Rule.\n\n"
        b"(1) A tenant loses the holding subject to the provisions of sections 57 and 58."
    )
    nodes = _parse(raw)
    targets = {nodes[0].node_id, nodes[2].node_id}
    edges, _warnings = extract_marker_edges(nodes, PACK)
    conditioned = [e for e in edges if e.type is EdgeType.CONDITIONED_ON]
    assert len(conditioned) == 2
    assert {e.dst for e in conditioned} == targets
    # One citation, so one evidence span, shared — the ids differ by `dst`.
    assert len({e.edge_id for e in conditioned}) == 2


def test_a_comma_inside_the_citation_does_not_truncate_the_list():
    # The clause break is looked for in the GAPS between citations, never
    # inside one. "sections 57, 58 and 59" is a single citation containing
    # commas; truncating at the first comma would silently keep only s.57.
    raw = (
        b"Section 57. A.\n\n(1) Text A.\n\n"
        b"Section 58. B.\n\n(1) Text B.\n\n"
        b"Section 59. C.\n\n(1) Text C.\n\n"
        b"Section 70. Rule.\n\n"
        b"(1) X is subject to the provisions of sections 57, 58 and 59."
    )
    nodes = _parse(raw)
    edges, _warnings = extract_marker_edges(nodes, PACK)
    conditioned = [e for e in edges if e.type is EdgeType.CONDITIONED_ON]
    assert len(conditioned) == 3


def test_abbreviated_section_forms_resolve():
    # India Code's text really does contain "Secs . 7, 8 and 9" — abbreviated,
    # plural, and with a space before the period. The old pattern required the
    # literal word "section" and saw none of it.
    raw = (
        b"Section 7. Hours.\n\n(1) Hours are limited.\n\n"
        b"Section 8. Holidays.\n\n(1) Holidays are granted.\n\n"
        b"Section 70. Rule.\n\n"
        b"(1) X applies subject to the provisions of Secs . 7 and 8."
    )
    nodes = _parse(raw)
    edges, _warnings = extract_marker_edges(nodes, PACK)
    assert len([e for e in edges if e.type is EdgeType.CONDITIONED_ON]) == 2


def test_amendment_surgery_reads_the_citation_that_precedes_it():
    # "in section 2, clauses (a) and (c) shall be omitted" names its target
    # BEFORE the operative phrase, and separates the two with a comma — so the
    # clause rule that governs non obstante cannot apply here. `ref_side` is
    # marker data for exactly this reason, and nearness picks the target.
    raw = (
        b"Section 2. Definitions.\n\n(1) In this Act, X means Y.\n\n"
        b"Section 9. Amendment.\n\n"
        b"(1) In section 2, clauses (a) and (c) shall be omitted."
    )
    nodes = _parse(raw)
    section2 = nodes[0]
    edges, _warnings = extract_marker_edges(nodes, PACK)
    amends = [e for e in edges if e.type is EdgeType.AMENDS]
    assert len(amends) == 1
    assert amends[0].dst == section2.node_id
