"""Pydantic structured output for L3, with a genuinely closed enum.

Requires the `llm` extra (`pip install 'dge[llm]'`). Nothing on the offline
path imports this module — `dge.l3.run` and `dge.l3.prompt` are stdlib-only, so
the deterministic pipeline still runs with no optional dependency installed.

Why the schema is built rather than declared
--------------------------------------------
The type field and the reference fields must be closed to *this call's* valid
values: the edge-type list is fixed, but the legal set of `src_ref`/`dst_ref`
labels depends on how many units this particular window contains. A static
model cannot express that, so `response_json_schema` injects both enums into
the emitted JSON Schema. A provider honouring `strict` structured output then
cannot return a label that does not exist — which removes an entire class of
fabricated endpoint before it reaches validation.

That is belt AND braces on purpose. `dge.l3.run` re-checks every field of every
candidate against the window regardless of what the schema said, because a
provider that silently ignores `strict`, or falls back to plain JSON mode, must
not be able to widen what gets stored. The schema is an optimisation; the code
in `run` is the enforcement.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dge.l3.prompt import ALLOWED_EDGE_TYPES


class ExtractedEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src_ref: str = Field(description="Label of the source unit, e.g. 'N2'")
    dst_ref: str = Field(description="Label of the target unit, e.g. 'N1'")
    type: str = Field(description="One of the allowed relation types")
    evidence_span: str = Field(
        description="Text copied character-for-character from the window shown"
    )
    confidence: float = Field(
        default=0.5, description="0-1 estimate that this relation holds as stated"
    )


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edges: list[ExtractedEdge] = Field(default_factory=list)


def response_json_schema(ref_labels: Sequence[str]) -> dict[str, Any]:
    """JSON Schema for one call, with the type and reference enums closed."""
    schema = ExtractionResponse.model_json_schema()
    edge_props = schema["$defs"]["ExtractedEdge"]["properties"]
    edge_props["type"]["enum"] = list(ALLOWED_EDGE_TYPES)
    if ref_labels:
        edge_props["src_ref"]["enum"] = list(ref_labels)
        edge_props["dst_ref"]["enum"] = list(ref_labels)
    edge_props["confidence"]["minimum"] = 0.0
    edge_props["confidence"]["maximum"] = 1.0
    return schema
