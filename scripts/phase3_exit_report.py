#!/usr/bin/env python3
"""Measure BUILD_PLAN Phase 3's exit criterion. No model, no key, no network.

Phase 3 exits on two things, and they are not the same kind of claim:

  1. "Soundness violation rate = 0."  A property of TRAVERSAL. Measured here
     over hundreds of real queries, with a falsification alongside it: the
     identical check run against a seeds-only context. If the flat-RAG arm is
     not massively unsound, the check is vacuous and the zero means nothing.

  2. "Exception recall and stale-answer rate improve measurably over the
     Phase 1 baseline."  A property of EXTRACTION. Measured on the labeled
     failure set in `corpus/failure_taxonomy.jsonl` with three arms over the
     SAME seeds, so the only variable is what traversal adds:

        A  seeds only                 docs/06 6.3 baseline 2 — the honest bar
        B  seeds + CONTEXT expansion  budgeted, non-closure
        C  seeds + CLOSURE + CONTEXT  the full pipeline

     Reported as the three-way split docs/06 6.1 demands — gold in the seed /
     in the expansion / never reached — because "never reached" points at the
     extractor and "in the expansion" points at the budgets, and the final
     answer alone cannot tell them apart.

A third section counts how many exception-shaped nodes in the corpus have any
outgoing closure edge at all. Closure runs on the reverse index, so a carve-out
with no outgoing edge is invisible from the rule it modifies however well the
traversal works — which makes this the recall ceiling that arm C is measured
against.

    python scripts/phase3_exit_report.py --bundle b.sqlite --corpus corpus/indian-acts
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dge.bundle import open_bundle
from dge.cli import _make_hybrid_seeder
from dge.domains.legal import get_pack
from dge.edges import extract_marker_edges, extract_structural_edges
from dge.lexicon import extract_terms, link_mentions
from dge.model import EdgeType, NodeKind
from dge.parsing import PlainTextParser, finalize_doc_id
from dge.query import rerank_seeder, run_query, verify_answer
from dge.retrieval.lexical import LexicalIndex
from dge.traversal.expand import context_frontier
from dge.traversal.policy import Budget

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dge.bundle import BundleGraph
    from dge.model import Node
    from dge.query import Seeder

SEEDING_MODES = ("lexical", "hybrid", "hybrid-rerank")


def build_seeder(graph: BundleGraph, nodes: Sequence[Node], mode: str) -> Seeder:
    """The seed stage for every arm, chosen by `mode`.

    docs/06 §6.3 names "normalized nodes + hybrid + rerank, no traversal" as the
    baseline the graph must beat — so the honest way to ask whether closure
    traversal earns its place is to give arm A that same seeder, not the weaker
    lexical-only one. All three arms share whatever this returns; the only
    variable between them stays what traversal adds.

    Reuses the product CLI's own `_make_hybrid_seeder` / `rerank_seeder` rather
    than a parallel implementation, so the eval measures the seeding path that
    actually ships.
    """
    if mode == "lexical":
        shared = LexicalIndex(nodes)
        return lambda q, k: [s.node_id for s in shared.search(q, k)]
    hybrid = _make_hybrid_seeder(graph)
    if hybrid is None:
        raise SystemExit(
            "error: --seeding "
            f"{mode} needs vectors in the bundle; run `dge embed -b <bundle>` first"
        )
    if mode == "hybrid":
        return hybrid
    from dge.adapters.rerank_local import DEFAULT_MODEL, FastEmbedReranker

    return rerank_seeder(hybrid, graph, FastEmbedReranker(model_name=DEFAULT_MODEL))

_WS = re.compile(r"\s+")

# Exception-shaped text, split by where the target lives, because the two fail
# for different reasons and only one of them is currently handled well.
CROSS_REF = re.compile(
    r"(?:nothing\s+(?:contained\s+)?in|except\s+as\s+(?:otherwise\s+)?provided\s+(?:in|by)|"
    r"save\s+as\s+(?:otherwise\s+)?provided\s+(?:in|by))"
    r"\s+(?:the\s+)?(?:sub-)?(?:sections?|secs?|clauses?|chapters?)\b\s*\.?\s*\d", re.IGNORECASE)
SELF_REF = re.compile(
    r"(?:^|\W)(?:provided\s+that|nothing\s+(?:contained\s+)?in\s+this\s+"
    r"(?:section|Act|Chapter|sub-section|rule)|save\s+as\s+(?:otherwise\s+)?provided|"
    r"except\s+as\s+(?:otherwise\s+)?provided)\b", re.IGNORECASE)
CLOSURE_TYPES = {EdgeType.EXCEPTION_OF, EdgeType.SUPERSEDES, EdgeType.AMENDS,
                 EdgeType.CONDITIONED_ON}


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def gold_nodes(gold: str, nodes: Sequence[Node]) -> list[str]:
    """Nodes whose raw text overlaps the gold span substantively.

    Gold spans are verbatim slices of the source file and routinely cross node
    boundaries — that is the point of several of the labeled cases — so the
    relation is overlap, not equality.
    """
    g = _norm(gold)
    hits: list[str] = []
    for node in nodes:
        raw = _norm(node.raw)
        if len(raw) < 25:
            continue
        if raw in g or g in raw:
            hits.append(node.node_id)
            continue
        for probe in (raw[:80], raw[-80:]):
            if len(probe) >= 60 and probe in g:
                hits.append(node.node_id)
                break
    return hits


def labeled_failure_arms(bundle: Path, taxonomy: Path, top_k: int,
                         seeding: str = "lexical") -> None:
    cases = [json.loads(line) for line in taxonomy.read_text().splitlines() if line.strip()]
    pack = get_pack("legal")
    tally: Counter[str] = Counter()
    rows: list[tuple[str, str, str, str, bool, str]] = []

    with open_bundle(bundle) as graph:
        nodes = graph.all_nodes()
        present = {Path(d.source_uri or d.doc_id).name for d in graph.documents()}
        index = LexicalIndex(nodes)
        seeder = build_seeder(graph, nodes, seeding)

        for case in cases:
            if case["source_file"] not in present:
                tally["not_in_bundle"] += 1
                continue
            gold = set(gold_nodes(case["gold_span"], nodes))
            query = case["query"]
            seeds = tuple(seeder(query, top_k))

            ctx_only = context_frontier(
                graph, seeds=seeds, already_included=seeds,
                query_relevance=index.relevance(query), budget=Budget())
            arm_b = set(seeds) | set(ctx_only.reached)
            result = run_query(graph, query, nodes=nodes, top_k=top_k,
                               seeder=lambda _q, _k, s=seeds: list(s), pack=pack)
            arm_c = set(result.assembled.node_ids)

            def where(pool: set[str], _gold: set[str] = gold, _s: tuple[str, ...] = seeds) -> str:
                if not _gold:
                    return "no-gold-node"
                if _gold & set(_s):
                    return "seed"
                return "expansion" if _gold & pool else "never"

            a, b, c = where(set(seeds)), where(arm_b), where(arm_c)
            sound = verify_answer(graph, cited_node_ids=seeds,
                                  context_node_ids=result.assembled.node_ids)
            for arm, verdict in (("A", a), ("B", b), ("C", c)):
                if verdict in ("seed", "expansion"):
                    tally[f"{arm}_reached"] += 1
                    tally[f"{arm}_reached_{case['cause']}"] += 1
            tally["cases"] += 1
            tally[f"cases_{case['cause']}"] += 1
            if not sound.ok:
                tally["unsound"] += 1
            rows.append((case["cause"], a, b, c, sound.ok, query))

    print(f"== labeled failure set (corpus/failure_taxonomy.jsonl) — seeding: {seeding} ==\n")
    print(f"{'cause':22} {'A seeds':10} {'B +ctx':10} {'C +closure':10} sound  query")
    for cause, a, b, c, ok, query in rows:
        print(f"{cause:22} {a:10} {b:10} {c:10} {ok!s:6} {query[:52]}")
    n = tally["cases"]
    print(f"\ngold reached — A {tally['A_reached']}/{n}   "
          f"B {tally['B_reached']}/{n}   C {tally['C_reached']}/{n}")
    for cause in sorted({r[0] for r in rows}):
        cn = tally[f"cases_{cause}"]
        print(f"   {cause:22} A {tally[f'A_reached_{cause}']}/{cn}   "
              f"B {tally[f'B_reached_{cause}']}/{cn}   C {tally[f'C_reached_{cause}']}/{cn}")
    print(f"\nsoundness violations on the labeled set: {tally['unsound']}/{n}")
    if tally["not_in_bundle"]:
        print(f"({tally['not_in_bundle']} labeled case(s) skipped — document not in this bundle)")


def soundness_sweep(bundle: Path, limit: int, seeding: str = "lexical") -> None:
    pack = get_pack("legal")
    with open_bundle(bundle) as graph:
        nodes = graph.all_nodes()
        seeder = build_seeder(graph, nodes, seeding)
        headings = [" ".join(n.raw.split()) for n in nodes
                    if n.kind is NodeKind.STRUCTURAL and 12 < len(n.raw) < 120]
        queries = random.Random(20260821).sample(headings, min(limit, len(headings)))

        flat_unsound = full_unsound = flat_violations = 0
        for query in queries:
            result = run_query(graph, query, nodes=nodes, top_k=8, pack=pack, seeder=seeder)
            # Flat RAG: the model sees only what retrieval ranked.
            flat = verify_answer(graph, cited_node_ids=result.seeds,
                                 context_node_ids=result.seeds)
            full = verify_answer(graph, cited_node_ids=result.seeds,
                                 context_node_ids=result.assembled.node_ids)
            flat_unsound += not flat.ok
            flat_violations += len(flat.violations)
            full_unsound += not full.ok

    total = len(queries)
    print(f"\n== soundness sweep ({total} queries) ==\n")
    print(f"flat-RAG context (seeds only)  UNSOUND {flat_unsound}/{total} "
          f"= {flat_unsound / max(total, 1):.1%}  ({flat_violations} violations)")
    print(f"full traversal context         UNSOUND {full_unsound}/{total} "
          f"= {full_unsound / max(total, 1):.1%}")
    print("\nThe first line is the falsification: without it a zero on the second\n"
          "line would only prove the check never fires.")


def exception_linkage(corpus: Path) -> None:
    pack = get_pack("legal")
    parser = PlainTextParser()
    counts: Counter[str] = Counter()
    unlinked_cross: list[str] = []

    for path in sorted(corpus.glob("*.txt")):
        result = parser.parse(path.read_bytes())
        if result.confidence < 0.5:
            counts["gated_out"] += 1
            continue
        counts["docs"] += 1
        nodes, struct = finalize_doc_id(path.stem, list(result.nodes),
                                        list(result.structural_edges))
        edges = [*struct,
                 *extract_structural_edges(nodes, pack, struct),
                 *extract_marker_edges(nodes, pack, struct)[0],
                 *link_mentions(nodes, extract_terms(nodes, pack))]
        out_closure = {e.src for e in edges if e.type in CLOSURE_TYPES}
        for node in nodes:
            if node.kind in (NodeKind.STRUCTURAL, NodeKind.FOOTNOTE):
                continue
            text = " ".join(node.raw.split())
            bucket = "cross" if CROSS_REF.search(text) else (
                "self" if SELF_REF.search(text) else None)
            if bucket is None:
                continue
            counts[f"{bucket}_total"] += 1
            if node.node_id in out_closure:
                counts[f"{bucket}_linked"] += 1
            elif bucket == "cross" and len(unlinked_cross) < 10:
                unlinked_cross.append(f"[{path.stem[:36]:36}] {text[:110]}")

    print(f"\n== exception linkage over {counts['docs']} acts "
          f"({counts['gated_out']} gated out) ==\n")
    for bucket, label in (("self", "SELF-REFERENTIAL  proviso / 'in this section'"),
                          ("cross", "CROSS-REFERENCE   'nothing in section 28'")):
        total, linked = counts[f"{bucket}_total"], counts[f"{bucket}_linked"]
        print(f"{label}")
        print(f"   exception-shaped nodes          {total}")
        print(f"   with an outgoing closure edge   {linked} ({linked / max(total, 1):.0%})")
        print(f"   INVISIBLE to reverse traversal  {total - linked} "
              f"({(total - linked) / max(total, 1):.0%})\n")
    if unlinked_cross:
        print("unlinked cross-reference carve-outs:")
        for line in unlinked_cross:
            print(f"  {line}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--corpus", type=Path, default=Path("corpus/indian-acts"))
    ap.add_argument("--taxonomy", type=Path, default=Path("corpus/failure_taxonomy.jsonl"))
    ap.add_argument("--queries", type=int, default=300, help="soundness sweep size")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--seeding", choices=SEEDING_MODES, default="lexical",
                    help="seed stage shared by all three arms (docs/06 §6.3). "
                         "hybrid / hybrid-rerank need `dge embed` to have run. "
                         "Default: lexical")
    args = ap.parse_args(argv)

    if not args.bundle.is_file():
        print(f"error: no bundle at {args.bundle}", file=sys.stderr)
        return 2

    labeled_failure_arms(args.bundle, args.taxonomy, args.top_k, args.seeding)
    soundness_sweep(args.bundle, args.queries, args.seeding)
    if args.corpus.is_dir():
        exception_linkage(args.corpus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
