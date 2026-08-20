from dge.model import NodeKind
from dge.parsing import PlainTextParser, finalize_doc_id


def test_byte_offsets_round_trip_to_original_bytes():
    raw = b"Section 1. Title.\n\n(1) First clause.\n\n(2) Second clause."
    result = PlainTextParser().parse(raw)
    assert result.confidence == 1.0
    for node in result.nodes:
        assert raw[node.byte_start:node.byte_end].decode("utf-8") == node.raw


def test_byte_offsets_survive_multibyte_utf8():
    # "—" (em dash, 3 bytes in utf-8) before the offset must not desync
    # char-based and byte-based positions.
    raw = "Explanation—the term “foo” means bar.\n\n(1) Next clause.".encode()
    result = PlainTextParser().parse(raw)
    for node in result.nodes:
        assert raw[node.byte_start:node.byte_end].decode("utf-8") == node.raw


def test_headings_become_structural_nodes_and_carry_section_path():
    raw = b"Section 1. Title.\n\nFirst paragraph text here."
    result = PlainTextParser().parse(raw)
    heading, paragraph = result.nodes
    assert heading.kind is NodeKind.STRUCTURAL
    assert paragraph.kind is NodeKind.PROPOSITION
    assert paragraph.inherited.section_path == "Section 1. Title."


def test_empty_input_gets_zero_confidence():
    result = PlainTextParser().parse(b"")
    assert result.confidence == 0.0
    assert result.nodes == ()


def test_decode_errors_lower_confidence_below_default_gate():
    # An invalid utf-8 byte sequence forces replacement characters.
    raw = b"Section 1. Title.\n\nBroken \xff\xfe text."
    result = PlainTextParser().parse(raw)
    assert result.confidence < 1.0
    assert any("replacement" in w for w in result.warnings)


def test_finalize_doc_id_rewrites_node_and_edge_ids_consistently():
    raw = b"Section 1. Title.\n\n(1) First clause."
    result = PlainTextParser().parse(raw)
    nodes, edges = finalize_doc_id("docABC", list(result.nodes), list(result.structural_edges))
    assert all(n.doc_id == "docABC" for n in nodes)
    assert all(n.node_id.startswith("docABC:") for n in nodes)
    node_ids = {n.node_id for n in nodes}
    for e in edges:
        assert e.src in node_ids
        assert e.dst in node_ids
