"""L0 substrate: deterministic parse -> structure + byte offsets + confidence.

`PlainTextParser` is the default `Parser` implementation for `.txt` / `.md`
input. It never guesses: every node's `byte_start`/`byte_end` is a verifiable
slice of the original bytes (CLAUDE.md invariant 1, invariant 10), and
confidence reflects what the parser actually recovered rather than a fixed
constant.

Design and the corpus evidence behind it: `PARSER_PLAN.md`. Two facts drive
everything here:

  - The corpus has two hard-wrap dialects. One puts a whole section on a
    handful of long lines (blank line separates *sections*); the other
    hard-wraps every sentence at ~75 columns with a blank line between every
    fragment (enumerators frequently alone on their own line). A single
    reflow rule handles both — see `_reflow` — rather than detecting the
    dialect and branching.
  - A bare `N.` line is genuinely ambiguous: it is a section heading in one
    place and a wrapped sub-section marker in another, with the identical
    shape. `_classify_line` resolves this locally (marginal-note terminator,
    then sentence-shape of the title text) rather than assuming one
    convention for the whole document — see PARSER_PLAN.md Decision 4.

Docling belongs behind this same protocol for PDFs/DOCX (CLAUDE.md anti-goals:
we do not write a parser, we adapt one) — not wired here, the dependency isn't
part of the offline core path.

Node/doc identity: this parser does not know the final `doc_id` (a content
hash computed by the pipeline). It emits nodes and edges keyed by a small
relative id ("0", "1", ...); `finalize_doc_id` rewrites them in one pass. This
keeps parsing a pure function of the bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from dge.interfaces import ParseResult
from dge.model import Edge, EdgeType, InheritedContext, Node, NodeKind, Provenance

# ---------------------------------------------------------------------------
# Line-kind regexes
# ---------------------------------------------------------------------------

# India Code renders amended text as `N[ ... ]`, N a footnote number, sitting
# BEFORE the enumerator it amends: `2[3. Act not to apply...`, `4[(1)] In
# this Act`. Every enumerator pattern below has to see through it. The
# closing `]` is not tracked — it can land lines later, inside otherwise
# ordinary prose, and is harmless left in place as part of the verbatim span.
_AMD = r"(?:\d{1,3}\[)*"

_KEYWORD_HEADING_RE = re.compile(
    rf"^(?:Section|SECTION|Chapter|CHAPTER|Article|ARTICLE)\s+{_AMD}"
    rf"(\d{{1,4}}[A-Z]{{0,2}})\.?\s*(?=\S|$)",
)
_BARE_HEADING_RE = re.compile(rf"^{_AMD}(\d{{1,4}}[A-Z]{{0,2}})\.\s*(?=\S)")
_BARE_HEADING_ONLY_RE = re.compile(rf"^{_AMD}(\d{{1,4}}[A-Z]{{0,2}})\.\s*$")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_CHAPTER_RE = re.compile(r"^(?:CHAPTER|Chapter|PART|Part)\s+([IVXLCDM]+|\d+)\b")
_SCHEDULE_RE = re.compile(
    r"^(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|[IVX]+)?\s*SCHEDULE\b",
    re.IGNORECASE,
)
_SUBSEC_RE = re.compile(rf"^{_AMD}\((\d{{1,3}}[A-Z]?)\)\s*")
_CLAUSE_RE = re.compile(rf"^{_AMD}\(([a-z]{{1,3}})\)\s*")
_CLAUSE_D_RE = re.compile(rf"^{_AMD}([a-z])\.\s+")
_CLAUSE_D_ONLY_RE = re.compile(rf"^{_AMD}([a-z])\.\s*$")
_PROVISO_RE = re.compile(r"^Provided\s+(?:further\s+|also\s+)?that\b", re.IGNORECASE)
_EXPLANATION_RE = re.compile(r"^Explanation\s*\d*\s*[.\-—:]", re.IGNORECASE)
_ILLUSTRATION_RE = re.compile(r"^Illustrations?\s*[.\-—:]", re.IGNORECASE)
_EXCEPTION_RE = re.compile(r"^Exceptions?\s*\d*\s*[.\-—:]", re.IGNORECASE)

# A real marginal-note heading almost always ends "...-" / "...—" / "...:--".
# The strongest available signal that a `heading`-shaped line really is one.
_MARGINAL_TERMINATOR_RE = re.compile(r"[.:]\s*[\-—–]{1,2}\s*$")

# A line that ends mid-thought (no terminal punctuation, or a dangling comma)
# is a dialect-B hard-wrap fragment, not the end of a sentence or title —
# both a heading's own title AND ordinary prose wrap this way across a blank
# line. A bare dash/em-dash also counts as "complete": in this corpus it is
# how a marginal-note terminator sometimes survives HTML->text conversion
# with its preceding period dropped ("commencement -" as well as
# "commencement.-").
_LOOKS_COMPLETE_RE = re.compile(r"[.:;?!]\s*$|[\-—–]{1,2}\s*$")

# Footnote citation lines are heading-shaped ("1. Ins. by Act 42 of 1983...")
# and must not be read as section headings.
_FN_VERB_RE = re.compile(
    r"^\d{1,3}\.\s+(Ins\.|Subs\.|Omitted|Rep\.|Added|Renumbered|Certain|The\s+words"
    r"|The\s+Explanation|Sub-clause|Clause|Section|Now|Vide|Received|Came|Enforced|Published)",
    re.IGNORECASE,
)
_FN_TAIL_RE = re.compile(
    r"\(w\.e\.f|\bibid\b|by\s+Act\s+\d+\s+of\s+\d{4}|vide\s+notification", re.IGNORECASE,
)

# Splits an inline proviso off the clause it modifies: "...Magistrate:
# Provided that..." is two propositions on one physical line.
_INLINE_PROVISO_RE = re.compile(
    r":\s*(?=Provided\s+(?:further\s+|also\s+)?that\b)", re.IGNORECASE,
)

# A heading-shaped line whose title reads as a full sentence (a subject and
# a verb) is a mis-shapen sub-section, not a new section — PARSER_PLAN.md
# Decision 4. Applied only to the *ambiguous* bare-`N.` heading forms; a
# `Section N.` / `Chapter N.` keyword-prefixed heading is unambiguous by
# construction and is never run through this check.
_SENTENCE_LIKE_RE = re.compile(
    r"^(?:It|This|The|In|No|Every|Where|Notwithstanding|Save|Subject|For|Whoever|Any|All)\b"
    r"|\b(?:shall|may|means|extends?|includes?|is|are|has|have|deemed)\b",
    re.IGNORECASE,
)

# Kinds that start a new logical unit (as opposed to being absorbed into the
# currently open one).
_STARTER_KINDS = frozenset({
    "chapter", "schedule", "md_heading", "heading", "heading_bare",
    "subsec", "clause", "clause_d", "proviso", "explanation",
    "illustration", "exception", "footnote", "footnote?",
})
# While one of these is open, a blank line is a wrapping artifact (dialect B)
# and gets absorbed rather than ending the unit.
_ENUMERATED_KINDS = _STARTER_KINDS - {"footnote", "footnote?"}
# Kinds that become NodeKind.STRUCTURAL and open a new level in the nesting
# stack. Everything else attaches to whatever is deepest on the stack without
# pushing itself. `heading_bare` is a real section heading (dialect B, `1.`
# with no keyword prefix) whenever `_classify_line` did NOT reclassify it to
# `subsec` — same level and kind as `heading`.
_STRUCTURAL_KINDS = frozenset({"chapter", "schedule", "md_heading", "heading", "heading_bare"})
# Amendment/editorial footnote lines ("1. Subs. by Act 42 of 1983, s. 11, for
# certain words (w.e.f. 31-5-1984).") are heading-shaped but are apparatus,
# not provisions: they must never become a closure-edge target (a proviso
# resolving to "its preceding sibling" must never land on one) or a
# structural/marker match source. `NodeKind.FOOTNOTE` keeps them in the
# substrate — still readable, still byte-addressed — while excluding them
# from every place that treats a node as a candidate provision.
_FOOTNOTE_KINDS = frozenset({"footnote", "footnote?"})
_NESTING_LEVEL: dict[str, int] = {
    "chapter": 0, "schedule": 0, "md_heading": 1, "heading": 1, "heading_bare": 1,
    "subsec": 2, "clause": 3, "clause_d": 3,
}


def _next_nonblank(stripped_lines: list[str], idx: int) -> str | None:
    for j in range(idx + 1, min(idx + 4, len(stripped_lines))):
        if stripped_lines[j]:
            return stripped_lines[j]
        if j > idx + 1:
            break  # only one blank line of lookahead tolerance
    return None


def _classify_line(s: str, last_sec: int, lookahead: str | None) -> str:
    if _CHAPTER_RE.match(s):
        return "chapter"
    if _SCHEDULE_RE.match(s):
        return "schedule"
    if _PROVISO_RE.match(s):
        return "proviso"
    if _EXPLANATION_RE.match(s):
        return "explanation"
    if _ILLUSTRATION_RE.match(s):
        return "illustration"
    if _EXCEPTION_RE.match(s):
        return "exception"
    if _SUBSEC_RE.match(s):
        return "subsec"
    if _CLAUSE_RE.match(s):
        return "clause"
    if _MD_HEADING_RE.match(s):
        return "md_heading"
    if _KEYWORD_HEADING_RE.match(s):
        return "heading"

    if _BARE_HEADING_ONLY_RE.match(s):
        if (lookahead and _SENTENCE_LIKE_RE.search(lookahead)
                and not _MARGINAL_TERMINATOR_RE.search(lookahead)):
            return "subsec"
        return "heading_bare"

    m = _BARE_HEADING_RE.match(s)
    if m:
        if _FN_VERB_RE.match(s) or _FN_TAIL_RE.search(s):
            return "footnote"
        num = int(re.match(r"\d+", m.group(1)).group(0))  # type: ignore[union-attr]
        if num <= last_sec:
            return "footnote?"
        if _MARGINAL_TERMINATOR_RE.search(s):
            return "heading"
        remainder = s[m.end():]
        if _SENTENCE_LIKE_RE.search(remainder):
            return "subsec"
        return "heading"

    if _CLAUSE_D_RE.match(s) or _CLAUSE_D_ONLY_RE.match(s):
        return "clause_d"
    return "text"


def _heading_number(s: str) -> int:
    m = _KEYWORD_HEADING_RE.match(s) or _BARE_HEADING_RE.match(s) or _BARE_HEADING_ONLY_RE.match(s)
    assert m
    return int(re.match(r"\d+", m.group(1)).group(0))  # type: ignore[union-attr]


def _is_bare_starter(kind: str, s: str) -> bool:
    """True when a starter line carries only its enumerator, no title/body
    text yet (e.g. a lone `1.` or `(1)`). Only a bare starter's *own* unit may
    absorb a blank line — see `_reflow`: a `Section 1. Title.` heading is
    already complete and a following blank is a real block separator, while a
    lone `1.` is mid-wrap and the blank is a dialect-B wrapping artifact."""
    if kind == "heading_bare":
        return True
    if kind == "subsec":
        m = _SUBSEC_RE.match(s)
        if m:
            return not s[m.end():].strip()
        # A bare-digit line ("1.") that `_classify_line` reclassified from
        # heading_bare to subsec via the sentence-lookahead rule — same bare
        # shape, no parens.
        return bool(_BARE_HEADING_ONLY_RE.match(s))
    if kind == "clause":
        m = _CLAUSE_RE.match(s)
        if not m:
            return False
        return not s[m.end():].strip()
    if kind == "clause_d":
        m = _CLAUSE_D_RE.match(s)
        if m:
            return not s[m.end():].strip()
        return bool(_CLAUSE_D_ONLY_RE.match(s))
    return False


def _iter_physical_lines(text: str) -> list[tuple[str, int, int]]:
    """`(content, char_start, char_end)` per physical line, terminator excluded.
    Handles `\\n`, `\\r\\n`, and bare `\\r` without desyncing offsets."""
    lines: list[tuple[str, int, int]] = []
    n = len(text)
    i = 0
    while i <= n:
        j = i
        while j < n and text[j] not in "\r\n":
            j += 1
        lines.append((text[i:j], i, j))
        if j >= n:
            break
        i = j + 2 if text[j] == "\r" and j + 1 < n and text[j + 1] == "\n" else j + 1
    return lines


@dataclass(frozen=True, slots=True)
class _RawUnit:
    kind: str
    char_start: int
    char_end: int  # exclusive; verbatim span, outer whitespace trimmed only


def _reflow(text: str) -> tuple[list[_RawUnit], dict[str, int]]:
    """Physical lines -> logical units, per PARSER_PLAN.md Decision 2: a unit
    starts at an enumerator/keyword line and absorbs every following line that
    doesn't start one. A blank line is absorbed — rather than ending the unit
    — while EITHER of two things is true of what's been accumulated so far:
    the open unit is still a bare enumerator with no title/body yet (`1.`,
    `(1)`), or its last line ends mid-thought rather than at a sentence/title
    boundary. The second condition is the one that matters most: dialect B
    hard-wraps a heading's own title, and ordinary prose, across a blank line
    exactly like it wraps an enumerator's continuation, so bareness alone
    (only covering the first case) still let titles and sentences fracture
    at the first blank.

    Returns the units and confidence-scoring counters gathered for free during
    the same pass (PARSER_PLAN.md Decision 4 / Task 6): units are cheap to
    reclassify after the fact, but the raw line-level signal (footnote-shaped
    non-monotonic headings) is not recoverable once collapsed into units.
    """
    lines = _iter_physical_lines(text)
    stripped = [content.strip() for content, _, _ in lines]

    units: list[_RawUnit] = []
    counters = {"ambiguous_headings": 0}
    cur_kind: str | None = None
    cur_start: int | None = None
    cur_end: int | None = None
    cur_bare = False
    last_line_s = ""
    last_sec = 0

    def flush() -> None:
        nonlocal cur_kind, cur_start, cur_end, cur_bare
        if cur_start is not None and cur_end is not None and cur_end > cur_start:
            units.append(_RawUnit(cur_kind or "text", cur_start, cur_end))
        cur_kind, cur_start, cur_end, cur_bare = None, None, None, False

    for idx, (content, cstart, cend) in enumerate(lines):
        s = stripped[idx]
        if not s:
            still_open = cur_kind in _ENUMERATED_KINDS and (
                cur_bare or not _LOOKS_COMPLETE_RE.search(last_line_s)
            )
            if not still_open:
                flush()
            continue
        kind = _classify_line(s, last_sec, _next_nonblank(stripped, idx))
        if kind == "heading":
            last_sec = _heading_number(s)
        if kind == "footnote?":
            counters["ambiguous_headings"] += 1
        if kind in _STARTER_KINDS:
            flush()
            cur_kind = kind
            cur_bare = _is_bare_starter(kind, s)
        elif cur_kind is not None:
            cur_bare = False
        last_line_s = s
        lead = len(content) - len(content.lstrip())
        trail = len(content) - len(content.rstrip())
        if cur_start is None:
            cur_start = cstart + lead
        cur_end = cend - trail

    flush()
    return units, counters


def _split_inline_provisos(units: list[_RawUnit], text: str) -> list[_RawUnit]:
    out: list[_RawUnit] = []
    for unit in units:
        span = text[unit.char_start:unit.char_end]
        matches = list(_INLINE_PROVISO_RE.finditer(span))
        if not matches:
            out.append(unit)
            continue
        prev_end = 0
        for m in matches:
            head = span[prev_end:m.start()].rstrip()
            if head:
                out.append(_RawUnit(unit.kind, unit.char_start + prev_end,
                                     unit.char_start + prev_end + len(head)))
            prev_end = m.end()
        tail = span[prev_end:]
        lead = len(tail) - len(tail.lstrip())
        if tail.strip():
            out.append(_RawUnit("proviso", unit.char_start + prev_end + lead, unit.char_end))
    return out


def _path_label(kind: str, raw: str) -> str:
    if kind in _STRUCTURAL_KINDS:
        flat = re.sub(r"\s+", " ", raw).strip()
        return flat if len(flat) <= 80 else flat[:77] + "..."
    m = _SUBSEC_RE.match(raw) or _CLAUSE_RE.match(raw) or _CLAUSE_D_RE.match(raw)
    if m:
        return f"({m.group(1)})"
    flat = re.sub(r"\s+", " ", raw).strip()
    return flat if len(flat) <= 40 else flat[:37] + "..."


class _ByteCursor:
    """Char offset -> byte offset, forward-only. Each call encodes only the
    delta since the last call, so a full document costs O(n) total instead of
    the O(n^2) that re-encoding `text[:pos]` from scratch per node would."""

    __slots__ = ("_byte_pos", "_char_pos", "_text")

    def __init__(self, text: str) -> None:
        self._text = text
        self._char_pos = 0
        self._byte_pos = 0

    def offset(self, char_pos: int) -> int:
        if char_pos < self._char_pos:
            raise ValueError("byte cursor only advances forward")
        if char_pos > self._char_pos:
            self._byte_pos += len(self._text[self._char_pos:char_pos].encode("utf-8"))
            self._char_pos = char_pos
        return self._byte_pos


# Confidence thresholds, calibrated against the 62-act corpus in
# corpus/indian-acts/ — see PARSER_PLAN.md Task 6 and `dge validate-parse`.
_TEXT_FRACTION_BAD = 0.5
_AMBIGUOUS_FRACTION_BAD = 0.1
_MAX_UNIT_LEN_BAD = 4000


class PlainTextParser:
    """L0 parser for plain text and markdown. No external dependencies."""

    def parse(self, raw: bytes, doc_class: str | None = None) -> ParseResult:
        warnings: list[str] = []
        text = raw.decode("utf-8", errors="replace")
        if "�" in text:
            warnings.append("utf-8 decode produced replacement characters")

        raw_units, counters = _reflow(text)
        units = _split_inline_provisos(raw_units, text)
        if not units:
            return ParseResult(nodes=(), structural_edges=(), confidence=0.0,
                                warnings=[*warnings, "no content blocks found"])

        cursor = _ByteCursor(text)
        nodes: list[Node] = []
        structural_edges: list[Edge] = []
        stack: list[tuple[int, str, str]] = []  # (level, node_id, path_label)
        n_text_units = 0

        for seq, unit in enumerate(units):
            rel_id = str(seq)
            body = text[unit.char_start:unit.char_end]
            if unit.kind == "text":
                n_text_units += 1
            level = _NESTING_LEVEL.get(unit.kind)
            if level is not None:
                while stack and stack[-1][0] >= level:
                    stack.pop()
            parent_id = stack[-1][1] if stack else None
            section_path = " > ".join(label for _, _, label in stack) or None
            if unit.kind in _STRUCTURAL_KINDS:
                kind = NodeKind.STRUCTURAL
            elif unit.kind in _FOOTNOTE_KINDS:
                kind = NodeKind.FOOTNOTE
            else:
                kind = NodeKind.PROPOSITION
            is_assertive = kind is NodeKind.PROPOSITION

            node = Node(
                node_id=rel_id,
                doc_id="",
                kind=kind,
                seq=seq,
                raw=body,
                byte_start=cursor.offset(unit.char_start),
                byte_end=cursor.offset(unit.char_end),
                inherited=InheritedContext(section_path=section_path),
                is_assertive=is_assertive,
            )
            nodes.append(node)

            if parent_id is not None:
                structural_edges.append(Edge(
                    edge_id=f"struct:{rel_id}:part_of:{parent_id}",
                    src=rel_id, dst=parent_id, type=EdgeType.PART_OF,
                    provenance=Provenance.STRUCTURAL, evidence_span=body[:200],
                ))
            if level is not None:
                stack.append((level, rel_id, _path_label(unit.kind, body)))

        text_fraction = n_text_units / len(units)
        ambiguous_fraction = counters["ambiguous_headings"] / len(units)
        max_unit_len = max(len(n.raw) for n in nodes)

        confidence = 1.0
        if "utf-8 decode produced replacement characters" in warnings:
            confidence -= 0.3
        if text_fraction > _TEXT_FRACTION_BAD:
            confidence -= 0.55
            warnings.append(
                f"{text_fraction:.0%} of units carry no structural marker "
                f"(threshold {_TEXT_FRACTION_BAD:.0%}); heading/enumerator "
                f"detection likely failed on this document"
            )
        if ambiguous_fraction > _AMBIGUOUS_FRACTION_BAD:
            confidence -= 0.2
            warnings.append(
                f"{ambiguous_fraction:.0%} of units are non-monotonic heading-shaped "
                f"lines (likely footnotes) that could not be resolved with confidence"
            )
        if max_unit_len > _MAX_UNIT_LEN_BAD:
            confidence -= 0.3
            warnings.append(
                f"largest unit is {max_unit_len} chars (threshold {_MAX_UNIT_LEN_BAD}); "
                f"reflow likely failed to split a block"
            )
        confidence = max(0.0, min(1.0, confidence))

        return ParseResult(nodes=nodes, structural_edges=structural_edges,
                            confidence=confidence, warnings=warnings)


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
