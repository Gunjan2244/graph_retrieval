"""Assembly: turn a collected node set into text a model can reason over.

docs/04 §4.6. Three rules, each of which changes the answer:

  1. Sort by SOURCE DOCUMENT POSITION, never by retrieval rank. The same two
     facts in similarity order read to a model as a contradiction; in document
     order they reconstruct the logic.
  2. Splice term glosses inline rather than appending definition blocks —
     `Territory [= the countries listed in Schedule B]` costs fewer tokens and
     reads better than a wall of appended definitions.
  3. Label superseded nodes explicitly rather than dropping them. Savings
     clauses preserve prior operation (docs/07), so "repealed" is not the same
     as "irrelevant".
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dge.model import Node
from dge.traversal.expand import Arrival, estimate_tokens


@dataclass(frozen=True, slots=True)
class AssembledContext:
    text: str
    node_ids: tuple[str, ...]
    tokens: int

    def __str__(self) -> str:
        return self.text


def _gloss_pattern(surface: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(surface)}\b")


def splice_glosses(
    text: str,
    glosses: Mapping[str, str],
    *,
    already_spliced: set[str] | None = None,
) -> str:
    """Inline `term [= gloss]` on first occurrence only.

    First occurrence only, and once per assembled context rather than once per
    node: repeating a gloss every time a ubiquitous defined term appears is how
    a term symbol table turns into token bloat.
    """
    seen = already_spliced if already_spliced is not None else set()
    for surface, gloss in sorted(glosses.items(), key=lambda kv: -len(kv[0])):
        if surface in seen or not gloss:
            continue
        pattern = _gloss_pattern(surface)
        if pattern.search(text):
            text = pattern.sub(f"{surface} [= {gloss}]", text, count=1)
            seen.add(surface)
    return text


def assemble(
    nodes: Sequence[Node],
    *,
    glosses: Mapping[str, str] | None = None,
    arrivals: Mapping[str, Arrival] | None = None,
    gloss_from_hop: int = 0,
    separator: str = "\n\n",
) -> AssembledContext:
    """Assemble `nodes` into document-ordered context text.

    `gloss_from_hop` mirrors the `defines` policy in docs/04 §4.4: a definition
    reached at hop 1 is worth its full span, deeper ones only their gloss.
    Callers that already included full definition spans pass a higher value so
    the same definition is not both spliced and quoted.
    """
    ordered = sorted(nodes, key=lambda n: (n.doc_id, n.seq))
    spliced: set[str] = set()
    parts: list[str] = []

    for node in ordered:
        body = node.for_assembly()
        if glosses:
            hops = arrivals[node.node_id].hops if arrivals and node.node_id in arrivals else 0
            if hops >= gloss_from_hop:
                body = splice_glosses(body, glosses, already_spliced=spliced)
        parts.append(body)

    text = separator.join(parts)
    return AssembledContext(
        text=text,
        node_ids=tuple(n.node_id for n in ordered),
        tokens=estimate_tokens(text),
    )
