"""Cutting a document into L3 call units, and the cost gate over them.

Two rules from the design, implemented here rather than in the adapter so that
they hold whatever model is wired in:

**One section per call** (docs/05 5.3). Not one document, and not one node. One
node has no context — a proviso alone cannot say what it modifies. One document
invites the model to relate anything to anything and, worse, makes invariant 10
toothless: with the whole act in the window, a span quoted from a completely
unrelated section is "verbatim" and passes. The section window is what makes
the evidence check bite.

**The gate is consulted before every call** (BUILD_PLAN Phase 3, docs/05 5.4).
`pack.should_run_l3` is a pack-declared lexical test — the engine never learns
what the terms mean, it only asks. L3 is the dominant cost line, so a section
with no closure marker anywhere in it does not get a call at all.

Section boundaries come from the substrate, not from a chunk size: a new
section opens at every `NodeKind.STRUCTURAL` node the parser emitted, and every
proposition after it belongs to that section until the next one. That is why
the parser rewrite in PARSER_PLAN.md had to land first — on the old substrate,
whole acts collapsed into single structural blobs and this would have produced
one gigantic window per document.

`FOOTNOTE` nodes are excluded from windows entirely, for the same reason
`dge.edges` excludes them from the sibling chain: amendment apparatus is not
operative text, and putting it in the window invites edges into it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from dge.domains.legal import DomainPack
from dge.model import Node, NodeKind

# A window larger than this is split. Not a token budget — a judgement budget:
# past a few thousand characters a model stops relating the whole window and
# starts relating whatever is nearest the end.
DEFAULT_MAX_WINDOW_CHARS = 6000


@dataclass(frozen=True, slots=True)
class Section:
    """One L3 call unit."""

    doc_id: str
    key: str                    # stable id for this window; part of the edge_id
    path: str                   # human-readable section path, sent to the model
    nodes: tuple[Node, ...]
    part: int = 0               # >0 when one section was split across calls
    parts: int = 1

    @property
    def evidence_window(self) -> str:
        """The text an `evidence_span` must appear verbatim in.

        Deliberately the raw substrate text of the window's nodes and NOTHING
        else — no `[N1]` reference labels, no section-path header, none of the
        scaffolding `dge.l3.prompt` wraps around it. That is stricter than
        invariant 10 requires (the scaffolding IS in the model's input window),
        and the extra strictness is the point: an evidence span that clears
        this check is a slice of a real document, so it stays checkable against
        the immutable original bytes (invariant 1) long after the prompt that
        produced it is gone.
        """
        return "\n".join(n.raw for n in self.nodes)

    def title(self) -> str:
        for n in self.nodes:
            if n.kind is NodeKind.STRUCTURAL:
                return re.sub(r"\s+", " ", n.raw).strip()[:120]
        return self.path or self.doc_id


def _windowable(nodes: Sequence[Node]) -> Iterator[Node]:
    for n in nodes:
        if n.kind is NodeKind.FOOTNOTE:
            continue
        yield n


def _split_oversized(
    doc_id: str, key: str, path: str, nodes: list[Node], max_chars: int
) -> list[Section]:
    if sum(len(n.raw) for n in nodes) <= max_chars:
        return [Section(doc_id, key, path, tuple(nodes))]

    chunks: list[list[Node]] = [[]]
    size = 0
    for n in nodes:
        if chunks[-1] and size + len(n.raw) > max_chars:
            chunks.append([])
            size = 0
        chunks[-1].append(n)
        size += len(n.raw)

    return [
        Section(doc_id, f"{key}/p{i}", path, tuple(chunk), part=i, parts=len(chunks))
        for i, chunk in enumerate(chunks)
    ]


def group_sections(
    nodes: Sequence[Node], *, max_chars: int = DEFAULT_MAX_WINDOW_CHARS
) -> list[Section]:
    """Split nodes (already in document order) into call units.

    Sections are contiguous in `seq` by construction, so a document with two
    identically-titled sections produces two windows rather than one merged
    one — an act really can repeat a heading, and merging them would put text
    from one in the evidence window of the other.
    """
    sections: list[Section] = []
    current: list[Node] = []
    current_path = ""
    current_doc = ""
    index = 0

    def flush() -> None:
        nonlocal current, index
        if current:
            sections.extend(
                _split_oversized(
                    current_doc, f"{current_doc}#s{index}", current_path, current, max_chars
                )
            )
            index += 1
        current = []

    for node in _windowable(nodes):
        starts_section = node.kind is NodeKind.STRUCTURAL
        if node.doc_id != current_doc:
            flush()
            current_doc, index = node.doc_id, 0
        elif starts_section:
            flush()
        if not current:
            current_path = node.inherited.section_path or ""
        current.append(node)
    flush()
    return sections


@dataclass(frozen=True, slots=True)
class GateReport:
    """What the cost gate did. L3 is the dominant cost line, so this is not
    debug output — it is the number that decides whether ingest is affordable."""

    sections_total: int
    sections_admitted: int
    chars_total: int
    chars_admitted: int

    @property
    def admit_fraction(self) -> float:
        return self.sections_admitted / self.sections_total if self.sections_total else 0.0

    @property
    def char_fraction(self) -> float:
        return self.chars_admitted / self.chars_total if self.chars_total else 0.0


def apply_gate(
    sections: Sequence[Section], pack: DomainPack
) -> tuple[list[Section], GateReport]:
    """Partition sections into those worth a model call and those that are not.

    The gate is `pack.should_run_l3` and nothing else — no engine-side heuristic
    is layered on top, because that is precisely the leak CLAUDE.md invariant 11
    forbids. If the legal pack admits too much, the fix is the pack's gate
    terms, not a filter here.
    """
    admitted: list[Section] = []
    chars_total = chars_admitted = 0
    for section in sections:
        window = section.evidence_window
        chars_total += len(window)
        if pack.should_run_l3(window):
            admitted.append(section)
            chars_admitted += len(window)
    return admitted, GateReport(len(sections), len(admitted), chars_total, chars_admitted)
