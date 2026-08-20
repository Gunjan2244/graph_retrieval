"""L0 substrate: deterministic parse -> structure + byte offsets + confidence.

`PlainTextParser` is the default `Parser` implementation for `.txt` / `.md`
input. It never guesses: every node's `byte_start`/`byte_end` is a verifiable
slice of the original bytes, and confidence reflects what the parser actually
recovered (encoding damage, empty input) rather than a fixed constant.

Docling belongs behind this same protocol for PDFs/DOCX (see CLAUDE.md anti-
goals: we do not write a parser, we adapt one) — that adapter is not wired
here because the dependency isn't part of the offline core path. Swapping it
in is a new module implementing `Parser`, not a change to this one.

Node/doc identity: this parser does not know the final `doc_id` (that is a
content hash of the whole document, computed by the pipeline). It emits nodes
and edges keyed by a small relative id ("0", "1", ...) and the pipeline
rewrites them to `f"{doc_id}:{rel_id}"` in one pass. This keeps parsing a
pure function of the bytes.
"""

from __future__ import annotations

import re
from dataclasses import replace

from dge.interfaces import ParseResult
from dge.model import Edge, EdgeType, InheritedContext, Node, NodeKind, Provenance

# Headings this parser recognizes without any domain pack: numbered statutory
# sections/chapters/articles (both the "Section 12." style and the bare
# "12. Title" style most Indian bare acts actually use — e.g. IPC section
# headings read "186. Obstructing public servant...", never "Section 186."),
# and markdown ATX headings. Domain-specific sub-structure (provisos,
# explanations, illustrations, ...) is layered on top by `dge.edges` using the
# domain pack — this module only finds the document's own section skeleton.
_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:Section|SECTION|Chapter|CHAPTER|Article|ARTICLE)\s+\d+[A-Za-z]*\.?"
    r"|\d{1,4}[A-Z]{0,2}\.\s+[A-Z]"
    r"|#{1,6}\s+.+"
    r")",
)

_BLANK_RUN = re.compile(r"\n{2,}")


def _byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


class PlainTextParser:
    """L0 parser for plain text and markdown. No external dependencies."""

    def parse(self, raw: bytes, doc_class: str | None = None) -> ParseResult:
        warnings: list[str] = []
        text = raw.decode("utf-8", errors="replace")
        if "�" in text:
            warnings.append("utf-8 decode produced replacement characters")

        blocks = self._split_blocks(text)
        if not blocks:
            return ParseResult(nodes=(), structural_edges=(), confidence=0.0,
                                warnings=[*warnings, "no content blocks found"])

        nodes: list[Node] = []
        structural_edges: list[Edge] = []
        section_stack: list[str] = []
        current_section_node: str | None = None

        for seq, (block_text, byte_start, byte_end) in enumerate(blocks):
            rel_id = str(seq)
            is_heading = bool(_HEADING_RE.match(block_text))
            if is_heading:
                title = block_text.strip().lstrip("#").strip()
                section_stack = [title]
                node = Node(
                    node_id=rel_id,
                    doc_id="",
                    kind=NodeKind.STRUCTURAL,
                    seq=seq,
                    raw=block_text,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    inherited=InheritedContext(section_path=title),
                    is_assertive=False,
                )
                current_section_node = rel_id
            else:
                section_path = " > ".join(section_stack) if section_stack else None
                node = Node(
                    node_id=rel_id,
                    doc_id="",
                    kind=NodeKind.PROPOSITION,
                    seq=seq,
                    raw=block_text,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    inherited=InheritedContext(section_path=section_path),
                )
                if current_section_node is not None:
                    structural_edges.append(Edge(
                        edge_id=f"struct:{rel_id}:part_of:{current_section_node}",
                        src=rel_id,
                        dst=current_section_node,
                        type=EdgeType.PART_OF,
                        provenance=Provenance.STRUCTURAL,
                        evidence_span=block_text[:200],
                    ))
            nodes.append(node)

        confidence = 1.0
        if warnings:
            confidence -= 0.3
        confidence = max(0.0, min(1.0, confidence))

        return ParseResult(nodes=nodes, structural_edges=structural_edges,
                            confidence=confidence, warnings=warnings)

    @staticmethod
    def _split_blocks(text: str) -> list[tuple[str, int, int]]:
        """Blank-line-delimited blocks, with byte offsets into the utf-8 source."""
        blocks: list[tuple[str, int, int]] = []
        char_pos = 0
        for part in _BLANK_RUN.split(text):
            start_char = text.index(part, char_pos) if part else char_pos
            stripped = part.strip()
            if stripped:
                # Byte offset of the stripped block within the original text.
                lead_ws = len(part) - len(part.lstrip())
                block_start_char = start_char + lead_ws
                byte_start = _byte_len(text[:block_start_char])
                byte_end = byte_start + _byte_len(stripped)
                blocks.append((stripped, byte_start, byte_end))
            char_pos = start_char + len(part)
        return blocks


def finalize_doc_id(doc_id: str, nodes: list[Node], edges: list[Edge]) -> tuple[list[Node], list[Edge]]:
    """Rewrite relative ids from `PlainTextParser` into final, doc-qualified ids."""
    final_nodes = [replace(n, node_id=f"{doc_id}:{n.node_id}", doc_id=doc_id) for n in nodes]
    final_edges = [
        replace(
            e,
            edge_id=f"{doc_id}:{e.edge_id}",
            src=f"{doc_id}:{e.src}",
            dst=f"{doc_id}:{e.dst}",
        )
        for e in edges
    ]
    return final_nodes, final_edges
