#!/usr/bin/env python3
"""Answerability pass over built hard cases.

Split out of `build_hard_cases.py` deliberately. Selection must stay blind to
arm C and is expensive (a rerank over every candidate); judging is independent
of selection, needs a model, and is cheap. Keeping them separate means a run
that had no API key at selection time can be judged later without repeating the
rerank pass -- which is exactly what happened here.

The judge answers one question: does this gold span actually change, qualify or
condition the answer to its question? A case where it does not is not a
retrieval failure worth measuring -- the graph would be "rescuing" a passage the
reader never needed.

The model sees the question and the gold TOGETHER here, which is fine: the
question is already fixed and filtered by this point, so there is nothing left
for it to leak into.

Usage:
    python scripts/judge_hard_cases.py --in corpus/failure_taxonomy_hard.jsonl \
        --out corpus/failure_taxonomy_hard.judged.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dge.adapters.extract_llm import ExtractorError
from dge.adapters.llm_text import TextCompleter

SYSTEM = (
    "You judge whether a passage of legislation affects the answer to a "
    "question. You reply with exactly one word: YES or NO."
)

JUDGE = """A reader asked this question about {act}:

{question}

Here is a passage from that Act:

{gold}

Does this passage change, qualify, limit or condition the correct answer to \
that question? Reply with exactly one word: YES or NO."""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--out", dest="dst", type=Path, required=True)
    ap.add_argument("--pace", type=float, default=2.0)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    cases = [json.loads(line) for line in
             args.src.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"judging {len(cases)} cases", flush=True)

    llm = TextCompleter(args.model, min_interval_s=args.pace) if args.model \
        else TextCompleter(min_interval_s=args.pace)

    kept: list[dict[str, str]] = []
    rejected = errors = 0
    for n, case in enumerate(cases, 1):
        act = case["source_file"].replace("_", " ").removesuffix(".txt")
        try:
            verdict = llm.complete(
                SYSTEM,
                JUDGE.format(act=act, question=case["query"],
                             gold=case["gold_span"][:1500]),
            )
        except ExtractorError as exc:
            # Never silently drop a case on an infrastructure failure: an
            # unjudged case is kept and labelled, not discarded as a NO.
            errors += 1
            case["why_it_fails"] += "  [judge unavailable: not verified]"
            kept.append(case)
            print(f"  {n:3d} ERROR (kept, unverified): {str(exc)[:80]}", flush=True)
            continue

        if verdict.strip().upper().startswith("YES"):
            kept.append(case)
        else:
            rejected += 1
            print(f"  {n:3d} REJECTED: {case['query'][:72]}", flush=True)

    args.dst.write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in kept),
        encoding="utf-8",
    )
    print(f"\nkept {len(kept)}  rejected {rejected}  judge-errors {errors}")
    print(f"wrote {args.dst}")
    print("\nThe judge is a filter, not a substitute for review: it says the gold "
          "is relevant, not that a lawyer would accept it as the answer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
