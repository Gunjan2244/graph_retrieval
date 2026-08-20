"""PROTOTYPE — not production code, not imported by `dge`.

Evidence behind `PARSER_PLAN.md`. Classifies every physical line of the fetched
bare-act corpus into a structural kind, reflows wrapped lines into logical
units, and reports the resulting granularity per document. Run it before and
after rewriting `dge.parsing` to see whether granularity actually moved.

    python3 scripts/proto_parser_lines.py                 # granularity table
    python3 scripts/proto_parser_lines.py --lines         # line-kind totals
    python3 scripts/proto_parser_lines.py --show FILE     # first units of one act

Deliberately has no `dge` imports and no dependencies: it is a measuring stick,
and a measuring stick that shares code with the thing it measures is useless.
It also throws byte offsets away, which production code may NOT do — see
PARSER_PLAN.md "Decision 3".
"""

from __future__ import annotations

import re
import statistics
import sys
from pathlib import Path

CORPUS = Path("corpus/indian-acts")

# India Code wraps amended text as `N[ ... ]`, N being a footnote number, and it
# sits BEFORE the enumerator: `2[3. Act not to apply...`, `4[(1)] In this Act`.
# Every enumerator pattern has to see through it.
AMD = r"(?:\d{1,3}\[)*"

HEADING = re.compile(rf"^{AMD}(\d{{1,4}}[A-Z]{{0,2}})\.\s*(?=\S)")
HEADING_BARE = re.compile(rf"^{AMD}(\d{{1,4}}[A-Z]{{0,2}})\.\s*$")
CHAPTER = re.compile(r"^(?:CHAPTER|Chapter|PART|Part)\s+([IVXLCDM]+|\d+)\b")
SCHEDULE = re.compile(r"^(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|[IVX]+)?\s*SCHEDULE\b", re.IGNORECASE)
SUBSEC = re.compile(rf"^{AMD}\((\d{{1,3}}[A-Z]?)\)\s*")
CLAUSE_P = re.compile(rf"^{AMD}\(([a-z]{{1,3}})\)\s*")
CLAUSE_D = re.compile(rf"^{AMD}([a-z])\.\s+")
PROVISO = re.compile(r"^Provided\s+(?:further\s+|also\s+)?that\b", re.IGNORECASE)
EXPLAIN = re.compile(r"^Explanation\s*\d*\s*[.\-—:]", re.IGNORECASE)
ILLUS = re.compile(r"^Illustrations?\b", re.IGNORECASE)
EXCEPT = re.compile(r"^Exceptions?\s*\d*\s*[.\-—:]", re.IGNORECASE)

# A marginal note terminator is the single most reliable heading signal.
MARGINAL_END = re.compile(r"[.\:]\s*[-—–]{1,2}\s*$")
# Amendment-footnote signals. Either the leading verb or the citation tail.
FN_VERB = re.compile(
    r"^\d{1,3}\.\s+(Ins\.|Subs\.|Omitted|Rep\.|Added|Renumbered|Certain|The\s+words"
    r"|The\s+Explanation|Sub-clause|Clause|Section|Now|Vide|Received|Came|Enforced|Published)",
    re.IGNORECASE,
)
FN_TAIL = re.compile(r"\(w\.e\.f|\bibid\b|by\s+Act\s+\d+\s+of\s+\d{4}|vide\s+notification", re.IGNORECASE)

STARTERS = {"chapter", "schedule", "proviso", "explanation", "illustration", "exception",
            "subsec", "clause", "clause_d", "heading", "heading_bare", "footnote", "footnote?"}
# Kinds that own an enumerator: while one of these is open, a blank line is a
# wrapping artifact, not a separator.
ENUMERATED = STARTERS - {"footnote", "footnote?"}


