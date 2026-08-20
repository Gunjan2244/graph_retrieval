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

from dge.bundle import open_bundle, write_bundle, write_node_vectors
from dge.domains.legal import get_pack
from dge.edges import extract_marker_edges, extract_structural_edges
from dge.interfaces import Embedder
from dge.lexicon import extract_terms, link_mentions
from dge.model import DocStatus, Document, Edge, Node, Term
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
    warnings: tuple[str, ...] = field(default_factory=tuple)


def embed_bundle(bundle_path: Path, embedder: Embedder) -> EmbedSummary:
    """L2: compute and write vectors into an already-ingested bundle.

    Deliberately a separate entry point from `ingest_documents`, not a stage
    inside it — L2 is disposable and independently re-runnable (CLAUDE.md
    architecture table): swapping the embedder means calling this again, never
    re-running L0/L1/L3.

    Embeds each document's nodes in ONE call per document, not one call for the
    whole corpus. This matters beyond batching: a contextual embedder
    (`dge.adapters.embed_hosted.VoyageEmbedder`) uses the grouping itself to
    contextualize chunks against their true siblings — mixing nodes from
    different documents into one call would contextualize them against each
    other, which is wrong, not just slower.
    """
    warnings: list[str] = []
    nodes_embedded = 0
    docs_embedded = 0

    with open_bundle(bundle_path) as graph:
        for doc in graph.documents():
            if doc.review_state == "pending":
                warnings.append(f"{doc.doc_id}: review-pending, skipped (no L1 ran)")
                continue
            doc_nodes = graph.nodes_in_doc_order(doc.doc_id)
            if not doc_nodes:
                continue

            texts = [n.normalized or n.raw for n in doc_nodes]
            doc_summary = doc.source_uri or doc.doc_id
            vectors = embedder.embed_documents(texts, doc_context=doc_summary)
            if len(vectors) != len(doc_nodes):
                warnings.append(
                    f"{doc.doc_id}: embedder returned {len(vectors)} vectors for "
                    f"{len(doc_nodes)} nodes; skipped"
                )
                continue

            write_node_vectors(
                bundle_path,
                model_id=embedder.model_id,
                dim=embedder.dim,
                vectors={n.node_id: v for n, v in zip(doc_nodes, vectors)},
            )
            nodes_embedded += len(doc_nodes)
            docs_embedded += 1

    return EmbedSummary(
        bundle_path=bundle_path,
        model_id=embedder.model_id,
        documents_embedded=docs_embedded,
        nodes_embedded=nodes_embedded,
        warnings=tuple(warnings),
    )
