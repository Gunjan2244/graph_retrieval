"""Hosted L2 embedder: Voyage AI's contextualized embeddings API.

CLAUDE.md's stack table names this the settled hosted choice
(`voyage-context-4 (hosted) / BGE-M3 (self-host)`) — not a generic "any hosted
embedder", specifically Voyage's *contextualized* embedding family. That is not
a naming detail: `dge.interfaces.Embedder` explicitly prefers "a model that
encodes each unit with its document context (late chunking / contextual
embeddings) over isolated-chunk encoding", and voyage-context-3/4 is a model
built around exactly that mechanism — you submit every chunk of one document
together and it returns per-chunk vectors that are aware of their siblings,
not each chunk encoded in isolation and hoping cosine similarity recovers what
was lost.

Endpoint contract (verified against Voyage's docs, since guessing an API
shape wrong ships silently-broken code):

    POST https://api.voyageai.com/v1/contextualizedembeddings
    body: {"inputs": [[chunk, chunk, ...]], "model": ..., "input_type": ...}
    resp: {"data": [{"data": [{"embedding": [...], "index": 0}, ...]}]}

`inputs` is a list of documents, each a list of that document's chunks in
order — the grouping IS the mechanism, not metadata alongside it. `embed_bundle`
in `dge.pipeline` calls `embed_documents` once per source document for this
reason: passing unrelated chunks from different documents in one call would
contextualize them against each other, which is wrong.

Uses stdlib `urllib` only (CLAUDE.md code convention: no vendor SDK outside its
adapter module — an HTTP call via urllib satisfies that without adding a
dependency). Requires `VOYAGE_API_KEY` in the environment, checked lazily at
call time so importing this module for type-checking never requires a key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import cast

API_URL = "https://api.voyageai.com/v1/contextualizedembeddings"
DEFAULT_MODEL = "voyage-context-4"  # per CLAUDE.md stack table; not this module's call to change
DEFAULT_OUTPUT_DIM = 1024
_TIMEOUT_S = 60


class VoyageAPIError(RuntimeError):
    pass


def _require_api_key() -> str:
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise VoyageAPIError(
            "VOYAGE_API_KEY is not set. Get a free-tier key at "
            "https://dashboard.voyageai.com and export it before using "
            "VoyageEmbedder."
        )
    return key


def _post(payload: dict[str, object]) -> dict[str, object]:
    key = _require_api_key()
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            parsed: dict[str, object] = json.loads(resp.read().decode("utf-8"))
            return parsed
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise VoyageAPIError(f"Voyage API {e.code}: {body}") from e


def _extract_embeddings(resp: dict[str, object], doc_index: int) -> list[list[float]]:
    """Pull `data[doc_index].data[*].embedding` out of the untyped JSON
    response, in one place, so the two callers below don't each carry their
    own `# type: ignore` scattered through indexing chains."""
    data = cast(list[dict[str, object]], resp["data"])
    inner = cast(list[dict[str, object]], data[doc_index]["data"])
    return [cast(list[float], e["embedding"]) for e in inner]


class VoyageEmbedder:
    """`dge.interfaces.Embedder` backed by Voyage AI's contextualized API."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        output_dim: int = DEFAULT_OUTPUT_DIM,
    ) -> None:
        self.model_id = f"voyage:{model_name}"
        self._model_name = model_name
        self.dim = output_dim

    def embed_documents(
        self, texts: Sequence[str], doc_context: str | None = None
    ) -> Sequence[Sequence[float]]:
        """All of `texts` must be chunks from ONE document, in order — see the
        module docstring. `doc_context` (if given) is sent as a leading
        synthetic chunk so the real chunks are contextualized against it, then
        its own embedding is discarded before returning."""
        if not texts:
            return []
        chunks = ([doc_context] + list(texts)) if doc_context else list(texts)
        resp = _post({
            "inputs": [chunks],
            "model": self._model_name,
            "input_type": "document",
            "output_dimension": self.dim,
        })
        vectors = _extract_embeddings(resp, doc_index=0)
        return vectors[1:] if doc_context else vectors

    def embed_query(self, text: str) -> Sequence[float]:
        resp = _post({
            "inputs": [[text]],
            "model": self._model_name,
            "input_type": "query",
            "output_dimension": self.dim,
        })
        return _extract_embeddings(resp, doc_index=0)[0]
