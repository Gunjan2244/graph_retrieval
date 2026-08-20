"""Granularity + confidence report for `dge.parsing.PlainTextParser` against
the real corpus. This is the durable check behind PARSER_PLAN.md Task 6's
exit criterion — run it after any change to `src/dge/parsing.py`:

    python3 scripts/parser_corpus_report.py

Unlike `scripts/proto_parser_lines.py` (a throwaway prototype with its own,
now-superseded classifier used only to arrive at the design), this imports the
real parser, so its numbers are the ones that actually ship.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dge.parsing import PlainTextParser

CORPUS = Path("corpus/indian-acts")


def main() -> None:
    parser = PlainTextParser()
    rows = []
    for path in sorted(CORPUS.glob("*.txt")):
        result = parser.parse(path.read_bytes())
        if not result.nodes:
            rows.append((path.name, 0, 0.0, 0, 0, result.confidence, "EMPTY"))
            continue
        lens = [len(n.raw) for n in result.nodes]
        rows.append((
            path.name, len(result.nodes), statistics.median(lens),
            sorted(lens)[int(0.95 * len(lens))], max(lens), result.confidence,
            "; ".join(result.warnings)[:90],
        ))

    rows.sort(key=lambda r: r[5])
    print(f"{'file':52s} {'nodes':>6s} {'med':>5s} {'p95':>6s} {'max':>6s} {'conf':>5s}  warnings")
    for name, n, med, p95, mx, conf, warn in rows:
        print(f"{name[:52]:52s} {n:6d} {med:5.0f} {p95:6d} {mx:6d} {conf:5.2f}  {warn}")

    meds = [r[2] for r in rows if r[1]]
    maxes = [r[4] for r in rows if r[1]]
    confs = [r[5] for r in rows]
    below = sum(1 for c in confs if c < 0.5)
    at_full = sum(1 for c in confs if c == 1.0)
    print(f"\nmedian-of-medians={statistics.median(meds):.0f}  "
          f"median-max={statistics.median(maxes):.0f}  worst-max={max(maxes)}")
    print(f"below confidence 0.5 (halted for review): {below}/{len(rows)}   "
          f"at full confidence: {at_full}/{len(rows)}")


if __name__ == "__main__":
    main()
