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


# ---------------------------------------------------------------------------
# Sub-section citation resolution ("sub-section (1)", "sub-sections (1) and
# (2)", "sub-section (1) of section 9").
#
# `LEGAL_SECTION_REF` cannot see these at all — the enumerator sits inside
# parentheses, not as a bare digit after the word "section" — and even if it
# matched, `section_registry` has no entry for a sub-section, only for
# headings. Measured on the 62-act corpus: 1111 bare citations, 310 in
# "sub-section (N) of section M" form, 47 as an explicit list, 2 of the
# hyphen-space variant India Code's text actually contains — roughly 40% of
# intra-document citations in the corpus, silently dropped before this.
#
# `subsection_registry` is keyed on (enclosing section, enumerator), never the
# enumerator alone: "(1)" recurs in nearly every section, so an unscoped
# registry would map every sub-section (1) in a document onto whichever one
# was written first — the same shape of fabrication `LEGAL_FOREIGN_REF` exists
# to prevent for section citations, reproduced at corpus scale instead of one
# marker type. `test_the_same_enumerator_in_another_section_is_never_the_target`
# is the one pinning that property; it is the most important test in this
# block.
# ---------------------------------------------------------------------------


def _parse_with_edges(raw: bytes):
    result = PlainTextParser().parse(raw)
    return finalize_doc_id("d", list(result.nodes), list(result.structural_edges))


