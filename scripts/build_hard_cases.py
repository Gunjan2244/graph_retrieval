#!/usr/bin/env python3
"""Build a labeled set of cases the STRONG baseline fails, so the graph's
contribution becomes measurable.

Why this exists (decisions.md 2026-09-05): the existing 15-case set yields only
2 rescues -- cases where hybrid+rerank misses the gold and traversal recovers
it. The exact binomial p-value for 2 rescues is 0.50, a coin flip. The binding
quantity is not total case count but `m`, the number of cases where the strong
baseline FAILS, because only those can carry evidence. The old set has m=3; the
other 12 are already solved by rerank and are dead weight for this question.

So cases are mined, generated, and then FILTERED BY ARM A ONLY.

Three things make LLM-written questions defensible here:

  1. The generator never sees the gold span. It is shown the RULE and its
     heading only, so it cannot echo wording it was never given. This is the
     single most important property in this file -- a question paraphrased from
     the gold is a lexical-overlap test, not a retrieval test.
  2. A coded overlap guard rejects questions that resemble the gold anyway.
  3. Selection consults arm A only. Consulting arm C would choose the cases
     that flatter the graph and manufacture the result being measured.

Usage:
    python scripts/build_hard_cases.py --bundle bundle30.sqlite \
        --corpus corpus/indian-acts --out corpus/failure_taxonomy_hard.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase3_exit_report import build_seeder, gold_nodes

from dge.adapters.extract_llm import ExtractorError
from dge.adapters.llm_text import TextCompleter
from dge.bundle import open_bundle
from dge.model import EdgeType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dge.model import Node

# `defines` is excluded despite being closure: it is 2555 of the 2829 closure
# edges in the 30-act bundle and would swamp every other failure mode. The
# product claim is about exceptions and superseded versions, so those are mined.
MINED_TYPES = {
    EdgeType.EXCEPTION_OF: "lost_exception",
    EdgeType.SUPERSEDES: "wrong_version",
    EdgeType.AMENDS: "wrong_version",
    EdgeType.CONDITIONED_ON: "lost_scope",
}

SYSTEM = (
    "You write realistic questions that a lawyer or compliance officer would "
    "ask about Indian legislation. You write ONE question and nothing else: no "
    "preamble, no quotes, no explanation."
)

# The prompt deliberately carries the rule only. See module docstring.
GENERATE = """Below is a provision from {act}.

{heading}{rule}

Write the single most natural question a reader of this provision would ask -- \
the practical question this provision would be consulted to answer.

Rules:
- Ask about the substance, do not quote the provision.
- Do not mention section numbers.
- One sentence, ending in a question mark.
"""

JUDGE = """A reader asked this question about {act}:

{question}

Here is a passage from that Act:

{gold}

Does this passage change, qualify, limit or condition the correct answer to \
that question? Answer with exactly one word: YES or NO."""

_WORD = re.compile(r"[a-z]{3,}")
_STOP = frozenset(
    ["the", "and", "for", "any", "all", "not", "but", "with", "that", "this", "such", "shall", "may", "his", "her", "its", "been", "have", "has", "was", "were", "are", "is", "be", "of", "in", "to", "on", "by", "or", "as", "at", "from", "under", "which", "who", "whom", "whose", "where", "when", "what", "act", "section", "sub", "clause", "provided", "provision", "person", "state", "government"]
)


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def overlap_ratio(question: str, gold: str) -> float:
    """Share of the question's content words that also appear in the gold span.

    An echoed question tests lexical matching, not retrieval, and would make the
    baseline look artificially good or bad depending on which way it echoed.
    Guarded in code rather than asked for in the prompt (CLAUDE.md invariant 10
    discipline: enforce, do not request).
    """
    q = _content_words(question)
    if not q:
        return 1.0
    return len(q & _content_words(gold)) / len(q)


@dataclass(frozen=True, slots=True)
class Candidate:
    gold_node: Node
    rule_node: Node
    cause: str
    act: str
    source_file: str


def mine_candidates(bundle: Path, seed: int) -> list[Candidate]:
    """Rule/exception pairs from closure edges: the edge SOURCE is the carve-out
    (the gold), the TARGET is the rule it modifies. Closure runs on the reverse
    index, which is exactly why the gold is hard to reach from the rule."""
    out: list[Candidate] = []
    with open_bundle(bundle) as graph:
        nodes = {n.node_id: n for n in graph.all_nodes()}
        docs = {d.doc_id: d for d in graph.documents()}
        for node in list(nodes.values()):
            for edge in graph.outgoing(node.node_id):
                cause = MINED_TYPES.get(edge.type)
                if cause is None:
                    continue
                rule = nodes.get(edge.dst)
                gold = nodes.get(edge.src)
                if rule is None or gold is None or rule.node_id == gold.node_id:
                    continue
                if len(gold.raw.strip()) < 80 or len(rule.raw.strip()) < 40:
                    continue  # too short to be a real provision or a real answer
                doc = docs.get(gold.doc_id)
                if doc is None or doc.source_uri is None:
                    continue
                name = Path(doc.source_uri).name
                out.append(
                    Candidate(
                        gold_node=gold,
                        rule_node=rule,
                        cause=cause,
                        act=name.replace("_", " ").removesuffix(".txt"),
                        source_file=name,
                    )
                )
    # Deduplicate on the gold node: one case per carve-out, not one per edge.
    seen: set[str] = set()
    unique = [c for c in out if not (c.gold_node.node_id in seen or seen.add(c.gold_node.node_id))]
    random.Random(seed).shuffle(unique)
    return unique


EXPORT_HEADER = """You are writing evaluation questions for a legal retrieval system.

