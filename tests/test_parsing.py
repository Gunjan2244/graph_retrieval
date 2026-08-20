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


# --- PARSER_PLAN.md regressions: dialect-B hard-wrapped bare-act text -------


def test_keyword_heading_does_not_swallow_the_next_paragraph():
    # A heading whose title is already complete on its own line must stop at
    # the next blank line, same as a plain block separator — not keep
    # absorbing on the theory that it's still "enumerated".
    raw = b"Section 1. Title.\n\nFirst paragraph text here.\n\nSecond paragraph text here."
    result = PlainTextParser().parse(raw)
    assert [n.raw for n in result.nodes] == [
        "Section 1. Title.",
        "First paragraph text here.",
        "Second paragraph text here.",
    ]


def test_dialect_b_heading_and_title_wrapped_across_blank_lines_merge_into_one_node():
    # Real India Code bare-act text (e.g. Actuaries Act, 2006): a bare
    # enumerator, then its title hard-wrapped onto a later physical line,
    # separated by a blank line that is a wrapping artifact, not a separator.
    raw = b"1.\n\nShort title,\n\nextent and commencement -\n\n(1)\n\nThis Act may be called it."
    result = PlainTextParser().parse(raw)
    heading, subsec = result.nodes
    assert heading.kind is NodeKind.STRUCTURAL
    assert heading.raw == "1.\n\nShort title,\n\nextent and commencement -"
    assert subsec.kind is NodeKind.PROPOSITION
    assert subsec.raw == "(1)\n\nThis Act may be called it."
    assert subsec.inherited.section_path == "1. Short title, extent and commencement -"


def test_bare_digit_heading_followed_by_a_sentence_is_a_subsection_not_a_new_section():
    # PARSER_PLAN.md Decision 4: "2. It extends to the whole of India." is
    # section 1's second sub-clause in the source Act, not a new section 2 —
    # the giveaway is that its title reads as a full sentence, not a phrase.
    raw = (
        b"1. Short title, extent and commencement\n\n"
        b"2. It extends to the whole of India.\n\n"
        b"3. It shall come into force at once."
    )
    result = PlainTextParser().parse(raw)
    headings = [n for n in result.nodes if n.kind is NodeKind.STRUCTURAL]
    assert len(headings) == 1
    assert headings[0].raw == "1. Short title, extent and commencement"


def test_amendment_bracket_before_a_heading_number_is_still_recognized():
    # India Code renders amended sections as `N[...]`, N a footnote number,
    # placed directly before the enumerator it wraps.
    raw = b"5[31. Hours of work below ground.-\n\nNo person shall work more than six hours."
    result = PlainTextParser().parse(raw)
    heading = result.nodes[0]
    assert heading.kind is NodeKind.STRUCTURAL
    assert "31. Hours of work below ground" in heading.raw


def test_footnote_shaped_line_is_not_read_as_a_new_section():
    raw = (
        b"5. Real section heading.-\n\n"
        b"Some operative text.\n\n"
        b"1. Ins. by Act 42 of 1983, s. 17 (w.e.f. 31-5-1984)."
    )
    result = PlainTextParser().parse(raw)
    headings = [n for n in result.nodes if n.kind is NodeKind.STRUCTURAL]
    assert len(headings) == 1
    assert "Real section heading" in headings[0].raw


def test_footnote_shaped_line_is_its_own_node_kind_not_a_proposition():
    # Footnotes are editorial/amendment apparatus, not provisions (task 1):
    # they must be a distinct NodeKind so downstream edge extraction can
    # exclude them from ever becoming a closure-edge target, rather than
    # being indistinguishable from real operative text as NodeKind.PROPOSITION.
    raw = (
        b"5. Real section heading.-\n\n"
        b"Some operative text.\n\n"
        b"1. Ins. by Act 42 of 1983, s. 17 (w.e.f. 31-5-1984)."
    )
    result = PlainTextParser().parse(raw)
    footnote = result.nodes[-1]
    assert footnote.kind is NodeKind.FOOTNOTE
    assert footnote.is_assertive is False


def test_three_level_nesting_produces_a_three_level_section_path():
    raw = (
        b"CHAPTER II\n\n"
        b"Section 3. Hijacking.-\n\n"
        b"(2) A person shall also be deemed to have committed the offence, if-\n\n"
        b"(a) makes a threat to commit such offence."
    )
    result = PlainTextParser().parse(raw)
    clause = result.nodes[-1]
    assert clause.raw == "(a) makes a threat to commit such offence."
    assert clause.inherited.section_path == "CHAPTER II > Section 3. Hijacking.- > (2)"


def test_document_with_almost_no_structural_markers_is_gated_below_threshold():
    # CLAUDE.md invariant 9: when heading/enumerator detection has plainly
    # failed on a document (here: no headings or enumerators at all, just
    # blank-separated prose), degrade to labeled low confidence and halt
    # rather than proceed on a substrate we have no signal we parsed right.
    body = "\n\n".join(f"Paragraph number {n} of running prose with no markers." for n in range(40))
    result = PlainTextParser().parse(body.encode())
    assert result.confidence < 0.5
    assert any("structural marker" in w for w in result.warnings)