def test_a_subsection_citation_resolves_within_its_own_section():
    raw = (
        b"Section 12. Rule.\n\n(1) Everyone must comply.\n\n"
        b"(2) Subject to the provisions of sub-section (1), a tenant may sublet."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    rule1 = next(n for n in nodes if "Everyone must comply" in n.raw)
    citing = next(n for n in nodes if "Subject to the provisions" in n.raw)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    conditioned = [e for e in edges if e.type is EdgeType.CONDITIONED_ON]
    assert len(conditioned) == 1
    assert conditioned[0].src == citing.node_id
    assert conditioned[0].dst == rule1.node_id


def test_the_same_enumerator_in_another_section_is_never_the_target():
    # Two sections, each with its own "(1)". A registry keyed on the bare
    # enumerator would resolve the citation to whichever "(1)" it saw first —
    # section 9's, not section 12's, and the citing node is in section 12.
    raw = (
        b"Section 9. Other.\n\n(1) Something else entirely.\n\n"
        b"Section 12. Rule.\n\n(1) Everyone must comply.\n\n"
        b"(2) Subject to the provisions of sub-section (1), a tenant may sublet."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    other_rule1 = next(n for n in nodes if "Something else entirely" in n.raw)
    local_rule1 = next(n for n in nodes if "Everyone must comply" in n.raw)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    conditioned = [e for e in edges if e.type is EdgeType.CONDITIONED_ON]
    assert len(conditioned) == 1
    assert conditioned[0].dst == local_rule1.node_id
    assert conditioned[0].dst != other_rule1.node_id


def test_a_subsection_citation_qualified_by_another_section_resolves_there():
    # "sub-section (1) of section 9" scopes to section 9, not the citing
    # node's own section. Note: the bare substring "section 9" inside this
    # phrase is ALSO matched by `LEGAL_SECTION_REF` independently (pre-existing
    # behaviour, unrelated to this change) and produces its own edge to
    # section 9's heading — coarser and redundant, but not fabricated: section
    # 9 genuinely is named. Both edges are asserted here rather than silently
    # narrowed to one; see decisions.md for why this is left as a documented
    # finding rather than patched without corpus evidence.
    raw = (
        b"Section 9. Base.\n\n(1) Base rule text.\n\n"
        b"Section 12. Override.\n\n"
        b"(1) Notwithstanding anything contained in sub-section (1) of section 9, "
        b"this rule governs."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    section9 = nodes[0]
    subsection1 = next(n for n in nodes if "Base rule text" in n.raw)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    supersedes = [e for e in edges if e.type is EdgeType.SUPERSEDES]
    dsts = {e.dst for e in supersedes}
    assert subsection1.node_id in dsts
    assert section9.node_id in dsts


def test_a_subsection_of_a_foreign_act_resolves_to_nothing():
    raw = (
        b"Section 12. Rule.\n\n(1) Everyone must comply.\n\n"
        b"(2) Subject to the provisions of sub-section (1) of section 12 of the "
        b"Companies Act, no obligation arises."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    edges, warnings = extract_marker_edges(nodes, PACK, struct_edges)
    assert [e for e in edges if e.type is EdgeType.CONDITIONED_ON] == []
    assert any("subject_to" in w for w in warnings)


def test_a_subsection_citation_naming_several_writes_an_edge_to_each():
    raw = (
        b"Section 12. Rule.\n\n(1) First rule.\n\n(2) Second rule.\n\n"
        b"(3) Subject to the provisions of sub-sections (1) and (2), a tenant "
        b"may sublet."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    targets = {
        next(n for n in nodes if "First rule" in n.raw).node_id,
        next(n for n in nodes if "Second rule" in n.raw).node_id,
    }
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    conditioned = [e for e in edges if e.type is EdgeType.CONDITIONED_ON]
    assert len(conditioned) == 2
    assert {e.dst for e in conditioned} == targets


def test_a_subsection_citation_in_a_later_clause_is_not_borrowed():
    # The non obstante clause's own scope is "this Act", which names no
    # section. "sub-section (1) of section 30" sits in a later clause, past a
    # comma — the same clause-break rule that governs section citations must
    # also stop the sub-section scan from borrowing it.
    raw = (
        b"Section 30. Confirmation.\n\n(1) Every record must be confirmed.\n\n"
        b"Section 40. Quashing.\n\n"
        b"(1) Notwithstanding anything contained in this Act, the record of every "
        b"case so quashed shall be submitted for confirmation under sub-section "
        b"(1) of section 30."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    assert [e for e in edges if e.type is EdgeType.SUPERSEDES] == []


def test_a_node_with_no_structural_ancestor_resolves_to_nothing():
    # No "Section N." heading anywhere in the document, so the citing node has
    # no structural parent to walk to. Degrades to silence (CLAUDE.md
    # invariant 9), not to a guessed scope.
    raw = b"Subject to the provisions of sub-section (1), commencement shall follow."
    nodes, struct_edges = _parse_with_edges(raw)
    edges, warnings = extract_marker_edges(nodes, PACK, struct_edges)
    assert [e for e in edges if e.type is EdgeType.CONDITIONED_ON] == []
    assert any("subject_to" in w for w in warnings)


def test_the_hyphen_space_form_resolves():
    # India Code's text really does contain "sub- section (N)" — hyphen,
    # then a space, before "section". Same corpus discipline as the
    # "Secs . 7" abbreviated form pinned above.
    raw = (
        b"Section 12. Rule.\n\n(1) Everyone must comply.\n\n"
        b"(2) Subject to the provisions of sub- section (1), a tenant may sublet."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    rule1 = next(n for n in nodes if "Everyone must comply" in n.raw)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    conditioned = [e for e in edges if e.type is EdgeType.CONDITIONED_ON]
    assert len(conditioned) == 1
    assert conditioned[0].dst == rule1.node_id

def test_a_subsection_range_expands_the_middle_enumerators():
    # "sub-sections (2) to (4)" names three provisions, not two. A resolver
    # that only captured the range's endpoints would silently drop (3).
    raw = (
        b"Section 12. Rule.\n\n(1) First.\n\n(2) Second.\n\n(3) Third.\n\n(4) Fourth.\n\n"
        b"(5) Subject to the provisions of sub-sections (2) to (4), a tenant may sublet."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    targets = {
        next(n for n in nodes if n.raw.startswith("(2)")).node_id,
        next(n for n in nodes if n.raw.startswith("(3)")).node_id,
        next(n for n in nodes if n.raw.startswith("(4)")).node_id,
    }
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    conditioned = [e for e in edges if e.type is EdgeType.CONDITIONED_ON]
    assert len(conditioned) == 3
    assert {e.dst for e in conditioned} == targets


def test_a_lettered_subsection_range_does_not_guess_the_middle():
    # "(2A) to (4)" has no defined successor for "2A" — expand only the two
    # named endpoints, never a guessed middle.
    raw = (
        b"Section 12. Rule.\n\n(2A) Second-A.\n\n(3) Third.\n\n(4) Fourth.\n\n"
        b"(5) Subject to the provisions of sub-sections (2A) to (4), a tenant may sublet."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    two_a = next(n for n in nodes if n.raw.startswith("(2A)"))
    four = next(n for n in nodes if n.raw.startswith("(4)"))
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    conditioned = [e for e in edges if e.type is EdgeType.CONDITIONED_ON]
    assert {e.dst for e in conditioned} == {two_a.node_id, four.node_id}

# ---------------------------------------------------------------------------
# "For the purposes of X" — the CITED variant.
#
# Same drafting formula as `for_the_purposes_of`, opposite target: that marker
# scopes to the unit it SITS IN ("this section"), this one to the unit it
# NAMES. Measured over 62 acts: 23 cited sites against 119 self-referential
# ones; the old marker matched none of the 23, so they were invisible rather
# than mis-targeted.
# ---------------------------------------------------------------------------


def test_a_cited_for_the_purposes_of_scopes_to_the_named_provision():
    raw = (
        b"Section 4. Costs.\n\n"
        b"(1) The Board shall meet the expenditure specified in the Schedule.\n\n"
        b"(2) For the purposes of sub-section (1), the expenditure on the dam "
        b"shall mean the capital cost only."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    usage = next(n for n in nodes if n.raw.startswith("(1)"))
    definition = next(n for n in nodes if n.raw.startswith("(2)"))
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    defines = [e for e in edges if e.type is EdgeType.DEFINES]
    # DEFINES traverses FORWARD (usage -> meaning), so the cited provision is
    # `src` and the definition is `dst`: seeding on (1) reaches (2).
    assert any(e.src == usage.node_id and e.dst == definition.node_id for e in defines)


def test_a_referential_for_the_purposes_of_is_not_a_definition():
    # "any authority prescribed for the purposes of sub-section (1) may ..."
    # names a provision but does not define anything for it — the citation
    # runs on into a verb phrase instead of prefacing a rule.
    raw = (
        b"Section 4. Authorities.\n\n"
        b"(1) The prescribed authority shall keep a register.\n\n"
        b"(2) Any authority prescribed for the purposes of sub-section (1) may "
        b"require the production of documents."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    usage = next(n for n in nodes if n.raw.startswith("(1)"))
    referential = next(n for n in nodes if n.raw.startswith("(2)"))
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    assert not [
        e for e in edges
        if e.type is EdgeType.DEFINES
        and e.src == usage.node_id
        and e.dst == referential.node_id
    ]


def test_a_cited_for_the_purposes_of_naming_a_foreign_act_resolves_to_nothing():
    raw = (
        b"Section 196. Local.\n\n(1) Something local.\n\n"
        b"Section 20. Proceedings.\n\n"
        b"(1) Every proceeding shall be deemed judicial for the purposes of "
        b"section 196 of the Indian Penal Code, and the Tribunal shall be a court."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    local196 = nodes[0]
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    assert not [
        e for e in edges
        if e.type is EdgeType.DEFINES and local196.node_id in (e.src, e.dst)
    ]

def test_a_multi_target_defines_citation_produces_distinct_edge_ids():
    # Regression: `MARKER_ORIENTATION` inverts DEFINES, so `dst` is the citing
    # node, not the target. Keying `edge_id` on `dst` gave every target from
    # one node the same id and raised UNIQUE constraint failed at bundle write.
    raw = (
        b"Section 4. Duties.\n\n"
        b"(1) An occupier shall give notice.\n\n"
        b"(2) An occupier shall keep a register.\n\n"
        b"(3) For the purposes of sub-sections (1) and (2), \"notice\" means "
        b"written notice served by post."
    )
    nodes, struct_edges = _parse_with_edges(raw)
    edges, _warnings = extract_marker_edges(nodes, PACK, struct_edges)
    defines = [e for e in edges if e.type is EdgeType.DEFINES]
    multi = [e for e in defines if "for_the_purposes_of_referenced" in e.edge_id]
    assert len(multi) == 2
    assert len({e.edge_id for e in multi}) == 2
