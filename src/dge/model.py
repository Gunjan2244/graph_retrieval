"""Core domain types.

These mirror sql/schema.sql. Keep them in sync; the schema is authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NodeKind(StrEnum):
    PROPOSITION = "proposition"
    TERM = "term"
    STRUCTURAL = "structural"
    FOOTNOTE = "footnote"  # editorial/amendment apparatus, not a provision — never a closure-edge target


class EdgeClass(StrEnum):
    """The central distinction of the whole design.

    CLOSURE  — omitting one makes the answer WRONG. Traversed to a fixed point,
               unbudgeted, on the reverse index.
    CONTEXT  — omitting one makes the answer THINNER. Budgeted, greedy, cuttable.
    """

    CLOSURE = "closure"
    CONTEXT = "context"


class EdgeType(StrEnum):
    # closure
    EXCEPTION_OF = "exception_of"
    SUPERSEDES = "supersedes"
    AMENDS = "amends"
    CONDITIONED_ON = "conditioned_on"
    DEFINES = "defines"
    # context
    ELABORATES = "elaborates"
    EXEMPLIFIES = "exemplifies"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CITES = "cites"
    SIMILAR_TO = "similar_to"
    PART_OF = "part_of"
    # term-to-term
    ABBREVIATION_OF = "abbreviation_of"
    VARIANT_OF = "variant_of"
    SYNONYM_OF = "synonym_of"
    IS_A = "is_a"
    # weak identity — surfaced to the model, never silently merged
    POSSIBLY_SAME_AS = "possibly_same_as"


EDGE_CLASS: dict[EdgeType, EdgeClass] = {
    EdgeType.EXCEPTION_OF: EdgeClass.CLOSURE,
    EdgeType.SUPERSEDES: EdgeClass.CLOSURE,
    EdgeType.AMENDS: EdgeClass.CLOSURE,
    EdgeType.CONDITIONED_ON: EdgeClass.CLOSURE,
    EdgeType.DEFINES: EdgeClass.CLOSURE,
}


def edge_class(t: EdgeType) -> EdgeClass:
    return EDGE_CLASS.get(t, EdgeClass.CONTEXT)


class Provenance(StrEnum):
    STRUCTURAL = "structural"   # deterministic, from document structure
    PATTERN = "pattern"         # regex / lexical marker
    MODEL = "model"             # LLM extracted
    VERIFIED = "verified"       # LLM extracted then pairwise verified
    SIMILARITY = "similarity"   # last resort; NEVER a typed relation on its own


class DocStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    DRAFT = "draft"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InheritedContext:
    """What the node needs to stand alone once removed from its document."""

    section_path: str | None = None
    temporal_scope: str | None = None
    subject: str | None = None
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Node:
    node_id: str
    doc_id: str
    kind: NodeKind
    seq: int
    raw: str                      # written once, never updated
    byte_start: int | None = None
    byte_end: int | None = None
    normalized: str | None = None  # L1 restatement; never replaces `raw`
    inherited: InheritedContext = field(default_factory=InheritedContext)
    is_assertive: bool = True
    status: DocStatus = DocStatus.CURRENT
    layer1_version: str | None = None   # None until L1 has run
    model_id: str | None = None         # model that produced `normalized`, if any

    def for_assembly(self) -> str:
        """Text as it should appear in assembled context."""
        prefix = f"[{self.inherited.section_path}] " if self.inherited.section_path else ""
        body = self.normalized or self.raw
        if self.status is DocStatus.SUPERSEDED:
            prefix = f"[SUPERSEDED] {prefix}"
        return prefix + body


@dataclass(frozen=True, slots=True)
class Edge:
    edge_id: str
    src: str
    dst: str
    type: EdgeType
    provenance: Provenance
    confidence: float = 1.0
    cross_doc: bool = False
    evidence_span: str | None = None
    model_id: str | None = None
    prompt_hash: str | None = None

    @property
    def cls(self) -> EdgeClass:
        return edge_class(self.type)


@dataclass(frozen=True, slots=True)
class Document:
    doc_id: str
    substrate_hash: str
    source_uri: str | None
    doc_class: str | None
    tenant_id: str
    ingested_at: str
    effective_date: str | None = None
    status: DocStatus = DocStatus.UNKNOWN
    superseded_by: str | None = None
    parse_confidence: float | None = None
    review_state: str = "none"          # none | pending | cleared
    acl_tag: str | None = None


@dataclass(frozen=True, slots=True)
class Term:
    term_id: str
    surface_form: str
    provenance: Provenance
    canonical: str | None = None
    scope_node_id: str | None = None      # lexical scope of this binding
    definition_node_id: str | None = None
    definition_kind: str | None = None    # 'means' (exhaustive) | 'includes' (illustrative)
    gloss: str | None = None              # one sentence; used at hop 2+
    variants: tuple[str, ...] = ()
    confidence: float = 1.0
