"""Orchestrates one ingest run: one or more source documents in, one bundle
file out. This is the "insert documents -> enriched file" product surface.

Stages, per docs/05-engine-implementation.md 5.1, run in this order for each
document: parse (L0) -> gate on confidence -> normalize (L1) -> lexicon
(definitions + mentions) -> deterministic + pattern edges (L3a). Every stage
writes a timing row to the ingest ledger (CLAUDE.md "definition of done" #4).
A document whose parse confidence is below threshold is written with
`review_state='pending'` and none of its downstream layers run — CLAUDE.md
invariant 9: degrade to labeled low confidence, never proceed on a corrupt
substrate. It does not block the rest of the batch.

L3b (LLM-verified edges) and L2 (embeddings) are pluggable per
`dge.interfaces` and are not run by this pipeline by default — see
`dge.adapters` for how to wire a real model in. Running fully offline by
default is deliberate: the deterministic layers (structure, lexical markers,
definitions) already carry most of the real signal in statutory text
(docs/05-engine-implementation.md 5.4), and everything they produce is
provenance-tagged so a later, model-backed pass can only ever add edges, never
silently replace what shipped here.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dge.bundle import open_bundle, write_bundle, write_model_edges, write_node_vectors
from dge.domains.legal import get_pack
from dge.edges import extract_marker_edges, extract_structural_edges
from dge.interfaces import EdgeExtractor, Embedder
from dge.l3.conflict import OverrideConflict, detect_override_conflicts
from dge.l3.run import L3Report, run_l3
from dge.l3.sections import DEFAULT_MAX_WINDOW_CHARS, GateReport, apply_gate, group_sections
from dge.lexicon import extract_terms, link_mentions
from dge.model import DocStatus, Document, Edge, Node, Provenance, Term
from dge.normalize import MODEL_ID as NORMALIZER_MODEL_ID
from dge.normalize import DeterministicNormalizer
from dge.parsing import PlainTextParser, finalize_doc_id

PARSE_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class IngestSummary:
    bundle_path: Path
    documents: int
    documents_review_pending: int
    nodes: int
    terms: int
    edges: int
    closure_edges: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dedupe_edges(edges: list[Edge]) -> list[Edge]:
    """The parser's generic paragraph->section PART_OF and the domain pack's
    `sub_section`/`clause` structural units both fire on numbered clauses,
    producing the same (src, dst, type) twice under different edge_ids. Real
    signal, redundant edge — and a duplicate silently inflates the degree
    penalty in `dge.traversal.policy.frontier_score`, which exists precisely
    to tell hubs apart from genuinely well-connected nodes. Keep the first,
    highest-confidence occurrence."""
    best: dict[tuple[str, str, str], Edge] = {}
    for e in edges:
        key = (e.src, e.dst, e.type.value)
        current = best.get(key)
        if current is None or e.confidence > current.confidence:
            best[key] = e
    return list(best.values())


def _ledger_row(doc_id: str, stage: str, duration_ms: float, model_id: str | None = None) -> dict[str, object]:
    return {
        "doc_id": doc_id,
        "stage": stage,
        "duration_ms": round(duration_ms, 3),
        "model_id": model_id,
        "recorded_at": _now(),
    }


def ingest_documents(
    paths: Sequence[Path],
    *,
    domain: str = "legal",
    tenant_id: str = "default",
    out_path: Path,
    acl_tag: str | None = None,
) -> IngestSummary:
    """Idempotent: doc_id is a content hash, so re-ingesting the same bytes
    produces the same doc_id, node_ids, and edge_ids (all derived, not
    random) — running this twice on the same input yields the same bundle."""
    pack = get_pack(domain)
    parser = PlainTextParser()
    normalizer = DeterministicNormalizer()

    documents: list[Document] = []
    document_bytes: dict[str, bytes] = {}
    all_nodes: list[Node] = []
    all_terms: list[Term] = []
    all_edges: list[Edge] = []
    ledger: list[dict[str, object]] = []
    warnings: list[str] = []

    for path in paths:
        raw = path.read_bytes()
        substrate_hash = hashlib.sha256(raw).hexdigest()
        doc_id = substrate_hash[:16]
        if doc_id in document_bytes:
            warnings.append(f"{path}: identical content already ingested in this run; skipped")
            continue

        t0 = time.perf_counter()
        parse_result = parser.parse(raw, doc_class=domain)
        ledger.append(_ledger_row(doc_id, "parse", (time.perf_counter() - t0) * 1000))
        warnings.extend(f"{path}: {w}" for w in parse_result.warnings)

        doc_nodes: list[Node] = []
        doc_terms: list[Term] = []
        doc_edges: list[Edge] = []
        review_state = "none"
        status = DocStatus.CURRENT

        if parse_result.confidence < PARSE_CONFIDENCE_THRESHOLD:
            review_state = "pending"
            status = DocStatus.UNKNOWN
            warnings.append(
                f"{path}: parse confidence {parse_result.confidence:.2f} < "
                f"{PARSE_CONFIDENCE_THRESHOLD}; halted for review, no downstream layers ran"
            )
        else:
            nodes, struct_edges = finalize_doc_id(
                doc_id, list(parse_result.nodes), list(parse_result.structural_edges)
            )

            t0 = time.perf_counter()
            nodes = list(normalizer.normalize(nodes, doc_summary=path.name))
            ledger.append(_ledger_row(doc_id, "normalize", (time.perf_counter() - t0) * 1000,
                                       model_id=NORMALIZER_MODEL_ID))

            t0 = time.perf_counter()
            doc_terms = extract_terms(nodes, pack)
            mention_edges = link_mentions(nodes, doc_terms)
            ledger.append(_ledger_row(doc_id, "lexicon", (time.perf_counter() - t0) * 1000))

            t0 = time.perf_counter()
            pattern_struct_edges = extract_structural_edges(nodes, pack, struct_edges)
            marker_edges, marker_warnings = extract_marker_edges(nodes, pack, struct_edges)
            ledger.append(_ledger_row(doc_id, "edges:det", (time.perf_counter() - t0) * 1000))
            warnings.extend(f"{path}: {w}" for w in marker_warnings)

            doc_nodes = nodes
            doc_edges = _dedupe_edges(
                [*struct_edges, *mention_edges, *pattern_struct_edges, *marker_edges]
            )

        documents.append(Document(
            doc_id=doc_id,
            substrate_hash=substrate_hash,
            source_uri=str(path),
            doc_class=None,  # genre classification (contract/amendment/policy) is unbuilt (BUILD_PLAN Phase 1)
            tenant_id=tenant_id,
            ingested_at=_now(),
            status=status,
            parse_confidence=parse_result.confidence,
            review_state=review_state,
            acl_tag=acl_tag,
        ))
        document_bytes[doc_id] = raw
        all_nodes.extend(doc_nodes)
        all_terms.extend(doc_terms)
        all_edges.extend(doc_edges)

    write_bundle(
        out_path,
        domain=domain,
        documents=documents,
        document_bytes=document_bytes,
        nodes=all_nodes,
        terms=all_terms,
        edges=all_edges,
        ingest_ledger=ledger,
        source_files=[str(p) for p in paths],
    )

    closure_edges = sum(1 for e in all_edges if e.cls.value == "closure")
    return IngestSummary(
        bundle_path=out_path,
        documents=len(documents),
        documents_review_pending=sum(1 for d in documents if d.review_state == "pending"),
        nodes=len(all_nodes),
        terms=len(all_terms),
        edges=len(all_edges),
        closure_edges=closure_edges,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class EmbedSummary:
    bundle_path: Path
    model_id: str
    documents_embedded: int
    nodes_embedded: int
    nodes_skipped: int = 0  # already carried this model's vector; resume path
    warnings: tuple[str, ...] = field(default_factory=tuple)


def embed_bundle(bundle_path: Path, embedder: Embedder) -> EmbedSummary:
    """L2: compute and write vectors into an already-ingested bundle.

    Deliberately a separate entry point from `ingest_documents`, not a stage
    inside it — L2 is disposable and independently re-runnable (CLAUDE.md
    architecture table): swapping the embedder means calling this again, never
    re-running L0/L1/L3.

    Never mixes nodes from different documents into one call: a contextual
    embedder (`dge.adapters.embed_hosted.VoyageEmbedder`) uses that grouping to
    contextualize chunks against their true siblings, so crossing a document
    boundary would silently change what the vectors mean.

    WITHIN a document the call is split only when the embedder declares
    `max_batch` (see `dge.interfaces.Embedder`), which a contextual embedder
    must not do. That opt-in exists because one call per document is unbounded
    in the number of texts: on the corpus's largest act (615 nodes) it drove
    ONNX Runtime to ~3.9GB and was OOM-killed, leaving that document — and 4 of
    15 labeled eval cases with it — silently unembedded while the run above it
    reported success.

    Resumable: nodes already carrying a vector from this `model_id` are
    skipped, so a re-run after an interruption costs only the missing work.
    Re-running start to finish still produces identical output for identical
    input, since a skipped node holds the vector this call would have written.
    """
    warnings: list[str] = []
    nodes_embedded = 0
    nodes_skipped = 0
    docs_embedded = 0

    raw_batch = getattr(embedder, "max_batch", None)
    max_batch = raw_batch if isinstance(raw_batch, int) and raw_batch > 0 else None

    with open_bundle(bundle_path) as graph:
        already = graph.embedded_node_ids(embedder.model_id)
        for doc in graph.documents():
            if doc.review_state == "pending":
                warnings.append(f"{doc.doc_id}: review-pending, skipped (no L1 ran)")
                continue
            doc_nodes = graph.nodes_in_doc_order(doc.doc_id)
            if not doc_nodes:
                continue

            pending = [n for n in doc_nodes if n.node_id not in already]
            nodes_skipped += len(doc_nodes) - len(pending)
            if not pending:
                continue

            doc_summary = doc.source_uri or doc.doc_id
            step = max_batch or len(pending)
            written = 0
            for start in range(0, len(pending), step):
                chunk = pending[start:start + step]
                texts = [n.normalized or n.raw for n in chunk]
                try:
                    vectors = embedder.embed_documents(texts, doc_context=doc_summary)
                except (MemoryError, RuntimeError) as exc:
                    # A Python-level failure is recordable; a kernel OOM kill is
                    # not catchable at all, which is why `max_batch` prevention
                    # matters more than this handler.
                    warnings.append(
                        f"{doc.doc_id}: embedder failed at node {start} of "
                        f"{len(pending)}: {type(exc).__name__}: {exc}"
                    )
                    break
                if len(vectors) != len(chunk):
                    warnings.append(
                        f"{doc.doc_id}: embedder returned {len(vectors)} vectors for "
                        f"{len(chunk)} nodes at offset {start}; stopped"
                    )
                    break

                write_node_vectors(
                    bundle_path,
                    model_id=embedder.model_id,
                    dim=embedder.dim,
                    vectors={n.node_id: v for n, v in zip(chunk, vectors)},
                )
                written += len(chunk)

            nodes_embedded += written
            if written == len(pending):
                docs_embedded += 1

    return EmbedSummary(
        bundle_path=bundle_path,
        model_id=embedder.model_id,
        documents_embedded=docs_embedded,
        nodes_embedded=nodes_embedded,
        nodes_skipped=nodes_skipped,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class ExtractSummary:
    bundle_path: Path
    model_id: str
    documents: int
    gate: GateReport
    calls: int
    edges_written: int
    candidates_rejected: int
    verified: int
    unconfirmed: int
    conflicts: tuple[OverrideConflict, ...] = field(default_factory=tuple)
    failures: tuple[str, ...] = field(default_factory=tuple)


def plan_extraction(
    bundle_path: Path, *, domain: str = "legal", max_chars: int = DEFAULT_MAX_WINDOW_CHARS
) -> tuple[GateReport, tuple[OverrideConflict, ...]]:
    """What an L3 run WOULD cost, and what the deterministic layers already
    found, without making a single model call.

    This is the zero-key path. L3 is the dominant cost line (docs/05 5.4), so
    being able to price a corpus before paying for it is not a convenience —
    and the conflict findings are deterministic, so they are available here
    too, with no model and no network.
    """
    pack = get_pack(domain)
    total = GateReport(0, 0, 0, 0)
    conflicts: list[OverrideConflict] = []
    with open_bundle(bundle_path) as graph:
        for doc in graph.documents():
            if doc.review_state == "pending":
                continue
            nodes = graph.nodes_in_doc_order(doc.doc_id)
            _admitted, report = apply_gate(group_sections(nodes, max_chars=max_chars), pack)
            total = GateReport(
                total.sections_total + report.sections_total,
                total.sections_admitted + report.sections_admitted,
                total.chars_total + report.chars_total,
                total.chars_admitted + report.chars_admitted,
            )
            conflicts.extend(detect_override_conflicts(nodes, pack))
    return total, tuple(conflicts)


def extract_bundle(
    bundle_path: Path,
    extractor: EdgeExtractor,
    *,
    domain: str = "legal",
    max_chars: int = DEFAULT_MAX_WINDOW_CHARS,
) -> ExtractSummary:
    """L3b: run the model extractor over an already-ingested bundle.

    A separate entry point from `ingest_documents` for the same reason
    `embed_bundle` is: L3 is expensive and independently re-runnable, and
    forcing a re-parse to re-extract would make the expensive layer hostage to
    the cheap one. Re-running converges rather than accumulating, because
    `edge_id`s are deterministic (`dge.bundle.write_model_edges`).

    Review-pending documents are skipped outright — CLAUDE.md invariant 9: no
    downstream layer runs on a substrate that failed its gate, and L3 is the
    most expensive way to process a document that should not be processed.
    """
    pack = get_pack(domain)
    reports: list[L3Report] = []
    to_write: list[Edge] = []
    docs = 0

    with open_bundle(bundle_path) as graph:
        for doc in graph.documents():
            if doc.review_state == "pending":
                continue
            nodes = graph.nodes_in_doc_order(doc.doc_id)
            if not nodes:
                continue
            existing = [
                e
                for node in nodes
                for e in graph.outgoing(node.node_id)
                if e.provenance is Provenance.PATTERN
            ]
            report = run_l3(
                nodes, pack, extractor,
                pattern_edges=existing,
                doc_summaries={doc.doc_id: doc.source_uri or doc.doc_id},
                max_chars=max_chars,
            )
            reports.append(report)
            docs += 1

            # A model edge and a pattern edge can describe the SAME relation
            # under different edge_ids (`model:defines:...` vs
            # `mention:...`). Both would be written, and a duplicate
            # (src, dst, type) silently inflates the degree penalty in
            # `dge.traversal.policy.frontier_score` — the term that exists to
            # tell hubs apart from genuinely well-connected nodes. This is the
            # same hazard `_dedupe_edges` guards at ingest; L3 writes bypassed
            # it because model and reconciled edges arrive as separate lists.
            #
            # The pattern edge wins, deliberately: after reconciliation it
            # carries VERIFIED provenance (pattern-detected AND
            # model-confirmed), which is strictly stronger evidence than an
            # unconfirmed model proposal for the identical relation.
            pattern_keys = {
                (e.src, e.dst, e.type.value) for e in report.reconciled
            }
            to_write.extend(
                e for e in report.edges
                if (e.src, e.dst, e.type.value) not in pattern_keys
            )
            # Only reconciled edges that actually CHANGED are rewritten; an
            # untouched pattern edge does not need a new created_at.
            by_id = {e.edge_id: e for e in existing}
            to_write.extend(
                e for e in report.reconciled if by_id.get(e.edge_id) != e
            )

    written = write_model_edges(bundle_path, to_write) if to_write else 0
    gate = GateReport(
        sum(r.gate.sections_total for r in reports),
        sum(r.gate.sections_admitted for r in reports),
        sum(r.gate.chars_total for r in reports),
        sum(r.gate.chars_admitted for r in reports),
    )
    return ExtractSummary(
        bundle_path=bundle_path,
        model_id=extractor.model_id,
        documents=docs,
        gate=gate,
        calls=sum(r.calls for r in reports),
        edges_written=written,
        candidates_rejected=sum(len(r.rejected) for r in reports),
        verified=sum(r.verified for r in reports),
        unconfirmed=sum(r.unconfirmed for r in reports),
        conflicts=tuple(c for r in reports for c in r.conflicts),
        failures=tuple(f for r in reports for f in r.failures),
    )
