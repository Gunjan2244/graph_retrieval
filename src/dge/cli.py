"""CLI entry point.

    dge ingest FILE [FILE...] -o bundle.sqlite
    dge query "..." --bundle bundle.sqlite

`ingest` is the writer BUILD_PLAN Phase 4 calls the "bundle writer" — insert
source documents, get back a single-file SQLite bundle carrying original bytes,
substrate, terms, and edges (docs/02-architecture.md 2.3).

`query` is the read side: seed, expand closure to a fixed point, expand context
on a budget, assemble in document order, and report a soundness verdict. It
prints CONTEXT, not an answer — supplying context rather than answers is a
deliberate product boundary (docs/01 §1.3).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dge.adapters.extract_llm import DEFAULT_MODEL as EXTRACT_DEFAULT_MODEL
from dge.pipeline import ingest_documents

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dge.bundle import BundleGraph
    from dge.interfaces import Reranker
    from dge.l3.conflict import OverrideConflict
    from dge.query import Seeder


def _cmd_ingest(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.files]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"error: not a file: {p}", file=sys.stderr)
        return 2

    summary = ingest_documents(
        paths,
        domain=args.domain,
        tenant_id=args.tenant,
        out_path=Path(args.output),
        acl_tag=args.acl_tag,
    )

    print(f"bundle written: {summary.bundle_path}")
    print(f"  documents        {summary.documents}"
          f"  ({summary.documents_review_pending} pending review)")
    print(f"  nodes            {summary.nodes}")
    print(f"  terms            {summary.terms}")
    print(f"  edges            {summary.edges}"
          f"  ({summary.closure_edges} closure / {summary.edges - summary.closure_edges} context)")
    if summary.warnings:
        print(f"  warnings         {len(summary.warnings)}")
        for w in summary.warnings[:20]:
            print(f"    - {w}")
        if len(summary.warnings) > 20:
            print(f"    ... and {len(summary.warnings) - 20} more")

    if summary.documents_review_pending:
        print(
            f"\n{summary.documents_review_pending} document(s) failed the parse-confidence "
            "gate and were written with review_state='pending' — no L1/lexicon/edge layers "
            "ran for them (CLAUDE.md invariant 9).",
            file=sys.stderr,
        )
    return 0


def _make_hybrid_seeder(graph: BundleGraph) -> Seeder | None:
    """Dense (bundle vectors) + sparse (lexical) -> RRF, as a `query.Seeder`.

    Returns None if the bundle has no vectors written (`dge embed` never ran)
    so the caller can fall back rather than error — L2 is optional by design.
    Picks the query-time embedder from the stored `model_id` prefix so the
    query is embedded by the SAME model that produced the document vectors;
    embedding a query with a different model than its corpus is silent
    nonsense (the vector spaces are not comparable) so this is not optional.
    """
    from dge.interfaces import Embedder
    from dge.retrieval.hybrid import fuse_seeds
    from dge.retrieval.lexical import LexicalIndex

    model_ids = graph.vector_model_ids()
    if not model_ids:
        return None
    manifest_model = model_ids[0]

    embedder: Embedder
    if manifest_model.startswith("fastembed:"):
        from dge.adapters.embed_local import FastEmbedEmbedder
        embedder = FastEmbedEmbedder(model_name=manifest_model.split(":", 1)[1])
    elif manifest_model.startswith("voyage:"):
        from dge.adapters.embed_hosted import VoyageEmbedder
        embedder = VoyageEmbedder(model_name=manifest_model.split(":", 1)[1])
    else:
        print(f"warning: unrecognized vector model_id {manifest_model!r}; "
              "cannot embed the query with it", file=sys.stderr)
        return None

    def seed(query: str, top_k: int) -> list[str]:
        nodes = graph.all_nodes()
        sparse = [s.node_id for s in LexicalIndex(nodes).search(query, top_k=top_k * 3)]
        query_vec = embedder.embed_query(query)
        dense = [nid for nid, _sim in
                graph.search_vectors(query_vec, model_id=manifest_model, top_k=top_k * 3)]
        return fuse_seeds(dense=dense, sparse=sparse, top_k=top_k)

    return seed


def _make_lexical_seeder(graph: BundleGraph) -> Seeder:
    """Plain lexical `Seeder`, for `--rerank` to wrap when `--use-vectors`
    wasn't also passed — reranking is an independent flag, not a variant of
    hybrid seeding, so it needs a base seeder even in the lexical-only case."""
    from dge.retrieval.lexical import LexicalIndex

    def seed(query: str, top_k: int) -> list[str]:
        nodes = graph.all_nodes()
        return [s.node_id for s in LexicalIndex(nodes).search(query, top_k=top_k)]

    return seed


def _make_reranker(provider: str, model: str | None) -> Reranker:
    if provider == "local":
        from dge.adapters.rerank_local import DEFAULT_MODEL, FastEmbedReranker
        return FastEmbedReranker(model_name=model or DEFAULT_MODEL)
    from dge.adapters.rerank_hosted import DEFAULT_MODEL, VoyageReranker
    return VoyageReranker(model_name=model or DEFAULT_MODEL)


def _cmd_query(args: argparse.Namespace) -> int:
    from dge.bundle import open_bundle
    from dge.domains.legal import get_pack
    from dge.query import rerank_seeder, run_query
    from dge.traversal.policy import Budget

    bundle_path = Path(args.bundle)
    if not bundle_path.is_file():
        print(f"error: no bundle at {bundle_path}", file=sys.stderr)
        return 2

    with open_bundle(bundle_path) as graph:
        seeder: Seeder | None = None
        if args.use_vectors:
            seeder = _make_hybrid_seeder(graph)
            if seeder is None:
                print("warning: --use-vectors set but bundle has no vectors "
                      "(run `dge embed` first); falling back to lexical-only seeding",
                      file=sys.stderr)

        if args.rerank:
            base_seeder = seeder or _make_lexical_seeder(graph)
            reranker = _make_reranker(args.rerank_provider, args.rerank_model)
            seeder = rerank_seeder(base_seeder, graph, reranker)

        result = run_query(
            graph,
            args.query,
            top_k=args.top_k,
            budget=Budget(max_tokens=args.max_tokens),
            min_closure_confidence=args.min_confidence,
            glosses=graph.glossary() if args.glosses else None,
            seeder=seeder,
            pack=get_pack(args.domain),
        )

        print(f"query: {result.query}")
        print(f"  seeds       {len(result.seeds)}")
        print(f"  closure     {len(result.closure.expanded_only)} added "
              f"(unbudgeted, to fixed point)")
        print(f"  context     {len(result.context.reached)} added "
              f"({result.context.stopped_because}, {result.context.tokens_used} tok)")
        if result.context.edge_types_fired:
            print(f"  edges fired {', '.join(result.context.edge_types_fired)}")
        print(f"  assembled   {len(result.assembled.node_ids)} nodes / "
              f"{result.assembled.tokens} tokens")
        print(f"  soundness   {result.soundness.summary()}")
        _print_conflicts(result.conflicts)

        if result.closure.skipped:
            print(f"\n  {len(result.closure.skipped)} closure edge(s) not followed:")
            for s in result.closure.skipped[:10]:
                print(f"    - {s.edge.type.value} -> {s.neighbor_id}: {s.reason}")

        if args.show_provenance:
            print("\n--- node provenance (seed / closure / context) ---")
            for node_id in result.assembled.node_ids:
                print(f"  {result.provenance_of(node_id):8s}  {node_id}")

        print("\n--- assembled context ---\n")
        print(result.assembled.text)

    return 0


def _cmd_embed(args: argparse.Namespace) -> int:
    from dge.interfaces import Embedder
    from dge.pipeline import embed_bundle

    bundle_path = Path(args.bundle)
    if not bundle_path.is_file():
        print(f"error: no bundle at {bundle_path}", file=sys.stderr)
        return 2

    embedder: Embedder
    if args.provider == "local":
        from dge.adapters.embed_local import DEFAULT_MODEL, FastEmbedEmbedder
        embedder = FastEmbedEmbedder(model_name=args.model or DEFAULT_MODEL)
    else:
        from dge.adapters.embed_hosted import DEFAULT_MODEL, VoyageEmbedder
        embedder = VoyageEmbedder(model_name=args.model or DEFAULT_MODEL)

    summary = embed_bundle(bundle_path, embedder)
    print(f"embedded: {summary.model_id}")
    print(f"  documents  {summary.documents_embedded}")
    print(f"  nodes      {summary.nodes_embedded}")
    if summary.nodes_skipped:
        print(f"  skipped    {summary.nodes_skipped} (already had this model's vectors)")
    if summary.warnings:
        print(f"  warnings   {len(summary.warnings)}")
        for w in summary.warnings[:20]:
            print(f"    - {w}")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from dge.pipeline import extract_bundle, plan_extraction

    bundle_path = Path(args.bundle)
    if not bundle_path.is_file():
        print(f"error: no bundle at {bundle_path}", file=sys.stderr)
        return 2

    if args.dry_run:
        gate, conflicts = plan_extraction(bundle_path, domain=args.domain)
        print("dry run — no model called, no key needed")
        print(f"  sections        {gate.sections_total}")
        print(f"  would call      {gate.sections_admitted}  "
              f"({gate.admit_fraction:.1%} of sections)")
        print(f"  input chars     {gate.chars_admitted:,} / {gate.chars_total:,}  "
              f"({gate.char_fraction:.1%} — this is the bill)")
        _print_conflicts(conflicts)
        return 0

    from dge.adapters.extract_llm import LiteLLMEdgeExtractor

    summary = extract_bundle(
        bundle_path,
        LiteLLMEdgeExtractor(
            model=args.model, temperature=args.temperature, min_interval_s=args.pace
        ),
        domain=args.domain,
    )
    print(f"extracted: {summary.model_id}")
    print(f"  documents       {summary.documents}")
    print(f"  gate            {summary.gate.sections_admitted}/{summary.gate.sections_total} "
          f"sections admitted ({summary.gate.char_fraction:.1%} of characters)")
    print(f"  model calls     {summary.calls}")
    print(f"  edges written   {summary.edges_written}")
    print(f"  discarded       {summary.candidates_rejected}  "
          f"(evidence span, window, or type check failed)")
    print(f"  pattern edges   {summary.verified} verified / {summary.unconfirmed} unconfirmed")
    _print_conflicts(summary.conflicts)
    if summary.failures:
        print(f"\n  {len(summary.failures)} section(s) failed:", file=sys.stderr)
        for failure in summary.failures[:10]:
            print(f"    - {failure}", file=sys.stderr)
    return 0


def _print_conflicts(conflicts: Sequence[OverrideConflict]) -> None:
    if not conflicts:
        return
    print(f"\n  {len(conflicts)} override conflict(s) FLAGGED — not resolved:")
    for conflict in conflicts[:10]:
        print(f"    - {conflict.describe()}")
    if len(conflicts) > 10:
        print(f"    ... and {len(conflicts) - 10} more")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dge", description="Document Graph Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest one or more documents into a bundle")
    ingest.add_argument("files", nargs="+", help="Source document paths (.txt / .md)")
    ingest.add_argument("-o", "--output", required=True, help="Output bundle path (.sqlite)")
    ingest.add_argument("--domain", default="legal", help="Domain pack to use (default: legal)")
    ingest.add_argument("--tenant", default="default", help="Tenant id to stamp on ingested documents")
    ingest.add_argument("--acl-tag", default=None, help="Matter/client/deal scope within the tenant")
    ingest.set_defaults(func=_cmd_ingest)

    query = sub.add_parser(
        "query", help="Query a bundle: seed, expand, assemble, verify soundness"
    )
    query.add_argument("query", help="The question to assemble context for")
    query.add_argument("-b", "--bundle", required=True, help="Bundle path (.sqlite)")
    query.add_argument("--domain", default="legal", help="Domain pack (default: legal)")
    query.add_argument("-k", "--top-k", type=int, default=8, help="Seed set size")
    query.add_argument("--max-tokens", type=int, default=8000,
                       help="Token budget for CONTEXT expansion (closure is unbudgeted)")
    query.add_argument("--min-confidence", type=float, default=0.0,
                       help="Drop closure edges below this confidence; skipped ones are "
                            "reported, never silently dropped")
    query.add_argument("--glosses", action="store_true",
                       help="Splice term glosses inline into the assembled context")
    query.add_argument("--show-provenance", action="store_true",
                       help="Print seed/closure/context origin for every assembled node")
    query.add_argument("--use-vectors", action="store_true",
                       help="Seed with dense+sparse hybrid (RRF) instead of lexical-only; "
                            "requires `dge embed` to have run against this bundle first")
    query.add_argument("--rerank", action="store_true",
                       help="Cross-encoder rerank the seed candidates (docs/06 §6.3's "
                            "'hybrid + rerank' baseline). Independent of --use-vectors: "
                            "wraps lexical-only seeding if that flag isn't also set.")
    query.add_argument("--rerank-provider", choices=["local", "hosted"], default="local",
                       help="local = fastembed cross-encoder, offline, no key. "
                            "hosted = Voyage AI rerank API, needs VOYAGE_API_KEY. Default: local")
    query.add_argument("--rerank-model", default=None,
                       help="Override the rerank provider's default model")
    query.set_defaults(func=_cmd_query)

    embed = sub.add_parser("embed", help="Compute and write L2 vectors into an existing bundle")
    embed.add_argument("-b", "--bundle", required=True, help="Bundle path (.sqlite)")
    embed.add_argument("--provider", choices=["local", "hosted"], default="local",
                       help="local = fastembed, offline, no key. "
                            "hosted = Voyage AI, needs VOYAGE_API_KEY. Default: local")
    embed.add_argument("--model", default=None, help="Override the provider's default model")
    embed.set_defaults(func=_cmd_embed)

    extract = sub.add_parser(
        "extract", help="L3: run the model edge extractor over an existing bundle"
    )
    extract.add_argument("-b", "--bundle", required=True, help="Bundle path (.sqlite)")
    extract.add_argument("--model", default=EXTRACT_DEFAULT_MODEL,
                         help="LiteLLM model string. Provider is config, not a code path: "
                              "groq/... (GROQ_API_KEY), gemini/... (GEMINI_API_KEY), "
                              "ollama/... (no key). Default: %(default)s")
    extract.add_argument("--temperature", type=float, default=0.0)
    extract.add_argument("--pace", type=float, default=0.0, metavar="SECONDS",
                         help="Minimum seconds between model calls. A free-tier "
                              "tokens-per-minute cap (Groq: 8000) is hit by burst, not "
                              "daily volume, and a 429 is recorded as a failed section, "
                              "never retried — so pacing, not retry, is how to stay "
                              "under it. Try 12-15 for the Groq free tier; 0 = no pacing.")
    extract.add_argument("--domain", default="legal", help="Domain pack (default: legal)")
    extract.add_argument("--dry-run", action="store_true",
                         help="Report what the cost gate would admit and what the "
                              "deterministic layers already found. No model, no key, "
                              "no network.")
    extract.set_defaults(func=_cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
