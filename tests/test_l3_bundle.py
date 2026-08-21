"""L3 through the bundle: ingest -> extract -> read back.

Uses a fake extractor, so this pins the plumbing (what gets written, that
re-running converges, that review-pending documents are never paid for) and
says nothing about model quality.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from dge.bundle import open_bundle
from dge.interfaces import EdgeCandidate
from dge.l3.prompt import prompt_hash
from dge.model import Node, Provenance
from dge.pipeline import extract_bundle, ingest_documents, plan_extraction

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample_act.txt"


class ProvisoExtractor:
    """Proposes one edge per window: the first proviso -> the unit before it.

    Quotes its evidence verbatim from the node it is talking about, so the
    edges it proposes are meant to survive validation. Everything adversarial
    lives in `tests/test_l3_extract.py`.
    """

    model_id = "fake:proviso-v1"
    prompt_hash = prompt_hash()

    def extract(
        self, section_nodes: Sequence[Node], section_path: str, doc_summary: str
    ) -> Sequence[EdgeCandidate]:
        for i, node in enumerate(section_nodes):
            if i and node.raw.lstrip().startswith("Provided that"):
                span = node.raw.strip()[:60]
                return [EdgeCandidate(
                    src=node.node_id, dst=section_nodes[i - 1].node_id,
                    type="exception_of", evidence_span=span, confidence=0.85,
                )]
        return []


@pytest.fixture
def bundle(tmp_path) -> Path:
    out = tmp_path / "b.sqlite"
    ingest_documents([SAMPLE], domain="legal", out_path=out)
    return out


def test_extract_writes_model_edges_with_full_provenance(bundle):
    summary = extract_bundle(bundle, ProvisoExtractor())
    assert summary.edges_written > 0

    with open_bundle(bundle) as graph:
        model_edges = [
            e
            for n in graph.all_nodes()
            for e in graph.outgoing(n.node_id)
            if e.provenance is Provenance.MODEL
        ]
    assert model_edges
    for edge in model_edges:
        # CLAUDE.md invariant 5, checked on what actually landed in SQLite
        # rather than on what the extractor returned.
        assert edge.model_id == "fake:proviso-v1"
        assert edge.prompt_hash == prompt_hash()
        assert edge.evidence_span
        assert 0.0 <= edge.confidence <= 1.0


def test_stored_evidence_spans_are_verbatim_in_the_node_they_came_from(bundle):
    extract_bundle(bundle, ProvisoExtractor())
    with open_bundle(bundle) as graph:
        for node in graph.all_nodes():
            for edge in graph.outgoing(node.node_id):
                if edge.provenance is Provenance.MODEL and edge.evidence_span:
                    src = graph.get_node(edge.src)
                    dst = graph.get_node(edge.dst)
                    window = f"{src.raw}\n{dst.raw}"
                    assert edge.evidence_span in window


def test_re_running_extraction_converges_rather_than_accumulating(bundle):
    first = extract_bundle(bundle, ProvisoExtractor())

    with open_bundle(bundle) as graph:
        before = sorted(
            e.edge_id for n in graph.all_nodes() for e in graph.outgoing(n.node_id)
        )
    extract_bundle(bundle, ProvisoExtractor())
    with open_bundle(bundle) as graph:
        after = sorted(
            e.edge_id for n in graph.all_nodes() for e in graph.outgoing(n.node_id)
        )
    assert before == after, "deterministic edge_ids must make re-running idempotent"
    assert first.edges_written > 0


def test_dry_run_prices_the_corpus_without_calling_anything(bundle):
    gate, _conflicts = plan_extraction(bundle)
    assert gate.sections_total > 0
    assert 0.0 <= gate.char_fraction <= 1.0


def test_review_pending_documents_are_never_sent_to_the_model(tmp_path):
    corrupt = tmp_path / "corrupt.txt"
    corrupt.write_text("\n\n\n   \n\n")
    out = tmp_path / "b.sqlite"
    ingest_documents([corrupt], domain="legal", out_path=out)

    with open_bundle(out) as graph:
        if not [d for d in graph.documents() if d.review_state == "pending"]:
            pytest.skip("fixture did not trigger the review gate on this parser version")

    class Explode:
        model_id = "fake:never"
        prompt_hash = prompt_hash()

        def extract(self, section_nodes, section_path, doc_summary):  # type: ignore[no-untyped-def]
            raise AssertionError("invariant 9: a review-pending doc must not reach L3")

    summary = extract_bundle(out, Explode())
    assert summary.documents == 0
    assert summary.calls == 0


class DuplicateOfPatternExtractor:
    """Proposes exactly the relation an existing PATTERN edge already carries.

    Real models do this constantly: the lexical marker layer and the model
    both notice the same proviso, or the same term usage, and each writes it
    under its own edge_id (`mention:...` vs `model:defines:...`).
    """

    model_id = "fake:duplicate-v1"
    prompt_hash = prompt_hash()

    def __init__(self, targets: list[tuple[str, str, str]]) -> None:
        self._targets = targets

    def extract(
        self, section_nodes: Sequence[Node], section_path: str, doc_summary: str
    ) -> Sequence[EdgeCandidate]:
        ids = {n.node_id for n in section_nodes}
        by_id = {n.node_id: n for n in section_nodes}
        out = []
        for src, dst, etype in self._targets:
            if src in ids and dst in ids:
                out.append(EdgeCandidate(
                    src=src, dst=dst, type=etype,
                    evidence_span=by_id[src].raw.strip()[:60],
                    confidence=0.95,
                ))
        return out


def test_model_edge_duplicating_a_pattern_edge_is_not_written_twice(bundle):
    """A duplicate (src, dst, type) silently inflates the degree penalty in
    `traversal.policy.frontier_score` — the term that distinguishes hubs from
    genuinely well-connected nodes. `_dedupe_edges` guards this at ingest;
    this pins the same guarantee on the L3 write path, which reaches the
    bundle by a different route."""
    with open_bundle(bundle) as g:
        pattern = [
            (e.src, e.dst, e.type.value)
            for n in g.all_nodes()
            for e in g.outgoing(n.node_id)
            if e.provenance is Provenance.PATTERN
        ]
    assert pattern, "fixture must have pattern edges for this test to mean anything"

    extract_bundle(bundle, DuplicateOfPatternExtractor(pattern))

    with open_bundle(bundle) as g:
        seen: dict[tuple[str, str, str], int] = {}
        for n in g.all_nodes():
            for e in g.outgoing(n.node_id):
                k = (e.src, e.dst, e.type.value)
                seen[k] = seen.get(k, 0) + 1
        dupes = {k: c for k, c in seen.items() if c > 1}
        assert not dupes, f"duplicate (src,dst,type) edges written: {dupes}"

        # And degree must equal the number of distinct relations touching a node.
        for n in g.all_nodes():
            rels = {
                (e.src, e.dst, e.type.value)
                for e in list(g.outgoing(n.node_id)) + list(g.incoming(n.node_id))
            }
            assert g.degree(n.node_id) == len(rels), (
                f"{n.node_id}: degree {g.degree(n.node_id)} != {len(rels)} unique relations"
            )


class DefinesExtractor:
    """Proposes exactly the relation the lexicon's mention-linking already
    found, to force the duplicate case.

    A model edge and a pattern edge can describe the SAME (src, dst, type)
    under different edge_ids (`model:defines:...` vs `mention:...`). Both
    being written is the bug this pins.
    """

    model_id = "fake:defines-v1"
    prompt_hash = prompt_hash()

    def __init__(self, src: str, dst: str, span: str) -> None:
        self._src, self._dst, self._span = src, dst, span

    def extract(
        self, section_nodes: Sequence[Node], section_path: str, doc_summary: str
    ) -> Sequence[EdgeCandidate]:
        ids = {n.node_id for n in section_nodes}
        if self._src in ids and self._dst in ids:
            return [EdgeCandidate(
                src=self._src, dst=self._dst, type="defines",
                evidence_span=self._span, confidence=0.95,
            )]
        return []


def test_model_edge_does_not_duplicate_an_existing_pattern_relation(bundle):
    """A duplicate (src, dst, type) silently inflates the degree penalty in
    `dge.traversal.policy.frontier_score` — the term that exists to tell hubs
    apart from genuinely well-connected nodes. `_dedupe_edges` guards this at
    ingest; L3 writes bypassed it because model and reconciled edges arrive as
    separate lists.
    """
    with open_bundle(bundle) as graph:
        pattern = [
            e
            for n in graph.all_nodes()
            for e in graph.outgoing(n.node_id)
            if e.provenance is Provenance.PATTERN and e.type.value == "defines"
        ]
        if not pattern:
            pytest.skip("fixture produced no pattern `defines` edge to collide with")
        target = pattern[0]
        span = graph.get_node(target.dst).raw.strip()[:40]

    extract_bundle(bundle, DefinesExtractor(target.src, target.dst, span))

    with open_bundle(bundle) as graph:
        keys = [
            (e.src, e.dst, e.type.value)
            for n in graph.all_nodes()
            for e in graph.outgoing(n.node_id)
        ]
        dupes = {k for k in keys if keys.count(k) > 1}
        assert not dupes, f"duplicate (src, dst, type) written: {dupes}"

        # And the surviving edge is the stronger-evidence one: a pattern hit
        # that the model also confirmed is VERIFIED, which outranks an
        # unconfirmed model proposal for the identical relation.
        survivor = [
            e
            for n in graph.all_nodes()
            for e in graph.outgoing(n.node_id)
            if (e.src, e.dst, e.type.value) == (target.src, target.dst, "defines")
        ]
        assert len(survivor) == 1
        assert survivor[0].provenance is Provenance.VERIFIED
