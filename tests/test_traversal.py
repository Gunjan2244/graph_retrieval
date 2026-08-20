"""Traversal tests. No database — everything runs against FixtureGraph.

The load-bearing test here is
`test_exception_is_only_reachable_via_the_reverse_index`: delete the
`incoming()` walk in `closure_neighbors` and it must fail. If it still passes,
the soundness guarantee is not actually being enforced by the code.
"""

from __future__ import annotations

import pytest

from dge.model import (
    DocStatus,
    Edge,
    EdgeType,
    InheritedContext,
    Node,
    NodeKind,
    Provenance,
)
from dge.traversal.assemble import assemble, splice_glosses
from dge.traversal.expand import (
    closure_fixpoint,
    closure_neighbors,
    context_frontier,
    default_key_extractor,
)
from dge.traversal.graph import FixtureGraph
from dge.traversal.policy import Budget
from dge.traversal.soundness import check_soundness


def node(node_id: str, seq: int, raw: str, *, doc: str = "d1",
         status: DocStatus = DocStatus.CURRENT, section: str | None = None) -> Node:
    return Node(
        node_id=node_id,
        doc_id=doc,
        kind=NodeKind.PROPOSITION,
        seq=seq,
        raw=raw,
        status=status,
        inherited=InheritedContext(section_path=section),
    )


def edge(edge_id: str, src: str, dst: str, t: EdgeType, *,
         confidence: float = 1.0, cross_doc: bool = False) -> Edge:
    return Edge(
        edge_id=edge_id,
        src=src,
        dst=dst,
        type=t,
        provenance=Provenance.STRUCTURAL,
        confidence=confidence,
        cross_doc=cross_doc,
    )


@pytest.fixture
def rule_and_exception() -> FixtureGraph:
    """The canonical shape: a rule, and a proviso that points AT it."""
    return FixtureGraph(
        nodes=[
            node("rule", 1, "Returns are accepted within 30 days of delivery."),
            node("exc", 2, "Clearance items are excluded from the return policy."),
            node("unrelated", 3, "The Board shall meet quarterly."),
        ],
        edges=[edge("e1", "exc", "rule", EdgeType.EXCEPTION_OF)],
    )


# --------------------------------------------------------------------------
# The guarantee
# --------------------------------------------------------------------------


def test_exception_is_only_reachable_via_the_reverse_index(rule_and_exception):
    g = rule_and_exception
    # Forward from the rule finds nothing: the edge runs exc -> rule.
    assert list(g.outgoing("rule")) == []
    # The exception is inbound, which is exactly why idx_edges_dst is
    # load-bearing rather than an optimisation (CLAUDE.md invariant 7).
    assert [e.edge_id for e in g.incoming("rule")] == ["e1"]

    result = closure_fixpoint(g, ["rule"])
    assert "exc" in result.reached, "reverse traversal failed to find the exception"
    assert result.arrivals["exc"].hops == 1
    assert result.arrivals["exc"].via is not None
    assert result.arrivals["exc"].via.type is EdgeType.EXCEPTION_OF


def test_closure_does_not_drag_in_unrelated_nodes(rule_and_exception):
    result = closure_fixpoint(rule_and_exception, ["rule"])
    assert "unrelated" not in result.reached


def test_soundness_flags_an_answer_that_omits_the_exception(rule_and_exception):
    # The model answered from the rule alone. That is precisely the failure
    # the product exists to prevent.
    report = check_soundness(
        rule_and_exception, cited_node_ids=["rule"], context_node_ids=["rule"]
    )
    assert report.ok is False
    assert report.missing_node_ids == ("exc",)
    assert report.violations[0].mandatory is True
    assert "UNSOUND" in report.summary()


def test_soundness_passes_once_the_exception_is_included(rule_and_exception):
    report = check_soundness(
        rule_and_exception, cited_node_ids=["rule"], context_node_ids=["rule", "exc"]
    )
    assert report.ok is True
    assert report.missing_node_ids == ()
    assert "sound" in report.summary()


def test_soundness_expand_and_rerun_loop_converges(rule_and_exception):
    """docs/04 §4.7: expand to include the missing neighbour and re-run."""
    context = ["rule"]
    report = check_soundness(
        rule_and_exception, cited_node_ids=["rule"], context_node_ids=context
    )
    assert not report.ok
    context = list(context) + list(report.missing_node_ids)
    report = check_soundness(
        rule_and_exception, cited_node_ids=["rule"], context_node_ids=context
    )
    assert report.ok


# --------------------------------------------------------------------------
# Closure semantics
# --------------------------------------------------------------------------