Below are {n} numbered provisions from Indian Acts. For EACH one, write the
single most natural question that a lawyer or compliance officer would ask --
the practical question this provision would be consulted to answer.

Rules:
- Ask about the substance. Do NOT quote or paraphrase the provision's wording.
- Do NOT mention section numbers, clause numbers, or the Act's name.
- Exactly one sentence, ending in a question mark.
- Write a question for every id. Do not skip any.

Return ONLY JSON Lines, one object per provision, no other text:
{{"id": "<the id>", "question": "<your question>"}}

---

"""


def export_rules(candidates: Sequence[Candidate], out: Path, batch: int) -> None:
    """Write the generation prompt with the GOLD SPAN WITHHELD.

    This is the property that makes externally generated questions usable: the
    writer is shown the rule and never the carve-out, so a question cannot echo
    wording it was never given. Keep it that way if this is ever edited -- the
    whole measurement depends on it.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    chunks = [candidates[i:i + batch] for i in range(0, len(candidates), batch)]
    for n, chunk in enumerate(chunks, 1):
        body = [EXPORT_HEADER.format(n=len(chunk))]
        for cand in chunk:
            body.append(f"### id: {cand.gold_node.node_id}\n")
            body.append(f"Act: {cand.act}\n\n")
            body.append(cand.rule_node.raw.strip()[:1500])
            body.append("\n\n")
        path = out.with_name(f"{out.stem}_{n:02d}{out.suffix}")
        path.write_text("".join(body), encoding="utf-8")
        print(f"  wrote {path}  ({len(chunk)} provisions)")


