from dge.domains.legal import get_pack
from dge.edges import extract_marker_edges, extract_structural_edges, validate_evidence_span
from dge.model import EdgeType
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
