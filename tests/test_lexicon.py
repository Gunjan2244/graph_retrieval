from dge.domains.legal import get_pack
from dge.lexicon import extract_terms, link_mentions
from dge.model import EdgeType
from dge.parsing import PlainTextParser, finalize_doc_id

PACK = get_pack("legal")


def _parse(raw: bytes):
    result = PlainTextParser().parse(raw)
    nodes, _ = finalize_doc_id("d", list(result.nodes), list(result.structural_edges))
    return nodes


def test_means_is_exhaustive_and_includes_is_illustrative():
    raw = (
        b'Section 1. Definitions.\n\n'
        b'(1) "vehicle" means a car.\n\n'
        b'(2) "conveyance" includes a bicycle.'
    )
    nodes = _parse(raw)
    terms = extract_terms(nodes, PACK)
    kinds = {t.surface_form: t.definition_kind for t in terms}
    assert kinds["vehicle"] == "means"
    assert kinds["conveyance"] == "includes"


def test_definition_scope_is_the_enclosing_section_not_the_whole_document():
    raw = (
        b'Section 1. First.\n\n'
        b'(1) "widget" means a gadget.\n\n'
        b'Section 2. Second.\n\n'
        b'(1) Something unrelated.'
    )
    nodes = _parse(raw)
    section1, section2 = nodes[0], nodes[2]
    terms = extract_terms(nodes, PACK)
    assert len(terms) == 1
    assert terms[0].scope_node_id == section1.node_id
    assert terms[0].scope_node_id != section2.node_id


def test_mention_links_run_from_usage_to_definition():
    raw = (
        b'Section 1. Rule.\n\n'
        b'(1) A widget must be registered.\n\n'
        b'(2) "widget" means a small gadget.'
    )
    nodes = _parse(raw)
    usage_node = nodes[1]
    definition_node = nodes[2]
    terms = extract_terms(nodes, PACK)
    edges = link_mentions(nodes, terms)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.type is EdgeType.DEFINES
    assert edge.src == usage_node.node_id
    assert edge.dst == definition_node.node_id


def test_no_self_referential_mention_edge_on_the_definition_site():
    raw = b'Section 1. Rule.\n\n(1) "widget" means a small gadget used for widgeting.'
    nodes = _parse(raw)
    terms = extract_terms(nodes, PACK)
    edges = link_mentions(nodes, terms)
    assert edges == []
