"""CLAUDE.md invariant 10, enforced in code.

    "An edge whose `evidence_span` does not appear verbatim in the model's
     input window is discarded. Enforce in code, not in the prompt."

This module is deliberately the first thing in L3 and depends on nothing else
in the package. A prompt that *asks* for verbatim spans is a request; this is
the enforcement, and it is what makes a model-extracted edge worth storing at
all: a candidate that cannot point at text actually present in the window is
indistinguishable from a fabrication, whatever its stated confidence.

Three verdicts, not two
-----------------------
`EXACT`     the span is a byte-for-byte substring of the window. Nothing to
            decide.
`REFLOWED`  the span matches a window substring after collapsing runs of
            whitespace (including the U+00A0 non-breaking spaces this corpus is
            full of, and the internal newlines PARSER_PLAN.md Decision 3 leaves
            inside `node.raw` after hard-wrap reflow). ACCEPTED — but the check
            returns the **original window slice**, and callers store that, so
            what lands in `edges.evidence_span` is still verbatim window text.
            The model's own whitespace is discarded, not trusted.
`REJECTED`  everything else.

Accepting `REFLOWED` is a real decision and worth defending, because the lazy
reading of invariant 10 is "exact substring or nothing". Exact-only fails on
this corpus for a reason that has nothing to do with the model's honesty: the
window a model sees contains hard-wrapped lines, so the sentence it is quoting
literally contains a newline mid-phrase, and every model on earth returns it
with a space. Rejecting that discards true edges while catching zero
fabrications. What invariant 10 is defending against is a span whose *content*
is not in the window — a paraphrase, a near-miss, a quote lifted from a
different section. Whitespace is not content, and the recovered-slice rule
means a reflowed match never widens what gets stored.

Everything else stays strict on purpose:

  - Case differences are REJECTED (`case_mismatch_only`), not folded in. A
    model that changed the case changed the characters, and unlike whitespace
    that is not a rendering artifact of our own parser.
  - Spans below `MIN_EVIDENCE_CHARS` are REJECTED (`too_short`). Without a
    floor, "the" or "shall" is a valid citation of any legal text ever
    written, and invariant 10 degrades into a spell-check.
  - An empty or whitespace-only span is REJECTED, never treated as "no claim".
    A candidate with no evidence is a candidate with no evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Characters this module treats as whitespace when `allow_reflow` is on. The
# unicode spaces are not decoration: the fetched Indian Acts carry U+00A0 and
# friends inside provisions, which is exactly why HANDOFF.md warns that
# hand-transcribed gold spans silently fail a verbatim check.
WHITESPACE: frozenset[str] = (
    frozenset(" \t\n\r\f\v")
    | frozenset("\u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff")
    | frozenset(chr(c) for c in range(0x2000, 0x200B))   # EN QUAD .. ZWSP
)
_WS_RE = re.compile("[" + re.escape("".join(sorted(WHITESPACE))) + "]+")

# A model citing fewer characters than this is not identifying a provision, it
# is matching a stopword. Tuned to admit the shortest real legal marker phrases
# ("per incuriam", 12) and reject everything below them.
MIN_EVIDENCE_CHARS = 12


class EvidenceVerdict(StrEnum):
    EXACT = "exact"
    REFLOWED = "reflowed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class EvidenceCheck:
    """Result of checking one candidate span against one input window.

    `span` is the text a caller must store — always a slice of the window
    itself, never the model's rendering of it, and None when rejected.
    """

    verdict: EvidenceVerdict
    span: str | None = None
    start: int | None = None
    end: int | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict is not EvidenceVerdict.REJECTED


def _normalize(text: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed text plus a map from each output char back to its
    index in `text`, so a match in normalized space can be recovered as an
    exact slice of the original."""
    out: list[str] = []
    index_map: list[int] = []
    pending_space_at: int | None = None
    for i, ch in enumerate(text):
        if ch in WHITESPACE:
            if out:  # never emit a leading space
                pending_space_at = i if pending_space_at is None else pending_space_at
            continue
        if pending_space_at is not None:
            out.append(" ")
            index_map.append(pending_space_at)
            pending_space_at = None
        out.append(ch)
        index_map.append(i)
    return "".join(out), index_map


def check_evidence(
    candidate_span: str | None,
    window: str,
    *,
    allow_reflow: bool = True,
    min_chars: int = MIN_EVIDENCE_CHARS,
) -> EvidenceCheck:
    """Decide whether `candidate_span` is verbatim evidence from `window`.

    The window is the ENTIRE text the model was shown for this call and nothing
    else. A span quoted from a neighbouring section fails here for free, with
    no extra rule, because that text was never in the window — which is why L3
    runs one section per call rather than one document per call.
    """
    if candidate_span is None:
        return EvidenceCheck(EvidenceVerdict.REJECTED, reason="missing")

    stripped = _WS_RE.sub(" ", candidate_span).strip()
    if not stripped:
        return EvidenceCheck(EvidenceVerdict.REJECTED, reason="empty")
    if len(stripped) < min_chars:
        return EvidenceCheck(
            EvidenceVerdict.REJECTED,
            reason=f"too_short ({len(stripped)} < {min_chars} chars)",
        )

    trimmed = candidate_span.strip()
    start = window.find(trimmed)
    if start >= 0:
        end = start + len(trimmed)
        return EvidenceCheck(EvidenceVerdict.EXACT, window[start:end], start, end)

    norm_window, index_map = _normalize(window)
    norm_span, _ = _normalize(candidate_span)

    if allow_reflow:
        at = norm_window.find(norm_span)
        if at >= 0:
            o_start = index_map[at]
            o_end = index_map[at + len(norm_span) - 1] + 1
            return EvidenceCheck(
                EvidenceVerdict.REFLOWED, window[o_start:o_end], o_start, o_end
            )

    if norm_window.casefold().find(norm_span.casefold()) >= 0:
        # The characters are present but the model altered them. Whitespace we
        # forgive because our own parser introduced it; case we do not.
        return EvidenceCheck(EvidenceVerdict.REJECTED, reason="case_mismatch_only")

    return EvidenceCheck(EvidenceVerdict.REJECTED, reason="not_in_window")