def classify(line: str, last_sec: int) -> str:
    s = line.strip()
    if not s:
        return "blank"
    if CHAPTER.match(s):
        return "chapter"
    if SCHEDULE.match(s):
        return "schedule"
    if PROVISO.match(s):
        return "proviso"
    if EXPLAIN.match(s):
        return "explanation"
    if ILLUS.match(s):
        return "illustration"
    if EXCEPT.match(s):
        return "exception"
    if SUBSEC.match(s):
        return "subsec"
    if CLAUSE_P.match(s):
        return "clause"
    if HEADING_BARE.match(s):
        return "heading_bare"          # ambiguous: section heading or <ol> item
    m = HEADING.match(s)
    if m:
        if FN_VERB.match(s) or FN_TAIL.search(s):
            return "footnote"
        base = int(re.match(r"\d+", m.group(1)).group(0))
        if base <= last_sec:
            return "footnote?"          # non-monotonic; probably a footnote
        return "heading"
    if CLAUSE_D.match(s):
        return "clause_d"
    return "text"


def reflow(text: str) -> list[tuple[str, str]]:
    """Physical lines -> logical units. One rule, no dialect branch."""
    units: list[tuple[str, str]] = []
    cur_kind: str | None = None
    cur_parts: list[str] = []
    last_sec = 0

    def flush() -> None:
        nonlocal cur_kind, cur_parts
        if cur_parts:
            joined = re.sub(r"\s+", " ", " ".join(cur_parts)).strip()
            if joined:
                units.append((cur_kind or "text", joined))
        cur_kind, cur_parts = None, []

    for line in text.replace("\r", "").split("\n"):
        kind = classify(line, last_sec)
        if kind == "heading":
            m = HEADING.match(line.strip())
            assert m
            last_sec = int(re.match(r"\d+", m.group(1)).group(0))
        if kind == "blank":
            if cur_kind not in ENUMERATED:
                flush()
            continue
        if kind in STARTERS:
            flush()
            cur_kind = kind
        cur_parts.append(line.strip())
    flush()
    return units


def split_inline_provisos(units: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """`...Magistrate: Provided that...` is two propositions on one line."""
    out: list[tuple[str, str]] = []
    for kind, text in units:
        parts = re.split(r"(?=:\s*Provided\s+(?:further\s+|also\s+)?that\b)", text)
        out.append((kind, parts[0]))
        out.extend(("proviso", p.lstrip(": ").strip()) for p in parts[1:])
    return out


def units_for(path: Path) -> list[tuple[str, str]]:
    return split_inline_provisos(reflow(path.read_text("utf-8", errors="replace")))


def _granularity_table() -> None:
    print(f"{'file':52s} {'units':>6s} {'med':>5s} {'p95':>6s} {'max':>6s} {'%text':>6s}")
    medians, maxes = [], []
    for path in sorted(CORPUS.glob("*.txt")):
        units = units_for(path)
        if not units:
            continue
        lens = [len(t) for _, t in units]
        pct_text = sum(1 for k, _ in units if k == "text") * 100 // len(units)
        med = statistics.median(lens)
        mx = max(lens)
        medians.append(med)
        maxes.append(mx)
        print(f"{path.name[:52]:52s} {len(units):6d} {med:5.0f} "
              f"{sorted(lens)[int(0.95 * len(lens))]:6d} {mx:6d} {pct_text:5d}%")
    print(f"\nmedian-of-medians={statistics.median(medians):.0f}  "
          f"median-max={statistics.median(maxes):.0f}  worst-max={max(maxes)}")


def _line_totals() -> None:
    totals: dict[str, int] = {}
    for path in sorted(CORPUS.glob("*.txt")):
        last_sec = 0
        for line in path.read_text("utf-8", errors="replace").replace("\r", "").split("\n"):
            kind = classify(line, last_sec)
            if kind == "heading":
                m = HEADING.match(line.strip())
                assert m
                last_sec = int(re.match(r"\d+", m.group(1)).group(0))
            totals[kind] = totals.get(kind, 0) + 1
    for kind, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"{kind:14s} {n:6d}")


def main() -> None:
    if "--lines" in sys.argv:
        _line_totals()
    elif "--show" in sys.argv:
        name = sys.argv[sys.argv.index("--show") + 1]
        for kind, text in units_for(CORPUS / name)[:40]:
            print(f"[{kind:12s}] {text[:110]}")
    else:
        _granularity_table()


if __name__ == "__main__":
    main()
