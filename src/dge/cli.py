"""CLI entry point: `dge ingest FILE [FILE...] -o bundle.sqlite`.

This is the product surface BUILD_PLAN Phase 4 calls the "bundle writer" —
insert one or more source documents, get back a single-file SQLite bundle
carrying original bytes, substrate, terms, and edges (docs/02-architecture.md
2.3). See `dge.pipeline` for what actually runs at each stage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dge.pipeline import ingest_documents


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
