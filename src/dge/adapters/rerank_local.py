"""Local L2 reranker: fastembed's cross-encoder (ONNX runtime, no torch).

Implements `dge.interfaces.Reranker`. Runs offline with no API key and no rate
limit, mirroring `embed_local.py`'s `FastEmbedEmbedder` — same install story
(`pip install dge[embed]`), same "no vendor SDK outside its adapter module"
rule. `rerank_hosted.py` implements the same Protocol against Voyage AI's
rerank API; callers choose between them by which adapter they construct,
never by a branch in core logic.

CLAUDE.md's stack table names BGE-reranker-v2 as the settled self-host choice.
fastembed's cross-encoder model zoo does not ship a v2 BGE reranker (checked
directly against the installed version's
`TextCrossEncoder.list_supported_models()` — only `BAAI/bge-reranker-base` is
offered, not `bge-reranker-v2-m3`), so this is a forced substitution, not a
preference — the same shape of gap `embed_local.py` documents for BGE-M3: same
BGE lineage, but the older single-stage cross-encoder rather than the v2
line's stronger multilingual/long-context model. Flagged here rather than
silently drifting from what CLAUDE.md settled.

`fastembed` is imported only in this module (CLAUDE.md code convention: no
vendor SDK outside its adapter). Install with `pip install dge[embed]`.
"""

from __future__ import annotations

from collections.abc import Sequence

from dge.model import Node

DEFAULT_MODEL = "BAAI/bge-reranker-base"  # closest fastembed has to bge-reranker-v2; see module docstring


class FastEmbedReranker:
    """`dge.interfaces.Reranker` backed by fastembed's ONNX cross-encoder."""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # local import: keep
        # this module's top-level clean of the vendor SDK for anyone who only
        # imports the module to read DEFAULT_MODEL / type-check against it
        # without the dependency installed — matches embed_local.py.

        self._model = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)
        self.model_id = f"fastembed:{model_name}"

    def rerank(
        self, query: str, candidates: Sequence[Node], top_k: int
    ) -> Sequence[tuple[Node, float]]:
        if not candidates:
            return []
        # Rerank the normalized restatement when L1 has run, else raw — same
        # rule `LexicalIndex` follows (invariant 2: `raw` is never mutated, so
        # this reads a second column rather than losing information).
        texts = [c.normalized or c.raw for c in candidates]
        scores = list(self._model.rerank(query, texts))
        ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: -pair[1])
        return ranked[:top_k]
