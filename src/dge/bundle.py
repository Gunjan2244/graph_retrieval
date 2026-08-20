"""Bundle writer and reader: the single-file SQLite artifact described in
docs/02-architecture.md 2.3 — original bytes, substrate, terms, edges,
manifest, all in one portable file. Vectors are written when an embedder ran;
L2 is disposable by design (CLAUDE.md architecture table), so their absence
never invalidates the bundle.

The DDL is `sql/schema.sql`, read from disk rather than duplicated here, so
there is exactly one authoritative copy (dge.model's docstring makes the same
promise for the dataclasses — both mirror the same file).

`BundleGraph` is the read side: it implements `dge.traversal.graph.Graph`
against the SQLite file, backing `incoming()` with `idx_edges_dst`. Traversal
code is written against the Protocol, so the same closure walk runs over a
fixture graph in tests and over a bundle here.
"""

from __future__ import annotations

import array
import base64
import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from dge.model import (
    DocStatus,
    Document,
    Edge,
    EdgeType,
    InheritedContext,
    Node,
    NodeKind,
    Provenance,
    Term,
)

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
    return datetime.now(UTC).isoformat()


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


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


def _row_to_node(row: sqlite3.Row) -> Node:
    ctx_raw = row["inherited_ctx"]
    ctx = json.loads(ctx_raw) if ctx_raw else {}
    return Node(
        node_id=row["node_id"],
        doc_id=row["doc_id"],
        kind=NodeKind(row["kind"]),
        seq=row["seq"],
        raw=row["raw"],
        byte_start=row["byte_start"],
        byte_end=row["byte_end"],
        normalized=row["normalized"],
        inherited=InheritedContext(
            section_path=ctx.get("section_path"),
            temporal_scope=ctx.get("temporal_scope"),
            subject=ctx.get("subject"),
            conditions=tuple(ctx.get("conditions") or ()),
        ),
        is_assertive=bool(row["is_assertive"]),
        status=DocStatus(row["status"]),
        layer1_version=row["layer1_version"],
        model_id=row["model_id"],
    )


def _row_to_edge(row: sqlite3.Row) -> Edge:
    return Edge(
        edge_id=row["edge_id"],
        src=row["src"],
        dst=row["dst"],
        type=EdgeType(row["type"]),
        provenance=Provenance(row["provenance"]),
        confidence=row["confidence"],
        cross_doc=bool(row["cross_doc"]),
        evidence_span=row["evidence_span"],
        model_id=row["model_id"],
        prompt_hash=row["prompt_hash"],
    )


