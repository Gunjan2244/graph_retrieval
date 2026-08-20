"""Protocols for external dependencies.

Rule: no vendor SDK is imported outside its adapter module. Core logic depends
only on these. Layer 2 in particular is disposable by design — the embedder is a
config value and will be swapped more than once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from dge.model import Edge, Node


@dataclass(frozen=True, slots=True)
class ParseResult:
    nodes: Sequence[Node]
    structural_edges: Sequence[Edge]
    confidence: float          # below threshold -> review queue, pipeline halts
    warnings: Sequence[str]


class Parser(Protocol):
    """L0. Docling adapter is the default implementation.

    Must emit byte offsets into the original document and associate table cells
    with their headers. A table parsed as prose poisons every layer above and no
    downstream cleverness recovers it.
    """

    def parse(self, raw: bytes, doc_class: str | None = None) -> ParseResult: ...


class Normalizer(Protocol):
    """L1. Small batched model. Resolves coref, produces a self-contained
    restatement, attaches inherited context, flags non-assertive nodes."""

    def normalize(self, nodes: Sequence[Node], doc_summary: str) -> Sequence[Node]: ...


class Embedder(Protocol):
    """L2. Prefer a model that encodes each unit with its document context
    (late chunking / contextual embeddings) over isolated-chunk encoding."""

    model_id: str
    dim: int

    def embed_documents(self, texts: Sequence[str], doc_context: str | None = None) -> Sequence[Sequence[float]]: ...
    def embed_query(self, text: str) -> Sequence[float]: ...


class Reranker(Protocol):
    """Cross-encoder. Usually the largest quality gain per unit of cost."""

    def rerank(self, query: str, candidates: Sequence[Node], top_k: int) -> Sequence[tuple[Node, float]]: ...


@dataclass(frozen=True, slots=True)
class EdgeCandidate:
    src: str
    dst: str
    type: str
    evidence_span: str
    confidence: float


class EdgeExtractor(Protocol):
    """L3. The quality bottleneck and the dominant cost line.

    Contract for implementations:
      - structured output, closed edge-type enum, explicit null option
      - one section per call, with section path + running document summary
      - every candidate carries an evidence_span

    The caller MUST discard any candidate whose evidence_span does not appear
    verbatim in the input window. Enforce in code, not in the prompt.
    """

    model_id: str
    prompt_hash: str

    def extract(self, section_nodes: Sequence[Node], section_path: str, doc_summary: str) -> Sequence[EdgeCandidate]: ...


class PairVerifier(Protocol):
    """Cross-document. Candidates come from retrieval; this decides whether a
    typed relation actually holds. Returns None for 'no relation'.

    Unverified similarity must never become a typed edge.
    """

    def verify(self, a: Node, b: Node) -> EdgeCandidate | None: ...
