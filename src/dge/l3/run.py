"""L3 orchestration: gate, call, validate, stamp, reconcile.

Stdlib only, and no model in sight — the model arrives as an
`dge.interfaces.EdgeExtractor`, so this whole file is testable against a fake
one with no network and no key. That is the same discipline `tests/test_vectors.py`
uses for the embedder, and it is why the ordering rules below can be asserted
in CI rather than hoped for.

Everything a model says is treated as a CLAIM until this module has checked it:

  1. the type is in the closed enum (`dge.l3.prompt.ALLOWED_EDGE_TYPES`);
  2. both endpoints are nodes that were actually in this call's window;
  3. the endpoints differ;
  4. the evidence span is verbatim in the window (`dge.l3.evidence`), and the
     span that gets STORED is the window's own slice, not the model's copy.

A candidate failing any of these is discarded with a recorded reason. Not
logged and kept, not repaired — discarded, per CLAUDE.md invariant 10 and
docs/05 5.6 ("L3 edge with unverifiable evidence span → Discard"). The recorded
reasons are how you tell a bad prompt from a bad model.

Note what checks 2 and 3 defend against that the JSON schema does not: the
schema is sent to the provider, and a provider that ignores `strict`, degrades
to plain JSON mode, or is a local Ollama model with no schema support at all,
can return anything. The adapter is not trusted either — it returns node ids,
and this module verifies those ids were in the window it just built.

MEDIUM markers become candidates; STRONG markers stay edges
-----------------------------------------------------------
BUILD_PLAN Phase 3: "STRONG-confidence hits become edges directly; MEDIUM
become candidates for model verification." Verification here is *agreement*,
not a second prompt: a MEDIUM pattern edge is confirmed when this pass
independently proposes the same (src, dst, type) from the same window. Three
outcomes, and the third is the one CLAUDE.md invariant 5 dictates:

  - confirmed        -> `Provenance.VERIFIED`, model_id and prompt_hash stamped.
  - contradicted     -> KEPT, confidence halved, marked unconfirmed. The model
                        saw the window and did not propose it. That is evidence
                        against, not proof against, so the edge is degraded to a
                        labeled low-confidence state and filtered at traversal
                        time — never deleted at ingest.
  - never examined   -> untouched. No model was ever in a position to form an
                        opinion, and pretending otherwise would make the graph
                        depend on the cost gate's mood. Three ways this
                        happens, and all three are the same case: the gate
                        skipped the section; the call failed; or the edge's
                        two endpoints never appeared in one window together,
                        which is the normal state of a `defines` link from a
                        provision to the definitions section. Only a question
                        the model could actually have answered counts as
                        having been asked.

STRONG pattern edges pass through completely untouched: a drafting convention
is better evidence than a model's opinion about a drafting convention.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from dge.domains.legal import DomainPack
from dge.interfaces import EdgeCandidate, EdgeExtractor
from dge.l3.conflict import OverrideConflict, detect_override_conflicts
from dge.l3.evidence import check_evidence
from dge.l3.prompt import ALLOWED_EDGE_TYPES, NO_RELATION
from dge.l3.sections import (
    DEFAULT_MAX_WINDOW_CHARS,
    GateReport,
    Section,
    apply_gate,
    group_sections,
)
from dge.model import Edge, EdgeType, Node, Provenance

# A pattern edge below this is a MEDIUM marker hit — a candidate for
# verification. At or above it, the marker was lexically unambiguous
# (`Confidence.STRONG` in the pack) and no model gets a vote.
STRONG_PATTERN_CONFIDENCE = 1.0

# What an unconfirmed MEDIUM edge is worth after a model looked at its window
# and did not propose it. Chosen to fall below `Budget.min_edge_confidence`
# (0.5) so the default traversal stops following it, while the edge itself
# stays in the graph, reported and recomputable.
UNCONFIRMED_MULTIPLIER = 0.5


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    section_key: str
    candidate: EdgeCandidate
    reason: str


@dataclass(frozen=True, slots=True)
class L3Report:
    """Everything one L3 pass did, including what it refused to do."""

    edges: tuple[Edge, ...] = ()
    reconciled: tuple[Edge, ...] = ()
    rejected: tuple[RejectedCandidate, ...] = ()
    conflicts: tuple[OverrideConflict, ...] = ()
    gate: GateReport = field(default_factory=lambda: GateReport(0, 0, 0, 0))
    calls: int = 0
    candidates_seen: int = 0
    verified: int = 0
    unconfirmed: int = 0
    failures: tuple[str, ...] = ()

    def summary(self) -> str:
        return (
            f"{self.calls} call(s) over {self.gate.sections_admitted}/"
            f"{self.gate.sections_total} sections ({self.gate.admit_fraction:.0%} "
            f"of sections, {self.gate.char_fraction:.0%} of characters); "
            f"{len(self.edges)} edge(s) kept, {len(self.rejected)} candidate(s) "
            f"discarded, {self.verified} pattern edge(s) verified, "
            f"{self.unconfirmed} unconfirmed"
        )


def _resolve_type(raw_type: str) -> EdgeType | None:
    for known in EdgeType:
        if known.value == raw_type:
            return known
    return None


def validate_candidate(
    candidate: EdgeCandidate, section: Section, *, model_id: str, prompt_hash: str
) -> tuple[Edge | None, str | None]:
    """Turn one model claim into an edge, or into a reason it was discarded.

    Returns `(None, None)` for the explicit null option — a model correctly
    reporting "no relation" is not a rejection and must not be counted as one.
    """
    if candidate.type == NO_RELATION:
        return None, None
    if candidate.type not in ALLOWED_EDGE_TYPES:
        return None, f"type {candidate.type!r} outside the closed enum"

    edge_type = _resolve_type(candidate.type)
    if edge_type is None:  # pragma: no cover - ALLOWED_EDGE_TYPES is built from EdgeType
        return None, f"type {candidate.type!r} is not a known EdgeType"

    in_window = {n.node_id for n in section.nodes}
    if candidate.src not in in_window:
        return None, f"src {candidate.src!r} was not in the call window"
    if candidate.dst not in in_window:
        return None, f"dst {candidate.dst!r} was not in the call window"
    if candidate.src == candidate.dst:
        return None, "src and dst are the same node"

    check = check_evidence(candidate.evidence_span, section.evidence_window)
    if check.span is None:
        return None, f"evidence span {check.reason}"

    confidence = min(1.0, max(0.0, candidate.confidence))
    return Edge(
        edge_id=f"model:{edge_type.value}:{candidate.src}:{candidate.dst}",
        src=candidate.src,
        dst=candidate.dst,
        type=edge_type,
        provenance=Provenance.MODEL,
        confidence=confidence,
        # The WINDOW's characters, never the model's rendering of them — see
        # dge.l3.evidence on why a REFLOWED match is safe.
        evidence_span=check.span,
        # CLAUDE.md invariant 5: model_id, prompt_hash, evidence_span and
        # confidence, on every enrichment, stamped here at the point of
        # creation so there is no window in which an unattributed model edge
        # exists.
        model_id=model_id,
        prompt_hash=prompt_hash,
    ), None


def _reconcile_pattern_edges(
    pattern_edges: Sequence[Edge],
    proposed: Mapping[tuple[str, str, str], float],
    examined_windows: Sequence[frozenset[str]],
    model_id: str,
    prompt_hash: str,
) -> tuple[list[Edge], int, int]:
    out: list[Edge] = []
    verified = unconfirmed = 0
    for edge in pattern_edges:
        medium = (
            edge.provenance is Provenance.PATTERN
            and edge.confidence < STRONG_PATTERN_CONFIDENCE
        )
        # BOTH endpoints, TOGETHER in one examined window — not merely `src`
        # somewhere in the pass. L3 shows the model one section per call and
        # `validate_candidate` discards any endpoint outside that window, so
        # for a pattern edge whose target sits in another section there is no
        # answer the model could have given that would count as confirmation.
        # Degrading it is inferring a denial from a question never asked.
        #
        # Measured on Child_Labour_(Prohibition_and_Regulation)_Act,_1986:
        # 17 of 21 degraded edges were cross-section `defines` links from a
        # provision to the Sec. 2 definition of a term it uses — the most
        # valuable edges in the graph and the ones a statute has most of —
        # all pushed to 0.4, below the 0.5 traversal floor, by a model that
        # was never shown the definitions section.
        if not medium or not any(
            edge.src in window and edge.dst in window for window in examined_windows
        ):
            out.append(edge)
            continue
        model_confidence = proposed.get((edge.src, edge.dst, edge.type.value))
        if model_confidence is not None:
            verified += 1
            out.append(Edge(
                edge_id=edge.edge_id,
                src=edge.src, dst=edge.dst, type=edge.type,
                provenance=Provenance.VERIFIED,
                # The pattern's own confidence is a floor: a hedgy model does
                # not make a real drafting marker less real, it just fails to
                # add to it.
                confidence=max(edge.confidence, model_confidence),
                cross_doc=edge.cross_doc,
                evidence_span=edge.evidence_span,
                model_id=model_id,
                prompt_hash=prompt_hash,
            ))
        else:
            unconfirmed += 1
            out.append(Edge(
                edge_id=edge.edge_id,
                src=edge.src, dst=edge.dst, type=edge.type,
                provenance=edge.provenance,
                confidence=round(edge.confidence * UNCONFIRMED_MULTIPLIER, 4),
                cross_doc=edge.cross_doc,
                evidence_span=edge.evidence_span,
                model_id=model_id,
                prompt_hash=prompt_hash,
            ))
    return out, verified, unconfirmed


def run_l3(
    nodes: Sequence[Node],
    pack: DomainPack,
    extractor: EdgeExtractor,
    *,
    pattern_edges: Sequence[Edge] = (),
    doc_summaries: Mapping[str, str] | None = None,
    max_chars: int = DEFAULT_MAX_WINDOW_CHARS,
) -> L3Report:
    """One L3 pass over one document's nodes.

    The cost gate is consulted before every call and there is no path around
    it: sections are partitioned by `apply_gate` and only the admitted list is
    iterated. L3 is the dominant cost line (docs/05 5.4), so "consulted before
    every call" has to be structural, not a conditional someone can forget.
    """
    sections = group_sections(nodes, max_chars=max_chars)
    admitted, gate = apply_gate(sections, pack)
    summaries = doc_summaries or {}

    edges: list[Edge] = []
    rejected: list[RejectedCandidate] = []
    failures: list[str] = []
    proposed: dict[tuple[str, str, str], float] = {}
    examined_windows: list[frozenset[str]] = []
    calls = candidates_seen = 0

    for section in admitted:
        label = section.path or section.title()
        try:
            candidates = extractor.extract(
                section.nodes, label, summaries.get(section.doc_id, section.doc_id)
            )
        except Exception as exc:  # noqa: BLE001 - a failed call is a recorded row, never a dead batch
            # docs/05 5.6 and CLAUDE.md "no silent excepts": one section's
            # model failure records a reason and the pass continues. The
            # alternative — letting it propagate — loses every edge extracted
            # before it, for a transient rate limit.
            failures.append(f"{section.key}: extractor failed: {type(exc).__name__}: {exc}")
            continue
        calls += 1
        # AFTER the call succeeded, never before. A section whose call raised
        # is in exactly the position of a section the gate skipped: no model
        # formed an opinion about it. Marking it examined up front made a
        # transient 429 silently halve the confidence of every MEDIUM pattern
        # edge in it — measured on the first real corpus run, where
        # Foreign_Marriage_Act,_1969 completed 0 calls and still had 28
        # pattern edges pushed below the 0.5 traversal floor. That is the
        # "cost gate's mood" failure this module's docstring rules out, and
        # it also breaks CLAUDE.md invariant 8: the graph must be a function
        # of the corpus, not of the network's weather.
        examined_windows.append(frozenset(n.node_id for n in section.nodes))

        for candidate in candidates:
            candidates_seen += 1
            edge, reason = validate_candidate(
                candidate, section,
                model_id=extractor.model_id, prompt_hash=extractor.prompt_hash,
            )
            if reason is not None:
                rejected.append(RejectedCandidate(section.key, candidate, reason))
                continue
            if edge is None:
                continue  # explicit null option, exercised correctly
            key = (edge.src, edge.dst, edge.type.value)
            proposed[key] = max(proposed.get(key, 0.0), edge.confidence)
            edges.append(edge)

    reconciled, verified, unconfirmed = _reconcile_pattern_edges(
        pattern_edges, proposed, examined_windows,
        extractor.model_id, extractor.prompt_hash,
    )

    return L3Report(
        edges=tuple(edges),
        reconciled=tuple(reconciled),
        rejected=tuple(rejected),
        conflicts=tuple(detect_override_conflicts(nodes, pack)),
        gate=gate,
        calls=calls,
        candidates_seen=candidates_seen,
        verified=verified,
        unconfirmed=unconfirmed,
        failures=tuple(failures),
    )