def test_closure_runs_to_a_fixed_point_not_a_depth_limit():
    """A 4-hop exception chain must survive. This is the case docs/04 §4.2
    says depth limits truncate while admitting hundreds of irrelevant
    neighbours."""
    g = FixtureGraph(
        nodes=[node(f"n{i}", i, f"provision {i}") for i in range(5)],
        edges=[
            edge("e1", "n1", "n0", EdgeType.EXCEPTION_OF),
            edge("e2", "n2", "n1", EdgeType.EXCEPTION_OF),
            edge("e3", "n3", "n2", EdgeType.EXCEPTION_OF),
            edge("e4", "n4", "n3", EdgeType.EXCEPTION_OF),
        ],
    )
    result = closure_fixpoint(g, ["n0"])
    assert set(result.reached) == {"n0", "n1", "n2", "n3", "n4"}
    assert result.arrivals["n4"].hops == 4


def test_closure_terminates_on_cycles():
    """'A qualifies B, B qualifies A' occurs in legal text (docs/04 §4.5)."""
    g = FixtureGraph(
        nodes=[node("a", 1, "A"), node("b", 2, "B")],
        edges=[
            edge("e1", "a", "b", EdgeType.EXCEPTION_OF),
            edge("e2", "b", "a", EdgeType.EXCEPTION_OF),
        ],
    )
    result = closure_fixpoint(g, ["a"])
    assert set(result.reached) == {"a", "b"}


def test_defines_is_traversed_forward_unlike_exception_of():
    """`defines` runs usage -> definition; `exception_of` runs exception ->
    rule. A single global direction would break one of them."""
    g = FixtureGraph(
        nodes=[node("usage", 1, "A specified asset may not be transferred."),
               node("defn", 2, '"specified asset" means land.')],
        edges=[edge("e1", "usage", "defn", EdgeType.DEFINES)],
    )
    result = closure_fixpoint(g, ["usage"])
    assert "defn" in result.reached
    # And not the other way around: starting at the definition should not walk
    # back to every usage of the term.
    assert "usage" not in closure_fixpoint(g, ["defn"]).reached


def test_defines_respects_its_hop_cap_while_exception_of_does_not():
    """DEFAULT_POLICY caps `defines` at 2 hops (docs/04 §4.4) but leaves
    `exception_of` unbounded. Both must be honoured by the same walk."""
    g = FixtureGraph(
        nodes=[node(f"d{i}", i, f"definition {i}") for i in range(5)],
        edges=[
            edge("e1", "d0", "d1", EdgeType.DEFINES),
            edge("e2", "d1", "d2", EdgeType.DEFINES),
            edge("e3", "d2", "d3", EdgeType.DEFINES),
            edge("e4", "d3", "d4", EdgeType.DEFINES),
        ],
    )
    result = closure_fixpoint(g, ["d0"])
    assert result.arrivals["d2"].hops == 2
    assert "d3" not in result.reached, "defines should stop at its 2-hop cap"
    assert any("caps at 2 hops" in s.reason for s in result.skipped)


def test_low_confidence_closure_edge_is_labeled_not_silently_dropped():
    """CLAUDE.md invariant 9: degrade to labeled low confidence, never to
    confident wrong."""
    g = FixtureGraph(
        nodes=[node("rule", 1, "rule"), node("exc", 2, "exception")],
        edges=[edge("e1", "exc", "rule", EdgeType.EXCEPTION_OF, confidence=0.2)],
    )
    result = closure_fixpoint(g, ["rule"], min_confidence=0.5)
    assert "exc" not in result.reached
    assert len(result.skipped) == 1
    assert "confidence" in result.skipped[0].reason
    assert result.skipped[0].neighbor_id == "exc"


def test_dangling_edge_is_recorded_rather_than_crashing():
    g = FixtureGraph(
        nodes=[node("rule", 1, "rule")],
        edges=[edge("e1", "ghost", "rule", EdgeType.EXCEPTION_OF)],
    )
    result = closure_fixpoint(g, ["rule"])
    assert result.reached == ("rule",)
    assert any(s.reason == "node not in graph" for s in result.skipped)


def test_closure_neighbors_ignores_context_edges():
    g = FixtureGraph(
        nodes=[node("a", 1, "a"), node("b", 2, "b")],
        edges=[edge("e1", "a", "b", EdgeType.ELABORATES)],
    )
    followed, _skipped = closure_neighbors(g, "a")
    assert followed == []


# --------------------------------------------------------------------------
# Context frontier
# --------------------------------------------------------------------------


def test_similar_to_is_never_traversed():
    """docs/04 §4.4: that is what seeding was for. Following it fans out
    combinatorially."""
    g = FixtureGraph(
        nodes=[node("a", 1, "a"), node("b", 2, "b")],
        edges=[edge("e1", "a", "b", EdgeType.SIMILAR_TO)],
    )
    result = context_frontier(g, seeds=["a"], query_relevance=lambda n: 1.0)
    assert "b" not in result.reached


