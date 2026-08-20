"""Hosted L2 reranker: Voyage AI's rerank API.

Implements `dge.interfaces.Reranker`. Pairs with `embed_hosted.py`'s
`VoyageEmbedder` — same vendor already settled by CLAUDE.md's stack table for
the hosted embedding path, same "stdlib urllib only, no vendor SDK outside
this adapter" rule, same lazy `VOYAGE_API_KEY` check so importing this module
for type-checking never requires a key.

Endpoint contract (verified against Voyage's docs, since guessing an API
shape wrong ships silently-broken code — same discipline `embed_hosted.py`
follows):

    POST https://api.voyageai.com/v1/rerank
    body: {"query": ..., "documents": [...], "model": ..., "top_k": ...}
    resp: {"data": [{"index": int, "relevance_score": float}, ...], ...}

`data` is returned sorted by descending relevance; `index` refers back into
the `documents` list this call sent, which is how the response is mapped back
onto the original `Node` objects below.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import cast

from dge.model import Node

API_URL = "https://api.voyageai.com/v1/rerank"
DEFAULT_MODEL = "rerank-2.5"
_TIMEOUT_S = 60


class VoyageAPIError(RuntimeError):
    pass


def _require_api_key() -> str:
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise VoyageAPIError(
            "VOYAGE_API_KEY is not set. Get a free-tier key at "
            "https://dashboard.voyageai.com and export it before using "
            "VoyageReranker."
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


class VoyageReranker:
    """`dge.interfaces.Reranker` backed by Voyage AI's rerank API."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_id = f"voyage:{model_name}"
        self._model_name = model_name

    def rerank(
        self, query: str, candidates: Sequence[Node], top_k: int
    ) -> Sequence[tuple[Node, float]]:
        if not candidates:
            return []
        texts = [c.normalized or c.raw for c in candidates]
        resp = _post({
            "query": query,
            "documents": texts,
            "model": self._model_name,
            "top_k": top_k,
        })
        data = cast(list[dict[str, object]], resp["data"])
        return [
            (candidates[cast(int, row["index"])], cast(float, row["relevance_score"]))
            for row in data
        ]
