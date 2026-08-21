#!/usr/bin/env python3
"""Measure the L3 cost gate against the real corpus.

BUILD_PLAN Phase 3: "Cost gate: `pack.should_run_l3(text)` before any L3 call."
This script answers the only question that matters about it — *what fraction of
the corpus does it admit* — because that fraction is very nearly the ingest
bill. Phase 0 measured 22.3% on the OLD substrate; the parser has since been
rewritten at ~10x granularity (PARSER_PLAN.md), so that number is not
transferable and this re-measures rather than trusting it.

Reports admission at two granularities, and they answer different questions:

  - by SECTION  — how many model CALLS get made (rate limits, wall clock).
  - by CHARACTER — how many input TOKENS get paid for (the actual bill).

Character share is the one to quote. A gate that skips many tiny sections while
admitting every long one saves nothing.

Also counts competing non obstante claims, since that is the other Phase 3
question the corpus can answer without a model.

    python scripts/phase3_gate_report.py --corpus corpus/indian-acts
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dge.domains.legal import get_pack
from dge.l3.conflict import detect_override_conflicts, override_claims
from dge.l3.sections import apply_gate, group_sections
from dge.model import NodeKind
from dge.parsing import PlainTextParser, finalize_doc_id
from dge.pipeline import PARSE_CONFIDENCE_THRESHOLD


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default="corpus/indian-acts")
    ap.add_argument("--domain", default="legal")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    pack = get_pack(args.domain)
    parser = PlainTextParser()
    files = sorted(p for p in Path(args.corpus).rglob("*") if p.suffix in {".txt", ".md"})
    if not files:
        print(f"no .txt/.md under {args.corpus}", file=sys.stderr)
        return 2

    tot_nodes = tot_nodes_admitted = 0
    tot_node_chars = tot_node_chars_admitted = 0
    tot_sections = tot_admitted = 0
    tot_chars = tot_chars_admitted = 0
    per_doc_admit: list[float] = []
    reviewed_out = 0
    gate_term_hits: Counter[str] = Counter()
    sole_admitting_term: Counter[str] = Counter()
    conflict_rows: list[dict[str, object]] = []
    claim_kinds: Counter[str] = Counter()

    for path in files:
        result = parser.parse(path.read_bytes(), doc_class=args.domain)
        if result.confidence < PARSE_CONFIDENCE_THRESHOLD:
            # Invariant 9: a document that never cleared the substrate gate
            # never reaches L3, so it must not be counted in L3's economics.
            reviewed_out += 1
            continue
        nodes, _edges = finalize_doc_id(path.stem, list(result.nodes), list(result.structural_edges))

        # Node granularity, for comparability with the Phase 0 number: that
        # was measured per unit, and units were what the OLD parser emitted.
        for node in nodes:
            if node.kind is not NodeKind.PROPOSITION:
                continue
            tot_nodes += 1
            tot_node_chars += len(node.raw)
            if pack.should_run_l3(node.raw):
                tot_nodes_admitted += 1
                tot_node_chars_admitted += len(node.raw)

        sections = group_sections(nodes)
        admitted, report = apply_gate(sections, pack)
        tot_sections += report.sections_total
        tot_admitted += report.sections_admitted
        tot_chars += report.chars_total
        tot_chars_admitted += report.chars_admitted
        if report.sections_total:
            per_doc_admit.append(report.admit_fraction)

        for section in admitted:
            low = section.evidence_window.lower()
            hits = [t for t in pack.gate_terms if t in low]
            for term in hits:
                gate_term_hits[term] += 1
            if len(hits) == 1:
                sole_admitting_term[hits[0]] += 1

        for claim in override_claims(nodes, pack):
            claim_kinds[claim.scope.value] += 1
        for conflict in detect_override_conflicts(nodes, pack):
            conflict_rows.append({
                "doc": path.stem,
                "kind": conflict.kind.value,
                "nodes": len(conflict.node_ids),
            })

    print(f"corpus                 {args.corpus}  ({len(files)} files)")
    print(f"  skipped (review)     {reviewed_out}  (parse confidence < {PARSE_CONFIDENCE_THRESHOLD})")
    print()
    print("L3 COST GATE — pack.should_run_l3")
    print(f"  sections total       {tot_sections}")
    print(f"  sections admitted    {tot_admitted}   ({tot_admitted / tot_sections:.1%} of calls)")
    print(f"  chars total          {tot_chars:,}")
    print(f"  chars admitted       {tot_chars_admitted:,}   "
          f"({tot_chars_admitted / tot_chars:.1%} of input tokens — THE BILL)")
    print()
    print("  same gate at NODE granularity (comparable to the Phase 0 number):")
    print(f"    nodes total        {tot_nodes}")
    print(f"    nodes admitted     {tot_nodes_admitted}   "
          f"({tot_nodes_admitted / tot_nodes:.1%})")
    print(f"    chars admitted     {tot_node_chars_admitted / tot_node_chars:.1%}")
    if per_doc_admit:
        print(f"  per-doc admit rate   median {statistics.median(per_doc_admit):.1%}  "
              f"min {min(per_doc_admit):.1%}  max {max(per_doc_admit):.1%}")
    print()
    print("  gate terms by how many admitted sections contain them:")
    for term, n in gate_term_hits.most_common():
        print(f"    {n:6d}  {term!r}")
    print()
    print("  sections admitted by ONE gate term alone (what tightening it would save):")
    for term, n in sole_admitting_term.most_common(8):
        print(f"    {n:6d}  {term!r}")
    print()
    print("NON OBSTANTE CLAIMS (deterministic, no model)")
    for kind, n in claim_kinds.most_common():
        print(f"  {n:6d}  scope={kind}")
    by_kind: Counter[str] = Counter(str(r["kind"]) for r in conflict_rows)
    print(f"  conflicts flagged    {len(conflict_rows)} across "
          f"{len({r['doc'] for r in conflict_rows})} documents")
    for kind, n in by_kind.most_common():
        print(f"    {n:6d}  {kind}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "files": len(files),
            "skipped_review": reviewed_out,
            "sections_total": tot_sections,
            "sections_admitted": tot_admitted,
            "nodes_total": tot_nodes,
            "nodes_admitted": tot_nodes_admitted,
            "chars_total": tot_chars,
            "chars_admitted": tot_chars_admitted,
            "gate_term_hits": dict(gate_term_hits),
            "claim_kinds": dict(claim_kinds),
            "conflicts": conflict_rows,
        }, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