def test_context_expansion_respects_the_token_budget():
    g = FixtureGraph(
        nodes=[node("seed", 0, "seed")] + [node(f"n{i}", i, "x" * 400) for i in range(1, 10)],
        edges=[edge(f"e{i}", "seed", f"n{i}", EdgeType.ELABORATES) for i in range(1, 10)],
    )
    result = context_frontier(
        g, seeds=["seed"], query_relevance=lambda n: 1.0,
        budget=Budget(max_tokens=250, saturation_window=99),
    )
    assert result.stopped_because == "budget"
    assert result.tokens_used <= 250
    assert len(result.reached) < 9


def test_context_expansion_stops_on_saturation():
    """Redundancy is the signal the neighbourhood is covered (docs/04 §4.5).
    Identical neighbours introduce no new keys, so expansion should stop well
    before the token budget is exhausted."""
    repeated = "the board shall meet quarterly in the registered office"
    g = FixtureGraph(
        nodes=[node("seed", 0, repeated)] + [node(f"n{i}", i, repeated) for i in range(1, 12)],
        edges=[edge(f"e{i}", "seed", f"n{i}", EdgeType.ELABORATES) for i in range(1, 12)],
    )
    result = context_frontier(
        g, seeds=["seed"], query_relevance=lambda n: 1.0,
        budget=Budget(max_tokens=100_000, saturation_window=3),
    )
    assert result.stopped_because == "saturated"
    assert len(result.reached) < 11


def test_relevant_far_node_beats_marginal_near_node():
    """The behaviour depth limits get exactly backwards (docs/04 §4.4)."""
    g = FixtureGraph(
        nodes=[node("seed", 0, "seed"), node("near", 1, "marginal"),
               node("mid", 2, "bridge"), node("far", 3, "highly relevant")],
        edges=[
            edge("e1", "seed", "near", EdgeType.ELABORATES),
            edge("e2", "seed", "mid", EdgeType.ELABORATES),
            edge("e3", "mid", "far", EdgeType.ELABORATES),
        ],
    )
    relevance = {"near": 0.01, "mid": 0.5, "far": 1.0}
    result = context_frontier(
        g, seeds=["seed"],
        query_relevance=lambda n: relevance.get(n.node_id, 0.0),
        budget=Budget(max_tokens=100_000, saturation_window=99),
    )
    assert result.reached.index("far") < result.reached.index("near")


def test_closure_nodes_consume_budget_but_are_not_re_emitted(rule_and_exception):
    result = context_frontier(
        rule_and_exception,
        seeds=["rule"],
        already_included=["exc"],
        query_relevance=lambda n: 1.0,
    )
    assert "exc" not in result.reached
    assert result.tokens_used > 0


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def test_assembly_is_in_document_order_not_retrieval_order():
    """Similarity-rank order reads to a model as a contradiction; document
    order reconstructs the logic (docs/04 §4.6)."""
    nodes = [
        node("exc", 2, "Clearance items are excluded.", section="§4.2 > Exclusions"),
        node("rule", 1, "Returns accepted within 30 days.", section="§4.2 > General"),
    ]
    out = assemble(nodes)
    assert out.node_ids == ("rule", "exc")
    assert out.text.index("Returns accepted") < out.text.index("Clearance items")
    assert "[§4.2 > General]" in out.text


def test_superseded_nodes_are_labeled_not_dropped():
    """Savings clauses preserve prior operation, so repealed != irrelevant."""
    nodes = [node("old", 1, "The old rule.", status=DocStatus.SUPERSEDED)]
    out = assemble(nodes)
    assert "[SUPERSEDED]" in out.text
    assert "The old rule." in out.text


def test_glosses_are_spliced_inline_once():
    text = "The Territory is defined. Territory again. Territory thrice."
    out = splice_glosses(text, {"Territory": "the countries in Schedule B"})
    assert out.count("[= the countries in Schedule B]") == 1
    assert "Territory [= the countries in Schedule B]" in out


def test_assembly_sorts_across_documents_deterministically():
    nodes = [
        node("b1", 1, "doc b first", doc="d2"),
        node("a2", 2, "doc a second", doc="d1"),
        node("a1", 1, "doc a first", doc="d1"),
    ]
    out = assemble(nodes)
    assert out.node_ids == ("a1", "a2", "b1")


def test_key_extractor_drops_stopwords():
    keys = default_key_extractor(node("n", 1, "The Board shall meet under this Act"))
    assert "board" in keys
    assert "shall" not in keys
    assert "the" not in keys
