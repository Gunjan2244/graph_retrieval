"""Local L2 embedder: fastembed (ONNX runtime, no torch).

Implements `dge.interfaces.Embedder`. Runs offline with no API key and no rate
limit, so it never blocks on account setup — the default adapter for
`dge embed`. `embed_hosted.py` implements the same Protocol against Voyage AI
for higher-quality contextual embeddings; callers choose between them by which
adapter they construct, never by a branch in core logic.

CLAUDE.md's stack table names BGE-M3 as the settled self-host choice.
fastembed's model zoo does not ship BGE-M3 (checked directly against the
installed version's `TextEmbedding.list_supported_models()` — it is not
there), so this is a forced substitution, not a preference: `bge-large-en-v1.5`
is the closest available model in the same BGE lineage. It is English-only and
capped at 512 tokens where BGE-M3 is multilingual with an 8192-token window, so
this is a real fidelity gap, not a cosmetic one — flagged here rather than
silently drifting from what CLAUDE.md settled. True BGE-M3 is available via
`FlagEmbedding` (requires torch), which is a legitimate second adapter to add
if that gap matters more than staying torch-free.

`fastembed` is imported only in this module (CLAUDE.md code convention: no
vendor SDK outside its adapter). Install with `pip install dge[embed]`.
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_MODEL = "BAAI/bge-large-en-v1.5"  # closest fastembed has to BGE-M3; see module docstring


class FastEmbedEmbedder:
    """`dge.interfaces.Embedder` backed by fastembed.

    Late-chunking / contextual embedding (the Protocol's stated preference) is
    not what this model does — it embeds each text independently. `doc_context`
    is accepted for Protocol conformance and prepended as a light-weight
    substitute (a real contextual embedder is a Stage C follow-up, not a
    correctness requirement for Stage B's traversal to be testable end to end).
    """

    #: Not contextual (see class docstring), so chunking within a document
    #: changes nothing about the vectors — and it must be capped: one call with
    #: a whole large document drove ONNX Runtime's allocation arena to ~3.9GB
    #: and was OOM-killed on the corpus's largest act (615 nodes) while the
    #: same model handled 375-node documents fine. See `pipeline.embed_bundle`.
    max_batch = 32

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # local import: keep this module's
        # top-level clean of the vendor SDK for anyone who only imports the
        # module to read DEFAULT_MODEL / type-check against it without the
        # dependency installed.

        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        self.model_id = f"fastembed:{model_name}"
        info = next(
            (m for m in TextEmbedding.list_supported_models() if m["model"] == model_name),
            None,
        )
        self.dim = int(info["dim"]) if info else 384

    def embed_documents(
        self, texts: Sequence[str], doc_context: str | None = None
    ) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        inputs = [f"{doc_context}\n\n{t}" if doc_context else t for t in texts]
        return [vec.tolist() for vec in self._model.embed(inputs)]

    def embed_query(self, text: str) -> Sequence[float]:
        vec: list[float] = next(iter(self._model.embed([text]))).tolist()
        return vec
