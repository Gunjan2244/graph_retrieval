import sqlite3
from pathlib import Path

from dge.pipeline import ingest_documents


def test_ingest_writes_a_queryable_bundle(tmp_path: Path):
    src = Path(__file__).resolve().parents[1] / "samples" / "sample_act.txt"
    out = tmp_path / "bundle.sqlite"

    summary = ingest_documents([src], domain="legal", tenant_id="acme", out_path=out)

    assert out.exists()
    assert summary.documents == 1
    assert summary.documents_review_pending == 0
    assert summary.nodes > 0
    assert summary.edges > 0
    assert summary.closure_edges > 0  # the sample act has provisos/definitions

    conn = sqlite3.connect(out)
    try:
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        assert doc_count == 1
        assert node_count == summary.nodes
        assert edge_count == summary.edges

        # Original bytes are preserved untouched (CLAUDE.md invariant 1).
        import base64
        blob = conn.execute("SELECT raw_base64 FROM document_blobs").fetchone()[0]
        assert base64.b64decode(blob) == src.read_bytes()

        # Reverse index usability: every closure edge's dst is a real node
        # (the soundness guarantee depends on this index, not just on it
        # existing — CLAUDE.md invariant 7).
        node_ids = {r[0] for r in conn.execute("SELECT node_id FROM nodes")}
        for src_id, dst_id in conn.execute(
            "SELECT src, dst FROM edges WHERE class = 'closure'"
        ):
            assert src_id in node_ids
            assert dst_id in node_ids

        # Manifest reflects the same counts a reader would use to sanity-check
        # the bundle before trusting it.
        manifest = dict(conn.execute("SELECT key, value FROM manifest"))
        assert manifest["node_count"] == str(node_count)
        assert manifest["edge_count"] == str(edge_count)
    finally:
        conn.close()


def test_ingest_is_idempotent_on_identical_bytes(tmp_path: Path):
    src = Path(__file__).resolve().parents[1] / "samples" / "sample_act.txt"
    out1 = tmp_path / "bundle1.sqlite"
    out2 = tmp_path / "bundle2.sqlite"

    s1 = ingest_documents([src], out_path=out1)
    s2 = ingest_documents([src], out_path=out2)

    assert s1.nodes == s2.nodes
    assert s1.edges == s2.edges

    conn1, conn2 = sqlite3.connect(out1), sqlite3.connect(out2)
    try:
        ids1 = {r[0] for r in conn1.execute("SELECT node_id FROM nodes")}
        ids2 = {r[0] for r in conn2.execute("SELECT node_id FROM nodes")}
        assert ids1 == ids2  # same content -> same doc_id -> same node_ids
    finally:
        conn1.close()
        conn2.close()


def test_multiple_documents_in_one_run_share_one_bundle(tmp_path: Path):
    doc_a = tmp_path / "a.txt"
    doc_b = tmp_path / "b.txt"
    doc_a.write_text("Section 1. A.\n\n(1) A applies.")
    doc_b.write_text("Section 1. B.\n\n(1) B applies.")
    out = tmp_path / "bundle.sqlite"

    summary = ingest_documents([doc_a, doc_b], out_path=out)

    assert summary.documents == 2
    conn = sqlite3.connect(out)
    try:
        doc_ids = [r[0] for r in conn.execute("SELECT doc_id FROM documents")]
        assert len(set(doc_ids)) == 2
        for doc_id in doc_ids:
            n = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE doc_id = ?", (doc_id,)
            ).fetchone()[0]
            assert n > 0
    finally:
        conn.close()


def test_low_confidence_parse_halts_that_document_for_review(tmp_path: Path):
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    good = tmp_path / "good.txt"
    good.write_text("Section 1. Rule.\n\n(1) Applies.")
    out = tmp_path / "bundle.sqlite"

    summary = ingest_documents([empty, good], out_path=out)

    assert summary.documents == 2
    assert summary.documents_review_pending == 1

    conn = sqlite3.connect(out)
    try:
        rows = dict(conn.execute("SELECT doc_id, review_state FROM documents"))
        pending = [doc_id for doc_id, state in rows.items() if state == "pending"]
        assert len(pending) == 1
        # The gated document contributes no nodes — the pipeline halts for
        # that document rather than proceeding on a corrupt/empty substrate.
        n = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE doc_id = ?", (pending[0],)
        ).fetchone()[0]
        assert n == 0
    finally:
        conn.close()