def load_questions(path: Path) -> dict[str, str]:
    """Read {id, question} JSONL back. Tolerates surrounding prose and code
    fences, because these come back pasted out of a chat window."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().strip("`").strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        node_id, question = row.get("id"), row.get("question")
        if isinstance(node_id, str) and isinstance(question, str) and question.strip():
            out[node_id] = question.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--corpus", type=Path, default=Path("corpus/indian-acts"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target", type=int, default=30, help="hard cases wanted")
    ap.add_argument("--max-candidates", type=int, default=140,
                    help="cap on generation calls (free-tier quota)")
    ap.add_argument("--max-overlap", type=float, default=0.5)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--pace", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=20260905)
    ap.add_argument("--seeding", default="hybrid-rerank",
                    help="the BASELINE arm A. Never arm C.")
    ap.add_argument("--export-rules", type=Path,
                    help="write a generation prompt (RULES ONLY, gold withheld) "
                         "for an external model, then exit. Use when no API key "
                         "is available.")
    ap.add_argument("--batch-size", type=int, default=40,
                    help="candidates per exported prompt file")
    ap.add_argument("--questions", type=Path,
                    help="JSONL of {id, question} produced externally. Skips the "
                         "generation call; guards and the arm-A filter still run "
                         "locally, so selection stays blind to arm C.")
    args = ap.parse_args()

    candidates = mine_candidates(args.bundle, args.seed)
    print(f"mined {len(candidates)} candidate rule/carve-out pairs", flush=True)
    by_cause: dict[str, int] = {}
    for c in candidates:
        by_cause[c.cause] = by_cause.get(c.cause, 0) + 1
    for k, v in sorted(by_cause.items(), key=lambda kv: -kv[1]):
        print(f"  {v:4d}  {k}", flush=True)

    if args.export_rules:
        export_rules(candidates[: args.max_candidates], args.export_rules,
                     args.batch_size)
        print("\nPaste each file into a model, save the JSONL replies to one "
              "file, then re-run with --questions <that file>.")
        return 0

    supplied = load_questions(args.questions) if args.questions else {}
    if args.questions:
        print(f"loaded {len(supplied)} externally generated questions", flush=True)
    # No completer at all when questions are supplied: the answerability judge
    # is skipped rather than faked, and every case is flagged for review.
    llm = None if supplied else TextCompleter(min_interval_s=args.pace)
    kept: list[dict[str, str]] = []
    stats = {"generated": 0, "overlap": 0, "not_verbatim": 0,
             "baseline_found": 0, "judged_no": 0, "llm_error": 0,
             "no_question": 0}

    with open_bundle(args.bundle) as graph:
        nodes = graph.all_nodes()
        seeder = build_seeder(graph, nodes, args.seeding)
        byid = {n.node_id: n for n in nodes}

        for cand in candidates[: args.max_candidates]:
            if len(kept) >= args.target:
                break

            src_path = args.corpus / cand.source_file
            if not src_path.is_file():
                continue
            source_text = src_path.read_text(encoding="utf-8", errors="strict")
            gold = cand.gold_node.raw.strip()
            if gold not in source_text:
                stats["not_verbatim"] += 1
                continue  # invariant 10 discipline: unverifiable gold is worthless

            heading = ""
            parent = byid.get(cand.rule_node.node_id)
            if parent is not None:
                heading = f"{parent.raw.strip()[:120]}\n\n"

            if llm is None:
                question = supplied.get(cand.gold_node.node_id, "")
                if not question:
                    stats["no_question"] += 1
                    continue
            else:
                try:
                    question = llm.complete(
                        SYSTEM,
                        GENERATE.format(act=cand.act, heading=heading,
                                        rule=cand.rule_node.raw.strip()[:1500]),
                    )
                except ExtractorError as exc:
                    stats["llm_error"] += 1
                    print(f"  llm error: {exc}", flush=True)
                    continue
            stats["generated"] += 1
            question = question.strip().strip('"').splitlines()[0].strip()

            ratio = overlap_ratio(question, gold)
            if ratio > args.max_overlap:
                stats["overlap"] += 1
                continue

            # --- arm A ONLY. Arm C is never consulted here. ---
            ranked = seeder(question, args.top_k)
            seed_ids = set(ranked)
            gold_ids = set(gold_nodes(gold, nodes))
            if not gold_ids:
                stats["not_verbatim"] += 1
                continue
            if seed_ids & gold_ids:
                stats["baseline_found"] += 1
                continue  # baseline already solves it: carries no evidence

            if llm is not None:
                try:
                    verdict = llm.complete(
                        "You judge whether a legal passage affects an answer. "
                        "Reply with exactly one word.",
                        JUDGE.format(act=cand.act, question=question,
                                     gold=gold[:1500]),
                    )
                except ExtractorError as exc:
                    stats["llm_error"] += 1
                    print(f"  llm error (judge): {exc}", flush=True)
                    continue
                if not verdict.upper().startswith("YES"):
                    stats["judged_no"] += 1
                    continue

            # Reuse the ranking already computed: `_make_hybrid_seeder` rebuilds
            # the lexical index over every node on each call, so a second call
            # per question doubles the cost of the whole run for nothing.
            top = next((byid[i].raw.strip() for i in ranked if i in byid), "")
            kept.append({
                "query": question,
                "cause": cand.cause,
                "source_file": cand.source_file,
                "gold_span": gold,
                "naive_retrieval_gets": top[:300],
                "why_it_fails": (
                    f"provisional (needs review): {cand.cause}; the carve-out is "
                    f"linked to its rule by a closure edge but is not retrieved by "
                    f"{args.seeding} seeding at top-{args.top_k}"
                ),
            })
            print(f"  KEPT {len(kept):2d}/{args.target}  [{cand.cause}] "
                  f"overlap={ratio:.2f}  {question[:70]}", flush=True)

    args.out.write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in kept),
        encoding="utf-8",
    )
    print(f"\nwrote {len(kept)} cases -> {args.out}")
    print("rejected:", json.dumps(stats))
    if llm is None and kept:
        print("\nNOTE: the answerability judge was SKIPPED (no model available). "
              "Hand review is the only thing standing between these cases and the "
              "measurement -- check that each gold span genuinely qualifies the "
              "answer to its question.")
    print("\nEVERY case needs hand review before use: naturalness, cause label, "
          "and that a lawyer reading the act would accept the gold as the answer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
