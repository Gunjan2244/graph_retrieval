"""Bundle writer: the single-file SQLite artifact described in
docs/02-architecture.md 2.3 — original bytes, substrate, terms, edges,
manifest, all in one portable file. Vectors are written when an embedder ran;
L2 is disposable by design (CLAUDE.md architecture table), so their absence
never invalidates the bundle.

The DDL is `sql/schema.sql`, read from disk rather than duplicated here, so
there is exactly one authoritative copy (dge.model's docstring makes the same
promise for the dataclasses — both mirror the same file).
"""

from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from dge.model import Document, Edge, Node, Term

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"

DGE_SCHEMA_VERSION = "0.1.0"


def _load_schema() -> str:
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"sql/schema.sql not found at {_SCHEMA_PATH}; the bundle writer reads the "
            "DDL from the repo rather than embedding a copy, so it must be run from "
            "a checkout that has sql/schema.sql alongside src/."
        )
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def write_bundle(
    out_path: Path,
    *,
    domain: str,
    documents: Sequence[Document],
    document_bytes: dict[str, bytes],
    nodes: Sequence[Node],
    terms: Sequence[Term],
    edges: Sequence[Edge],
    ingest_ledger: Sequence[dict[str, object]],
    source_files: Sequence[str],
) -> None:
    """Write a fresh bundle to `out_path`. Overwrites any existing file at that
    path — the bundle is a build artifact, recomputable from source documents
    (CLAUDE.md invariant 8), never hand-edited."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(out_path)
    try:
        conn.executescript(_load_schema())
        with conn:
            _write_documents(conn, documents)
            _write_blobs(conn, document_bytes)
            _write_nodes(conn, nodes)
            _write_terms(conn, terms)
            _write_edges(conn, edges)
            _write_ledger(conn, ingest_ledger)
            _write_manifest(conn, domain, documents, nodes, edges, terms, source_files)
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_documents(conn: sqlite3.Connection, documents: Sequence[Document]) -> None:
    conn.executemany(
        """INSERT INTO documents
           (doc_id, substrate_hash, source_uri, doc_class, effective_date, status,
            superseded_by, parse_confidence, review_state, tenant_id, acl_tag, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (d.doc_id, d.substrate_hash, d.source_uri, d.doc_class, d.effective_date,
             d.status.value, d.superseded_by, d.parse_confidence, d.review_state,
             d.tenant_id, d.acl_tag, d.ingested_at)
            for d in documents
        ],
    )


def _write_blobs(conn: sqlite3.Connection, document_bytes: dict[str, bytes]) -> None:
    conn.executemany(
        "INSERT INTO document_blobs (doc_id, raw_base64, content_type, filename) VALUES (?, ?, ?, ?)",
        [
            (doc_id, base64.b64encode(raw).decode("ascii"), "text/plain", None)
            for doc_id, raw in document_bytes.items()
        ],
    )


def _write_nodes(conn: sqlite3.Connection, nodes: Sequence[Node]) -> None:
    conn.executemany(
        """INSERT INTO nodes
           (node_id, doc_id, kind, seq, byte_start, byte_end, raw, normalized,
            section_path, inherited_ctx, is_assertive, status, layer1_version, model_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                n.node_id, n.doc_id, n.kind.value, n.seq, n.byte_start, n.byte_end,
                n.raw, n.normalized, n.inherited.section_path,
                json.dumps(asdict(n.inherited)), int(n.is_assertive), n.status.value,
                n.layer1_version, n.model_id,
            )
            for n in nodes
        ],
    )


def _write_terms(conn: sqlite3.Connection, terms: Sequence[Term]) -> None:
    conn.executemany(
        """INSERT INTO terms
           (term_id, surface_form, canonical, scope_node_id, definition_node_id,
            definition_kind, gloss, variants, provenance, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (t.term_id, t.surface_form, t.canonical, t.scope_node_id, t.definition_node_id,
             t.definition_kind, t.gloss, json.dumps(list(t.variants)), t.provenance.value,
             t.confidence)
            for t in terms
        ],
    )


def _write_edges(conn: sqlite3.Connection, edges: Sequence[Edge]) -> None:
    now = _now()
    conn.executemany(
        """INSERT INTO edges
           (edge_id, src, dst, type, class, cross_doc, confidence, provenance,
            model_id, prompt_hash, evidence_span, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (e.edge_id, e.src, e.dst, e.type.value, e.cls.value, int(e.cross_doc),
             e.confidence, e.provenance.value, e.model_id, e.prompt_hash,
             e.evidence_span, now)
            for e in edges
        ],
    )


def _write_ledger(conn: sqlite3.Connection, rows: Sequence[dict[str, object]]) -> None:
    conn.executemany(
        """INSERT INTO ingest_ledger
           (doc_id, stage, duration_ms, input_tokens, output_tokens, model_id, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (r["doc_id"], r["stage"], r.get("duration_ms"), r.get("input_tokens"),
             r.get("output_tokens"), r.get("model_id"), r.get("recorded_at", _now()))
            for r in rows
        ],
    )


def _write_manifest(
    conn: sqlite3.Connection,
    domain: str,
    documents: Sequence[Document],
    nodes: Sequence[Node],
    edges: Sequence[Edge],
    terms: Sequence[Term],
    source_files: Sequence[str],
) -> None:
    closure_edges = sum(1 for e in edges if e.cls.value == "closure")
    entries = {
        "dge_schema_version": DGE_SCHEMA_VERSION,
        "domain": domain,
        "created_at": _now(),
        "document_count": str(len(documents)),
        "node_count": str(len(nodes)),
        "term_count": str(len(terms)),
        "edge_count": str(len(edges)),
        "closure_edge_count": str(closure_edges),
        "context_edge_count": str(len(edges) - closure_edges),
        "source_files": json.dumps(list(source_files)),
        "review_pending_docs": json.dumps(
            [d.doc_id for d in documents if d.review_state == "pending"]
        ),
    }
    conn.executemany(
        "INSERT INTO manifest (key, value) VALUES (?, ?)",
        list(entries.items()),
    )
