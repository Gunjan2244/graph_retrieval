"""L3 orchestration against a fake extractor.

Same discipline as `tests/test_vectors.py`: a deterministic stand-in conforming
to `dge.interfaces.EdgeExtractor` exercises the gate, the window construction,
the evidence enforcement and the pattern reconciliation with no key, no
network, and no model. What is NOT tested here is whether a real model produces
good edges — that needs a real model, and nothing in BUILD_PLAN.md is ticked on
the strength of these tests.

Most of these are adversarial: the fakes below fabricate spans, cite nodes that
were never in the window, invent edge types and point edges at themselves,
because those are the failures that matter. A model that behaves is not the
case worth pinning.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from dge.domains.legal import get_pack
from dge.edges import extract_marker_edges
from dge.interfaces import EdgeCandidate
from dge.l3.prompt import prompt_hash
from dge.l3.run import UNCONFIRMED_MULTIPLIER, run_l3
from dge.l3.sections import group_sections
from dge.model import Edge, EdgeType, Node, Provenance
from dge.parsing import PlainTextParser, finalize_doc_id

PACK = get_pack("legal")

# Section 12 is dense with closure markers, so the gate admits it.
# Section 20 carries no gate term at all, so it must never reach a model.
DOC = (
    b"Section 12. Limitation on transfer.\n\n"
    b"(1) No person shall transfer any specified asset without the prior written\n"
    b"approval of the Authority.\n\n"
    b"Provided that nothing in this section shall apply to a transfer effected by\n"
    b"operation of law.\n\n"
    b"Section 20. Short title.\n\n"
    b"(1) This Act may be called the Test Act, 2020 and shall come into force on\n"
    b"such date as the Central Government may, by notification, appoint.\n"
)


def _nodes() -> tuple[list[Node], list[Edge]]:
    result = PlainTextParser().parse(DOC)
    return finalize_doc_id("d", list(result.nodes), list(result.structural_edges))


class RecordingExtractor:
    """Returns a scripted candidate list and records every window it saw."""

    model_id = "fake:recorded-v1"
    prompt_hash = prompt_hash()

    def __init__(self, script: object = None) -> None:
        self.calls: list[tuple[tuple[Node, ...], str]] = []
        self._script = script

    def extract(
        self, section_nodes: Sequence[Node], section_path: str, doc_summary: str
    ) -> Sequence[EdgeCandidate]:
        self.calls.append((tuple(section_nodes), section_path))
        if callable(self._script):
            return self._script(section_nodes)
        return ()


def _proviso_and_rule(nodes: Sequence[Node]) -> tuple[Node, Node]:
    rule = next(n for n in nodes if "No person shall transfer" in n.raw)
    proviso = next(n for n in nodes if n.raw.lstrip().startswith("Provided that"))
    return proviso, rule


# ---------------------------------------------------------------------------
# The cost gate
# ---------------------------------------------------------------------------


def test_gate_is_consulted_before_every_call_and_short_title_never_reaches_a_model():
    nodes, _ = _nodes()
    extractor = RecordingExtractor()
    report = run_l3(nodes, PACK, extractor)

    seen = {n.node_id for call in extractor.calls for n in call[0]}
    short_title = next(n for n in nodes if "Short title" in n.raw)
    assert short_title.node_id not in seen, "a section with no gate term must cost nothing"
    assert report.gate.sections_admitted < report.gate.sections_total
    assert report.calls == report.gate.sections_admitted


def test_one_section_per_call_windows_never_mix_sections():
    nodes, _ = _nodes()
    extractor = RecordingExtractor()
    run_l3(nodes, PACK, extractor)

    section_of = {
        n.node_id: s.key for s in group_sections(nodes) for n in s.nodes
    }
    for window, _path in extractor.calls:
        assert len({section_of[n.node_id] for n in window}) == 1


# ---------------------------------------------------------------------------
# Invariant 10, at the orchestration layer
# ---------------------------------------------------------------------------


def test_fabricated_evidence_span_is_discarded_however_confident_the_model_is():
    nodes, _ = _nodes()
    proviso, rule = _proviso_and_rule(nodes)

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=rule.node_id, type="exception_of",
            # A true statement about the section that appears nowhere in it.
            evidence_span="transfers by operation of law are exempt from approval",
            confidence=0.99,
        )]

    report = run_l3(nodes, PACK, RecordingExtractor(script))
    assert report.edges == ()
    assert len(report.rejected) == 1
    assert report.rejected[0].reason.startswith("evidence span")


def test_edge_pointing_outside_the_call_window_is_discarded():
    nodes, _ = _nodes()
    proviso, _rule = _proviso_and_rule(nodes)
    outsider = next(n for n in nodes if "Short title" in n.raw)

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=outsider.node_id, type="exception_of",
            evidence_span="Provided that nothing in this section shall apply",
            confidence=0.9,
        )]

    report = run_l3(nodes, PACK, RecordingExtractor(script))
    assert report.edges == ()
    assert "was not in the call window" in report.rejected[0].reason


def test_stored_evidence_span_is_window_text_not_the_models_rendering():
    nodes, _ = _nodes()
    proviso, rule = _proviso_and_rule(nodes)
    # The window's text is hard-wrapped; the model quotes it on one line.
    reflowed = "Provided that nothing in this section shall apply to a transfer effected by operation of law."

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=rule.node_id, type="exception_of",
            evidence_span=reflowed, confidence=0.8,
        )]

    report = run_l3(nodes, PACK, RecordingExtractor(script))
    assert len(report.edges) == 1
    stored = report.edges[0].evidence_span
    assert stored != reflowed, "the model's whitespace must not be what gets stored"
    assert stored in proviso.raw, "stored span must be verbatim substrate text"


def test_type_outside_the_closed_enum_is_discarded():
    nodes, _ = _nodes()
    proviso, rule = _proviso_and_rule(nodes)

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=rule.node_id, type="contradicts",
            evidence_span="Provided that nothing in this section shall apply",
            confidence=1.0,
        )]

    report = run_l3(nodes, PACK, RecordingExtractor(script))
    assert report.edges == ()
    assert "outside the closed enum" in report.rejected[0].reason


def test_self_edge_is_discarded():
    nodes, _ = _nodes()
    proviso, _rule = _proviso_and_rule(nodes)

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=proviso.node_id, type="exception_of",
            evidence_span="Provided that nothing in this section shall apply",
            confidence=1.0,
        )]

    report = run_l3(nodes, PACK, RecordingExtractor(script))
    assert report.edges == ()
    assert report.rejected[0].reason == "src and dst are the same node"


def test_explicit_null_option_is_not_counted_as_a_rejection():
    nodes, _ = _nodes()
    proviso, rule = _proviso_and_rule(nodes)

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        return [EdgeCandidate(
            src=proviso.node_id, dst=rule.node_id, type="none",
            evidence_span="", confidence=0.0,
        )]

    report = run_l3(nodes, PACK, RecordingExtractor(script))
    assert report.edges == ()
    assert report.rejected == (), "'no relation' is a correct answer, not a failure"
    assert report.candidates_seen > 0


# ---------------------------------------------------------------------------
# Invariant 5 — provenance on everything
# ---------------------------------------------------------------------------


def test_every_kept_edge_carries_model_id_prompt_hash_span_and_confidence():
    nodes, _ = _nodes()
    proviso, rule = _proviso_and_rule(nodes)

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=rule.node_id, type="exception_of",
            evidence_span="Provided that nothing in this section shall apply",
            confidence=0.77,
        )]

    extractor = RecordingExtractor(script)
    report = run_l3(nodes, PACK, extractor)
    edge = report.edges[0]
    assert edge.provenance is Provenance.MODEL
    assert edge.confidence == 0.77
    assert edge.evidence_span
    assert edge.model_id == "fake:recorded-v1"
    assert edge.prompt_hash == prompt_hash()


def test_out_of_range_confidence_is_clamped_not_trusted():
    nodes, _ = _nodes()
    proviso, rule = _proviso_and_rule(nodes)

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=rule.node_id, type="exception_of",
            evidence_span="Provided that nothing in this section shall apply",
            confidence=7.5,
        )]

    report = run_l3(nodes, PACK, RecordingExtractor(script))
    assert report.edges[0].confidence == 1.0


def test_edge_ids_are_deterministic_so_a_rerun_dedupes_rather_than_duplicates():
    nodes, _ = _nodes()
    proviso, rule = _proviso_and_rule(nodes)

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=rule.node_id, type="exception_of",
            evidence_span="Provided that nothing in this section shall apply",
            confidence=0.6,
        )]

    first = run_l3(nodes, PACK, RecordingExtractor(script))
    second = run_l3(nodes, PACK, RecordingExtractor(script))
    assert [e.edge_id for e in first.edges] == [e.edge_id for e in second.edges]


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_a_failing_call_records_a_reason_and_does_not_kill_the_batch():
    nodes, _ = _nodes()

    class Exploding(RecordingExtractor):
        def extract(self, section_nodes, section_path, doc_summary):  # type: ignore[no-untyped-def]
            super().extract(section_nodes, section_path, doc_summary)
            raise RuntimeError("rate limited")

    report = run_l3(nodes, PACK, Exploding())
    assert report.edges == ()
    assert report.failures and "rate limited" in report.failures[0]
    assert report.calls == 0


# ---------------------------------------------------------------------------
# MEDIUM markers become candidates; STRONG stay edges
# ---------------------------------------------------------------------------


def _medium_and_strong(nodes: Sequence[Node], struct: Sequence[Edge]) -> tuple[Edge, ...]:
    edges, _warnings = extract_marker_edges(nodes, PACK, struct)
    return tuple(edges)


def test_strong_pattern_edges_are_never_touched_by_the_model_pass():
    nodes, struct = _nodes()
    pattern = _medium_and_strong(nodes, struct)
    strong = [e for e in pattern if e.confidence == 1.0]
    if not strong:
        pytest.skip("fixture produced no STRONG marker edge")

    report = run_l3(nodes, PACK, RecordingExtractor(), pattern_edges=pattern)
    after = {e.edge_id: e for e in report.reconciled}
    for edge in strong:
        assert after[edge.edge_id] == edge


def test_medium_marker_confirmed_by_the_model_becomes_verified():
    nodes, _struct = _nodes()
    proviso, rule = _proviso_and_rule(nodes)
    medium = Edge(
        edge_id="marker:test:medium", src=proviso.node_id, dst=rule.node_id,
        type=EdgeType.EXCEPTION_OF, provenance=Provenance.PATTERN, confidence=0.6,
        evidence_span="Provided that",
    )

    def script(window: Sequence[Node]) -> Sequence[EdgeCandidate]:
        if proviso not in window:
            return ()
        return [EdgeCandidate(
            src=proviso.node_id, dst=rule.node_id, type="exception_of",
            evidence_span="Provided that nothing in this section shall apply",
            confidence=0.9,
        )]

    report = run_l3(nodes, PACK, RecordingExtractor(script), pattern_edges=[medium])
    out = next(e for e in report.reconciled if e.edge_id == "marker:test:medium")
    assert out.provenance is Provenance.VERIFIED
    assert out.confidence == 0.9
    assert out.model_id == "fake:recorded-v1"
    assert out.prompt_hash == prompt_hash()
    assert report.verified == 1


def test_medium_marker_the_model_declined_is_kept_but_degraded_never_deleted():
    nodes, _struct = _nodes()
    proviso, rule = _proviso_and_rule(nodes)
    medium = Edge(
        edge_id="marker:test:medium", src=proviso.node_id, dst=rule.node_id,
        type=EdgeType.EXCEPTION_OF, provenance=Provenance.PATTERN, confidence=0.6,
        evidence_span="Provided that",
    )

    report = run_l3(nodes, PACK, RecordingExtractor(), pattern_edges=[medium])
    out = next(e for e in report.reconciled if e.edge_id == "marker:test:medium")
    # CLAUDE.md invariant 5: filter at traversal time, never delete at ingest.
    assert out.confidence == pytest.approx(0.6 * UNCONFIRMED_MULTIPLIER)
    assert out.confidence < 0.5, "must fall below the default traversal floor"
    assert report.unconfirmed == 1


def test_medium_marker_in_a_gated_out_section_is_left_alone():
    nodes, _struct = _nodes()
    short_title = next(n for n in nodes if "This Act may be called" in n.raw)
    heading = next(n for n in nodes if "Short title" in n.raw)
    medium = Edge(
        edge_id="marker:test:ungated", src=short_title.node_id, dst=heading.node_id,
        type=EdgeType.CONDITIONED_ON, provenance=Provenance.PATTERN, confidence=0.6,
    )

    report = run_l3(nodes, PACK, RecordingExtractor(), pattern_edges=[medium])
    out = next(e for e in report.reconciled if e.edge_id == "marker:test:ungated")
    # No model formed an opinion, so the graph must not pretend one did.
    assert out == medium
    assert report.unconfirmed == 0