class BundleGraph:
    """`dge.traversal.graph.Graph` backed by a bundle file.

    Nodes are cached on first read (a bundle is immutable once written), but
    edge lookups go to SQL every time so the `idx_edges_src` / `idx_edges_dst`
    indexes do the work they exist for. `incoming()` is the reverse index and
    is what makes the soundness guarantee a lookup rather than a scan
    (CLAUDE.md invariant 7).

    Use as a context manager, or call `close()`.
    """

    __slots__ = ("_conn", "_node_cache")

    def __init__(self, path: Path) -> None:
        if not Path(path).is_file():
            raise FileNotFoundError(f"no bundle at {path}")
        self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        self._node_cache: dict[str, Node | None] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- Graph protocol ---------------------------------------------------

    def get_node(self, node_id: str) -> Node | None:
        if node_id not in self._node_cache:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            self._node_cache[node_id] = _row_to_node(row) if row else None
        return self._node_cache[node_id]

    def outgoing(self, node_id: str) -> Sequence[Edge]:
        rows = self._conn.execute("SELECT * FROM edges WHERE src = ?", (node_id,)).fetchall()
        return [_row_to_edge(r) for r in rows]

    def incoming(self, node_id: str) -> Sequence[Edge]:
        rows = self._conn.execute("SELECT * FROM edges WHERE dst = ?", (node_id,)).fetchall()
        return [_row_to_edge(r) for r in rows]

    def degree(self, node_id: str) -> int:
        row = self._conn.execute(
            "SELECT (SELECT COUNT(*) FROM edges WHERE src = ?) + "
            "       (SELECT COUNT(*) FROM edges WHERE dst = ?) AS d",
            (node_id, node_id),
        ).fetchone()
        return int(row["d"])

    # -- beyond the Protocol ----------------------------------------------

    def all_nodes(self) -> list[Node]:
        rows = self._conn.execute(
            "SELECT * FROM nodes ORDER BY doc_id, seq"
        ).fetchall()
        return [_row_to_node(r) for r in rows]

    def nodes_in_doc_order(self, doc_id: str) -> list[Node]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE doc_id = ? ORDER BY seq", (doc_id,)
        ).fetchall()
        return [_row_to_node(r) for r in rows]

    def get_section(self, node_id: str) -> list[Node]:
        """Every node sharing the anchor node's section_path, in document
        order. `get_node` returns one unit; this returns the provision it
        sits in."""
        anchor = self.get_node(node_id)
        if anchor is None:
            return []
        path = anchor.inherited.section_path
        if not path:
            return [anchor]
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE doc_id = ? AND section_path = ? ORDER BY seq",
            (anchor.doc_id, path),
        ).fetchall()
        return [_row_to_node(r) for r in rows]

    def glossary(self) -> dict[str, str]:
        """surface_form -> gloss, for inline splicing at assembly."""
        rows = self._conn.execute(
            "SELECT surface_form, gloss FROM terms WHERE gloss IS NOT NULL"
        ).fetchall()
        return {r["surface_form"]: r["gloss"] for r in rows}

    def terms(self) -> list[Term]:
        rows = self._conn.execute("SELECT * FROM terms").fetchall()
        return [
            Term(
                term_id=r["term_id"],
                surface_form=r["surface_form"],
                provenance=Provenance(r["provenance"]),
                canonical=r["canonical"],
                scope_node_id=r["scope_node_id"],
                definition_node_id=r["definition_node_id"],
                definition_kind=r["definition_kind"],
                gloss=r["gloss"],
                variants=tuple(json.loads(r["variants"]) if r["variants"] else ()),
                confidence=r["confidence"],
            )
            for r in rows
        ]

    def documents(self) -> list[Document]:
        rows = self._conn.execute("SELECT * FROM documents ORDER BY doc_id").fetchall()
        return [
            Document(
                doc_id=r["doc_id"],
                substrate_hash=r["substrate_hash"],
                source_uri=r["source_uri"],
                doc_class=r["doc_class"],
                tenant_id=r["tenant_id"],
                ingested_at=r["ingested_at"],
                effective_date=r["effective_date"],
                status=DocStatus(r["status"]),
                superseded_by=r["superseded_by"],
                parse_confidence=r["parse_confidence"],
                review_state=r["review_state"],
                acl_tag=r["acl_tag"],
            )
            for r in rows
        ]

    def manifest(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM manifest").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def vector_model_ids(self) -> list[str]:
        """Distinct `model_id` values present in node_vectors, most-recent
        first. A bundle can carry vectors from more than one embedder (e.g.
        after switching providers without re-embedding) since L2 rows are
        keyed `(node_id, model_id)` conceptually — `INSERT OR REPLACE` only
        overwrites same-node-same-model rows."""
        rows = self._conn.execute(
            "SELECT model_id, MAX(computed_at) mc FROM node_vectors "
            "GROUP BY model_id ORDER BY mc DESC"
        ).fetchall()
        return [r["model_id"] for r in rows]

    def has_vectors(self, model_id: str | None = None) -> bool:
        if model_id:
            row = self._conn.execute(
                "SELECT 1 FROM node_vectors WHERE model_id = ? LIMIT 1", (model_id,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT 1 FROM node_vectors LIMIT 1").fetchone()
        return row is not None

    def search_vectors(
        self, query_vector: Sequence[float], *, model_id: str, top_k: int = 10
    ) -> list[tuple[str, float]]:
        """Brute-force cosine similarity over stored vectors for one model_id.

        Deliberately not an ANN index — see sql/schema.sql node_vectors for
        why. Returns (node_id, similarity) sorted best first.
        """
        rows = self._conn.execute(
            "SELECT node_id, vector_base64 FROM node_vectors WHERE model_id = ?",
            (model_id,),
        ).fetchall()
        scored = [
            (r["node_id"], _cosine(query_vector, _unpack_vector(r["vector_base64"])))
            for r in rows
        ]
        scored.sort(key=lambda t: -t[1])
        return scored[:top_k]


def open_bundle(path: Path) -> BundleGraph:
    """Open a bundle read-only. See `BundleGraph`."""
    return BundleGraph(Path(path))


# ---------------------------------------------------------------------------
# Vectors (L2) — disposable, written and rebuilt independently of everything
# above. See sql/schema.sql node_vectors for why they are base64-packed
# float32 in a TEXT column rather than a native vector/BLOB type.
# ---------------------------------------------------------------------------


def _pack_vector(values: Sequence[float]) -> str:
    return base64.b64encode(array.array("f", values).tobytes()).decode("ascii")


def _unpack_vector(encoded: str) -> array.array[float]:
    a = array.array("f")
    a.frombytes(base64.b64decode(encoded))
    return a


def write_node_vectors(
    path: Path,
    *,
    model_id: str,
    dim: int,
    vectors: dict[str, Sequence[float]],
) -> None:
    """Insert or replace vectors into an EXISTING bundle.

    Unlike `write_bundle`, this opens the file read-write in place rather than
    rewriting it — embeddings are computed after the substrate/edges are
    already committed, and L2 is disposable (CLAUDE.md architecture table): a
    new embedder overwrites old rows for the same node_id keyed on `model_id`,
    it never forces re-running L0/L1/L3.
    """
    now = _now()
    conn = sqlite3.connect(path)
    try:
        with conn:
            conn.executemany(
                """INSERT OR REPLACE INTO node_vectors
                   (node_id, model_id, dim, vector_base64, computed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (node_id, model_id, dim, _pack_vector(vec), now)
                    for node_id, vec in vectors.items()
                ],
            )
    finally:
        conn.close()


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
