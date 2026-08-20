"""L2 vector storage and retrieval, against a fake embedder.

Deliberately does not import `fastembed` or hit a network API: a fast,
deterministic stand-in that conforms to `dge.interfaces.Embedder` is enough to
test the storage/search plumbing (base64 packing, cosine search, per-document
grouping in `embed_bundle`). Testing the real adapters — that fastembed loads
a model, that Voyage's endpoint contract holds — is done manually/in CI with
network access, not in the default fast test run.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from dge.bundle import open_bundle, write_node_vectors
from dge.pipeline import embed_bundle, ingest_documents

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample_act.txt"
DIM = 8


class FakeEmbedder:
    """Deterministic, content-hash-based. Same text -> same vector, always —
    which is what makes the round-trip and 'nearest neighbour is itself'
    tests below meaningful without a real model."""

    model_id = "fake:hash-v1"
    dim = DIM

    def _vec(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in h[:DIM]]

    def embed_documents(
        self, texts: Sequence[str], doc_context: str | None = None
    ) -> Sequence[Sequence[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> Sequence[float]:
        return self._vec(text)


@pytest.fixture
def bundle(tmp_path) -> Path:
    out = tmp_path / "b.sqlite"
    ingest_documents([SAMPLE], domain="legal", out_path=out)
    return out


def test_vector_round_trips_through_base64_packing(bundle):
    with open_bundle(bundle) as g:
        node_id = g.all_nodes()[0].node_id
    original = [0.1, -0.2, 0.3, 0.0, 1.0, -1.0, 0.5, -0.5]
    write_node_vectors(bundle, model_id="fake:test", dim=8, vectors={node_id: original})

    with open_bundle(bundle) as g:
        results = g.search_vectors(original, model_id="fake:test", top_k=1)
    assert results[0][0] == node_id
    assert results[0][1] == pytest.approx(1.0, abs=1e-5), "a vector must be its own nearest match"


def test_search_vectors_ranks_by_cosine_similarity(bundle):
    with open_bundle(bundle) as g:
        ids = [n.node_id for n in g.all_nodes()[:3]]
    vectors = {
        ids[0]: [1.0, 0.0, 0.0, 0, 0, 0, 0, 0],
        ids[1]: [0.9, 0.1, 0.0, 0, 0, 0, 0, 0],   # close to ids[0]
        ids[2]: [0.0, 0.0, 1.0, 0, 0, 0, 0, 0],   # orthogonal
    }
    write_node_vectors(bundle, model_id="fake:test", dim=8, vectors=vectors)

    with open_bundle(bundle) as g:
        results = g.search_vectors([1.0, 0.0, 0.0, 0, 0, 0, 0, 0], model_id="fake:test", top_k=3)
    ranked = [nid for nid, _sim in results]
    assert ranked == [ids[0], ids[1], ids[2]]


def test_embed_bundle_writes_one_vector_per_node(bundle):
    summary = embed_bundle(bundle, FakeEmbedder())
    assert summary.model_id == "fake:hash-v1"
    with open_bundle(bundle) as g:
        nodes = g.all_nodes()
        assert summary.nodes_embedded == len(nodes)
        assert g.has_vectors("fake:hash-v1")
        for n in nodes:
            text = n.normalized or n.raw  # embed_bundle embeds this, not always n.raw
            hits = g.search_vectors(FakeEmbedder()._vec(text), model_id="fake:hash-v1", top_k=1)
            assert hits[0][0] == n.node_id


def test_embed_bundle_skips_review_pending_documents(tmp_path):
    corrupt = tmp_path / "corrupt.txt"
    corrupt.write_text("\n\n\n   \n\n")  # near-empty -> low parse confidence
    out = tmp_path / "b.sqlite"
    ingest_documents([corrupt], domain="legal", out_path=out)

    with open_bundle(out) as g:
        pending = [d for d in g.documents() if d.review_state == "pending"]
    if not pending:
        pytest.skip("fixture text did not trigger the review gate on this parser version")

    summary = embed_bundle(out, FakeEmbedder())
    assert any("review-pending" in w for w in summary.warnings)


def test_vector_model_ids_reflects_what_was_written(bundle):
    with open_bundle(bundle) as g:
        assert g.vector_model_ids() == []
    embed_bundle(bundle, FakeEmbedder())
    with open_bundle(bundle) as g:
        assert g.vector_model_ids() == ["fake:hash-v1"]
