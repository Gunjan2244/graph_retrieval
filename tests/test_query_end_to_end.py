"""End-to-end: ingest a document, query the bundle, verify the guarantee.

This is the test that exercises the real product surface — the SQLite bundle
reader, the traversal, and the soundness check together — rather than a fixture
graph. It uses `samples/sample_act.txt`, which is synthetic and deliberately
marker-dense (that is why Phase 0 density FAILS on it); here that density is
exactly what makes it a good closure fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dge.bundle import open_bundle
from dge.model import EdgeType
from dge.pipeline import ingest_documents
from dge.query import run_query, verify_answer
from dge.retrieval.lexical import LexicalIndex

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample_act.txt"
RULE_TEXT = "ordinary course of business"
QUERY = "transfer made in the ordinary course of business permitted"


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("bundle") / "sample.sqlite"
    ingest_documents([SAMPLE], domain="legal", out_path=out)
    return out


def _find(graph, needle: str) -> str:
    for n in graph.all_nodes():
        if needle in n.raw:
            return n.node_id
    raise AssertionError(f"no node containing {needle!r}")


def test_bundle_reader_round_trips_nodes_and_edges(bundle):
    with open_bundle(bundle) as g:
        assert g.all_nodes(), "bundle has no nodes"
        assert g.manifest()["domain"] == "legal"
        assert g.documents()[0].review_state == "none"


def test_reverse_index_finds_the_proviso_from_the_rule(bundle):
    with open_bundle(bundle) as g:
        rule = _find(g, RULE_TEXT)
        inbound = [e for e in g.incoming(rule) if e.type is EdgeType.EXCEPTION_OF]
        assert inbound, "the proviso should point AT the rule via the reverse index"
        # And the same edge is NOT reachable by following outgoing edges.
        outbound = [e for e in g.outgoing(rule) if e.type is EdgeType.EXCEPTION_OF]
        assert not outbound


def test_closure_pulls_in_exceptions_that_seeding_alone_would_miss(bundle):
    """The whole product claim, end to end.

    Seed on the rule only. The provisos share almost no vocabulary with the
    query, so lexical seeding ranks them far below the cutoff — but closure
    traversal must bring them in regardless.
    """
    with open_bundle(bundle) as g:
        rule = _find(g, RULE_TEXT)
        proviso = _find(g, "operation of law")

        # Flat retrieval alone misses it at k=1.
        index = LexicalIndex(g.all_nodes())
        top1 = [s.node_id for s in index.search(QUERY, top_k=1)]
        assert top1 == [rule]
        assert proviso not in top1

        result = run_query(g, QUERY, top_k=1)
        assert result.seeds == (rule,)
        assert proviso in result.assembled.node_ids
        assert result.provenance_of(proviso) == "closure"
        assert result.soundness.ok


def test_exception_to_the_exception_also_arrives(bundle):
    """`Provided further that...` is an exception to the first proviso. A depth
    limit of 1 from the seed would truncate it; the fixed point does not."""
    with open_bundle(bundle) as g:
        second = _find(g, "exempt any class of transfers")
        result = run_query(g, QUERY, top_k=1)
        assert second in result.assembled.node_ids


def test_assembled_context_is_in_document_order(bundle):
    with open_bundle(bundle) as g:
        result = run_query(g, QUERY, top_k=1)
        text = result.assembled.text
        rule_at = text.index("ordinary course of business")
        proviso_at = text.index("operation of law")
        further_at = text.index("exempt any class of transfers")
        assert rule_at < proviso_at < further_at, "must read rule, then its provisos"


def test_flat_rag_answer_is_caught_as_unsound(bundle):
    """A model that answered from the rule alone — the confidently-wrong case
    the product exists to prevent."""
    with open_bundle(bundle) as g:
        rule = _find(g, RULE_TEXT)
        report = verify_answer(g, cited_node_ids=[rule], context_node_ids=[rule])
        assert report.ok is False
        assert report.missing_node_ids
        assert any(v.mandatory for v in report.violations)


def test_ingest_is_idempotent(tmp_path):
    """Definition of done #1: re-running produces identical output for
    identical input. doc_id is a content hash and every derived id follows
    from it, so this must hold byte-for-byte at the graph level."""
    a = tmp_path / "a.sqlite"
    b = tmp_path / "b.sqlite"
    ingest_documents([SAMPLE], domain="legal", out_path=a)
    ingest_documents([SAMPLE], domain="legal", out_path=b)

    with open_bundle(a) as ga, open_bundle(b) as gb:
        assert [n.node_id for n in ga.all_nodes()] == [n.node_id for n in gb.all_nodes()]
        assert [n.raw for n in ga.all_nodes()] == [n.raw for n in gb.all_nodes()]
        edges_a = sorted((e.edge_id, e.src, e.dst, e.type.value)
                         for n in ga.all_nodes() for e in ga.outgoing(n.node_id))
        edges_b = sorted((e.edge_id, e.src, e.dst, e.type.value)
                         for n in gb.all_nodes() for e in gb.outgoing(n.node_id))
        assert edges_a == edges_b
