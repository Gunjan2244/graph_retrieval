#!/usr/bin/env python3
"""Phase 0 — the failure-taxonomy exercise (BUILD_PLAN Phase 0, docs/06 §6.2).

50-100 real queries over the real corpus, each labeled with why a naive
flat-chunk retriever gets it wrong:

    lost_referent        - "it"/"the Board" resolved two paragraphs earlier
    lost_scope            - the governing condition sits in a header/preamble
    lost_exception         - a proviso/exception is not similar to the query
    wrong_version           - the answer is superseded by an amendment
    needs_aggregation        - the answer requires combining >1 provision
    topic_not_proposition     - embedding matches subject, not the specific claim

This determines build order (docs/06 §6.2): "if 70% are lost scope, Layer 1
ships next week and the graph is optional." It is deliberately NOT automated
against a live retriever — L2 hybrid retrieval does not exist yet (Stage C).
This is a hand-labeled exercise, and every entry is checked programmatically
against one invariant that also governs L3 in production (CLAUDE.md
invariant 10): a gold_span that is not verbatim in its cited source file is
worthless as ground truth, so it is rejected rather than silently trusted.

Usage:
    python scripts/phase0_taxonomy.py --cases corpus/failure_taxonomy.jsonl \\
        --corpus ./corpus/indian-acts
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

CAUSES = {
    "lost_referent",
    "lost_scope",
    "lost_exception",
    "wrong_version",
    "needs_aggregation",
    "topic_not_proposition",
}


@dataclass(frozen=True, slots=True)
class TaxonomyCase:
    query: str
    cause: str
    source_file: str
    gold_span: str
    naive_retrieval_gets: str
    why_it_fails: str


def load_cases(path: Path) -> list[TaxonomyCase]:
    cases: list[TaxonomyCase] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"{path}:{lineno}: invalid JSON: {e}", file=sys.stderr)
            continue
        cases.append(TaxonomyCase(**d))
    return cases


def validate(cases: list[TaxonomyCase], corpus_dir: Path) -> list[str]:
    """Every gold_span must appear verbatim in its cited source file. A
    taxonomy entry whose 'evidence' can't be found in the actual document is
    not evidence — it's a claim, and this exercise exists to catch the
    product making exactly that mistake."""
    errors: list[str] = []
    text_cache: dict[str, str] = {}
    for i, c in enumerate(cases):
        if c.cause not in CAUSES:
            errors.append(f"case {i} ({c.query!r}): unknown cause '{c.cause}'")
            continue
        src = corpus_dir / c.source_file
        if c.source_file not in text_cache:
            if not src.is_file():
                errors.append(f"case {i} ({c.query!r}): source file not found: {src}")
                continue
            text_cache[c.source_file] = src.read_text(encoding="utf-8", errors="replace")
        text = text_cache.get(c.source_file, "")
        if text and c.gold_span not in text:
            errors.append(
                f"case {i} ({c.query!r}): gold_span not verbatim in {c.source_file}"
            )
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, default=Path("corpus/failure_taxonomy.jsonl"))
    ap.add_argument("--corpus", type=Path, default=Path("corpus/indian-acts"))
    args = ap.parse_args()

    if not args.cases.is_file():
        print(f"no taxonomy file at {args.cases}", file=sys.stderr)
        return 1

    cases = load_cases(args.cases)
    errors = validate(cases, args.corpus)

    tally = Counter(c.cause for c in cases)
    print(f"=== Phase 0: failure taxonomy — {len(cases)} case(s) ===\n")
    for cause in sorted(CAUSES):
        n = tally.get(cause, 0)
        pct = (n / len(cases) * 100) if cases else 0.0
        print(f"  {cause:24s} {n:4d}  ({pct:.0f}%)")

    print(f"\nverbatim-grounding: {len(cases) - len(errors)}/{len(cases)} pass")
    if errors:
        print(f"\n{len(errors)} verbatim-grounding failure(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)

    print("\n--- target (BUILD_PLAN Phase 0): 50-100 labeled cases ---")
    if len(cases) < 50:
        print(f"[PARTIAL] {len(cases)}/50 minimum — this determines build order, "
              "keep going before treating it as conclusive")
    else:
        print(f"[OK] {len(cases)} cases reaches the 50-100 target range")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
