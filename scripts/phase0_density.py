#!/usr/bin/env python3
"""Phase 0 — closure-edge density measurement.

The entire traversal design rests on ONE empirical claim:

    closure edges are sparse and their chains terminate quickly.

If that is false in your corpus, bounded traversal does not work and the design
needs rework. This script measures it on real documents, before any product code
exists. Run it first. It is cheap and it can save months.

Usage:
    python scripts/phase0_density.py --corpus ./samples --domain legal
    python scripts/phase0_density.py --corpus ./samples --domain legal --json out.json

Input: a directory of .txt / .md files (run your parser first for PDFs — this
script deliberately does not depend on the parser so it can run on day zero).

PASS criteria (see BUILD_PLAN Phase 0):
    - closure chain length p95 <= 3
    - closure density at least ~10x below context density
    - closure-marker precision >= 0.8 on a hand-checked sample of 30
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dge.domains.legal import Confidence, DomainPack, get_pack

SENT_SPLIT = re.compile(r"(?<=[.;:])\s+(?=[A-Z(\"“])|\n{2,}")


def split_units(text: str) -> list[str]:
    """Crude unit splitter. Phase 0 only needs the right order of magnitude."""
    parts = [p.strip() for p in SENT_SPLIT.split(text) if p and p.strip()]
    return [p for p in parts if len(p) > 25]


def scan(units: list[str], pack: DomainPack) -> dict:
    """Detect marker and structural hits per unit."""
    closure_hits: dict[int, list[str]] = defaultdict(list)
    context_hits: dict[int, list[str]] = defaultdict(list)
    marker_counts: Counter[str] = Counter()
    definitions = 0
    citations = 0

    for i, u in enumerate(units):
        for su in pack.structural_units:
            if su.regex.search(u):
                marker_counts[f"struct:{su.name}"] += 1
                bucket = closure_hits if su.edge_class == "closure" else context_hits
                bucket[i].append(su.name)

        for m in pack.markers:
            if m.regex.search(u):
                marker_counts[f"marker:{m.name}"] += 1
                if m.confidence in (Confidence.STRONG, Confidence.MEDIUM):
                    closure_hits[i].append(m.name)

        for dp in pack.definition_patterns:
            if dp.search(u):
                definitions += 1
                break

        for cp in pack.citation_patterns:
            if cp.search(u):
                citations += 1
                break

    return {
        "closure_hits": closure_hits,
        "context_hits": context_hits,
        "marker_counts": marker_counts,
        "definitions": definitions,
        "citations": citations,
    }


def chain_lengths(closure_hits: dict[int, list[str]], n_units: int) -> list[int]:
    """Approximate closure chain depth.

    Heuristic for Phase 0: consecutive units that each carry a closure marker
    form a chain (a proviso to a proviso, an exception to an exception). This
    over-counts slightly, which is the safe direction — if chains look short
    even when over-counted, the sparsity assumption holds.
    """
    lengths: list[int] = []
    run = 0
    for i in range(n_units):
        if i in closure_hits:
            run += 1
        elif run:
            lengths.append(run)
            run = 0
    if run:
        lengths.append(run)
    return lengths


def pct(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, round(q * (len(s) - 1)))
    return float(s[k])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--domain", default="legal")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--sample", type=int, default=30,
                    help="how many marker hits to dump for manual precision check")
    args = ap.parse_args()

    pack = get_pack(args.domain)
    files = sorted(p for p in args.corpus.rglob("*") if p.suffix.lower() in {".txt", ".md"})
    if not files:
        print(f"no .txt/.md files under {args.corpus}", file=sys.stderr)
        return 1

    total_units = 0
    total_closure = 0
    total_context = 0
    all_chains: list[int] = []
    markers: Counter[str] = Counter()
    definitions = 0
    citations = 0
    gated_in = 0
    samples: list[tuple[str, str]] = []
    per_doc: list[dict] = []

    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        units = split_units(text)
        if not units:
            continue
        r = scan(units, pack)

        total_units += len(units)
        total_closure += len(r["closure_hits"])
        total_context += len(r["context_hits"])
        all_chains += chain_lengths(r["closure_hits"], len(units))
        markers += r["marker_counts"]
        definitions += r["definitions"]
        citations += r["citations"]
        gated_in += sum(1 for u in units if pack.should_run_l3(u))

        for i, names in list(r["closure_hits"].items())[:2]:
            if len(samples) < args.sample:
                samples.append((",".join(names), units[i][:220]))

        per_doc.append({
            "file": f.name,
            "units": len(units),
            "closure_units": len(r["closure_hits"]),
            "closure_density": round(len(r["closure_hits"]) / len(units), 4),
        })

    closure_density = total_closure / total_units if total_units else 0.0
    context_density = total_context / total_units if total_units else 0.0
    p95 = pct(all_chains, 0.95)
    gate_rate = gated_in / total_units if total_units else 0.0

    print(f"\n=== Phase 0: closure density — domain={pack.name} ===")
    print(f"documents                {len(per_doc)}")
    print(f"units                    {total_units}")
    print(f"units w/ closure marker  {total_closure}  ({closure_density:.1%})")
    print(f"units w/ context marker  {total_context}  ({context_density:.1%})")
    print(f"definition sites         {definitions}")
    print(f"citation-bearing units   {citations}")
    print(f"chain length  mean/p95   {statistics.mean(all_chains) if all_chains else 0:.2f} / {p95:.0f}")
    print(f"L3 cost gate admits      {gate_rate:.1%} of units")

    print("\ntop markers:")
    for name, c in markers.most_common(18):
        print(f"  {c:6d}  {name}")

    print("\n--- PASS/FAIL ---")
    chain_ok = p95 <= 3
    ratio = (context_density / closure_density) if closure_density else float("inf")
    sparse_ok = closure_density <= 0.25
    print(f"[{'PASS' if chain_ok else 'FAIL'}] closure chain p95 <= 3           (got {p95:.0f})")
    print(f"[{'PASS' if sparse_ok else 'FAIL'}] closure density <= 25%           (got {closure_density:.1%})")
    print(f"       context:closure density ratio     {ratio:.1f}x")
    if not (chain_ok and sparse_ok):
        print("\n>> Closure edges are NOT sparse in this corpus.")
        print(">> Do not proceed to Phase 3 as designed. Revisit traversal budgeting.")

    print(f"\n--- hand-check these {len(samples)} hits for precision (target >= 0.8) ---")
    for name, snippet in samples:
        print(f"\n[{name}]\n  {snippet}")

    if args.json:
        args.json.write_text(json.dumps({
            "domain": pack.name,
            "documents": len(per_doc),
            "units": total_units,
            "closure_density": closure_density,
            "context_density": context_density,
            "chain_p95": p95,
            "gate_rate": gate_rate,
            "markers": dict(markers),
            "per_doc": per_doc,
        }, indent=2))
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
