# NEXT_STEPS.md

Open this first in a new session. It is the shortest path from cold start to
useful work. Everything here is actionable; background and evidence live in the
other docs.

**Reading order:** this file → `CLAUDE.md` (invariants) → `BUILD_PLAN.md`
(what's done, with evidence) → `README.md` (status, environment gotchas) →
`HANDOFF.md` (open questions) → `PARSER_PLAN.md` (L0 detail).

---

## Where the project stands

**15 of 40 tasks done.** 133 tests pass, `mypy --strict` and `ruff` clean.

Of the 25 remaining, 10 are Phases 5–6 and explicitly post-MVP. **15 tasks
stand between here and the MVP** defined in `docs/06` §6.4.

| Phase | Remaining | Notes |
|---|---|---|
| 0 · Validation | 1 | failure taxonomy at 15/50, deliberately unpadded |
| 1 · Substrate | 4 | L1 normalizer is the significant one |
| 2 · Symbol table | 7 | not started; highest commercial value |
| 3 · Closure + L3 | 1 | **EXIT half met.** Soundness = 0 measured; recall does not beat baseline |
| 4 · Tool surface | 2 | MCP server; mostly plumbing |
| 5–6 | 10 | post-MVP, deferred |

The full loop works end to end on real Indian bare acts: **ingest → embed →
query**, with closure traversal, a soundness verdict, and `--rerank`. L3 runs
and writes provenance-stamped edges.

**Now established (2026-08-21):** L3 has run on real acts. Soundness violation
rate is 0 over 170 real queries, and the check is falsified rather than merely
asserted — flat-RAG scores 75.3% unsound on the same queries. But **closure
traversal changed no labeled failure case**: seeds-only 10/15, full pipeline
11/15, `lost_exception` 3/5 in both arms. Per docs/06 §6.3 that is a verdict on
the extractor, not the policy, and the traversal policy was deliberately left
alone. Full evidence in `BUILD_PLAN.md` Phase 3; reasoning in `decisions.md`.

**The binding practical constraint:** the Gemini free tier is **20 requests per
day, per model** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`),
confirmed on `gemini-3.6-flash` and `gemini-2.5-flash`. Model edges therefore
exist for 1 of 9 documents, so the above measures a mostly-deterministic graph.

---

## Environment — copy this verbatim

```bash
cd /home/someone_practicing/Downloads/dge-starter
source .venv/bin/activate                 # deps live here; system python has none
export PYTHONPATH=src
set -a; . ./.env; set +a                   # GEMINI_API_KEY, verified working
export FASTEMBED_CACHE_PATH=/home/someone_practicing/.cache/fastembed
export HF_HUB_OFFLINE=1                    # reranker only; the EMBEDDER is not cached — see README
```

- **Model string: `gemini/gemini-3.6-flash`.** `gemini-2.0-flash` is retired and
  returns 404.
- **Gemini 3.x is a reasoning model** — reasoning tokens count against
  `max_tokens`. A call capped at 10 tokens spent 59 thinking and returned
  `content=None`. The L3 adapter sets no cap, so it is safe; anything that adds
  one gets silent empty responses that look like "no edges found."
- `.env` lives at the repo root, mode 600, git-ignored. (It was in
  `.mypy_cache/.env`, which mypy can wipe — moved.)
- **Free-tier quota is 20 requests per DAY, per model** —
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, confirmed on
  `gemini-3.6-flash` and `gemini-2.5-flash` on 2026-08-21. `--dry-run` first is
  still right, but it prices tokens and the wall is request count: a 49-call
  run gets ~19 through. Quota is per model, so switching `--model` buys another
  20 at the cost of mixing models in one measurement.
- **`tenacity` must be installed** or litellm's `num_retries` raises
  `tenacity import failed` instead of retrying. It is installed now; it is not
  in `pyproject.toml`'s `llm` extra.

Checks that must stay green:

```bash
ruff check src scripts tests && mypy --strict src && python3 -m pytest tests/ -q
```

---

## Immediate next step: fix the extractor's cross-section blindness

**Model: Opus.** This is the finding Phase 3's measurement produced, and it is
the one thing standing between the graph and its own product claim.

The problem, in one sentence: **a carve-out that names other sections cannot be
linked to them.** L3 runs one section per call, so the model can only propose
edges between units it was shown, and `validate_candidate` is right to reject
anything else. The deterministic path does not cover the gap either —
`dge.edges._resolve_target`'s `referenced` hint takes a single match from
`_REF_RE`, which does not handle "Secs . 7, 8 and 9" (plural, list, stray
space). Corpus-wide: self-referential provisos link at 89% (463/521),
cross-reference carve-outs at 62% (5/8), and the cross-reference form is what
two of the five labeled `lost_exception` cases turn on.

Two candidate fixes, and they are not equivalent:

1. **Widen reference resolution (pack data + `_resolve_target`).** Handle
   plural forms and enumerated lists, emitting one edge per named section.
   Cheap, deterministic, no model cost, and it is the lever invariant 11 points
   at. Start here. Verify with
   `python scripts/phase3_exit_report.py --bundle <b>.sqlite` — the Child
   Labour case should move from `never` to `expansion`.
2. **Let L3 see named sections.** A second window containing the units a
   carve-out cites. This is a real design change (it breaks "one section per
   call" and multiplies cost) and should not be attempted until (1) is measured
   — it may not be needed.

Also worth fixing while in there, both found by hand-checking:

- **Explanations attach to their preceding sibling**, but an Explanation
  qualifies the whole section. Since `defines` traverses FORWARD, the
  provisions that actually use the term never reach it. 11/15 correct in the
  sample, and the 4 misses are all this.
- **Mention linking fires inside proper nouns** — "Child Labour Technical
  Advisory Committee" links to the definition of "child". 17/20 correct.

**Do not spend model quota on this.** It is deterministic work, and the free
tier is 20 requests/day/model — see the constraint note above.

---

## Also open, cheap, and blocking good measurement

- **`BAAI/bge-large-en-v1.5` is not cached**, so all recall numbers above use
  lexical seeding rather than docs/06 §6.3's hybrid + rerank baseline. That
  makes the baseline weaker, which flatters traversal — so the negative result
  is robust, but the absolute numbers will move. See README's cache section.
- **`tenacity` was missing** from the venv, so litellm's `num_retries` raised
  instead of retrying. Installed; add it to the `llm` extra in
  `pyproject.toml`.
- **The failure taxonomy is 15/50.** All 15 are now measured, which makes the
  next 35 worth more than they were.

## After that, in order

**Phase 2 — term symbol table (7 tasks).** Highest commercial value in the plan.
`docs/06` §6.6 argues `lint()` is the wedge that opens the door: *"27 defined
terms never defined"* is legible to a buyer in five seconds, where retrieval
quality demands they trust a benchmark. If you ever need to show this to someone
before the retrieval story is provable, build this.

- *Sonnet*: definition-site detection from `LEGAL_DEFINITIONS`, mention linking
  via `pyahocorasick`, the lint checks.
- *Opus*: scope resolution with shadowing (`"For the purposes of this section"`
  overriding the Act-level glossary), and storing `means` vs `includes`
  distinctly — exhaustive-vs-illustrative is litigated constantly and flattening
  it produces confidently wrong scope answers.

**Phase 4 — MCP server (2 tasks). Sonnet.** Ten tools over functions that
already exist. This is the distribution channel — drops into Claude Code and any
agent framework without asking anyone to change their stack.

**Leftovers. Sonnet.** L1 normalizer (needs the same key as L3); failure taxonomy
from 15 → 50 cases.

---

## Model routing — what actually separated them here

**Opus** for work where a wrong answer is expensive and invisible: architectural
decisions, invariant-dense logic, prompt contracts, and judging quality from
evidence. Example: traversal direction must resolve *per edge type* —
`exception_of` runs reverse, `defines` runs forward — and a single global
direction silently breaks one of them while every test still passes.

**Sonnet** for well-specified execution against an existing pattern: adapters
behind a Protocol, CLI plumbing, tests, lint cleanup, corpus fetching. It did the
parser rewrite and the reranker well.

---

## Working agreements — these are why the project is trustworthy

1. **Never mark `[x]` on something that has only run against a fake.** This has
   happened twice. Both times the code was correct and the claim was overstated.
2. **Verify by running, not by exit code.** A background download reported exit 0
   with the model weights entirely missing. Load it and score something.
3. **Tests for guarantees must be falsifiable.** Delete the reverse-index walk in
   `closure_neighbors` → 7 tests fail, including every soundness test. Delete the
   L3 dedup fix → the duplicate test fails. If breaking the mechanism doesn't
   break a test, the guarantee isn't enforced by anything.
4. **Leave shortfalls visibly unchecked rather than padded.** The failure taxonomy
   sits at 15/50 for this reason — it determines build order, so inventing volume
   defeats its purpose.
5. **Report the negative result.** "Extraction is mediocre" is a finding.

---

## Open questions that need real decisions

**Should a closure relation on a *section* propagate to its *children*?** A
`referenced` marker resolves to a section's heading node, while the clause making
the claim is a sub-section. Seeding a heading reaches a competing provision on
the reverse index; seeding the bare clause does not — the only path runs through
`part_of`, a budgeted context edge. Both halves are pinned in
`tests/test_conflict.py`. The obvious fix would unify the budgeted and unbudgeted
halves of traversal, which invariant 6 forbids doing casually.

**The cost gate is much weaker than Phase 0 suggested.** It skips 46% of calls
but only 24% of the bill, because L3 runs one *section* per call and admitted
sections are the long ones. The lever is pack data (`LEGAL_GATE_TERMS`), not
engine code — 178 of 1272 admitted sections come in on `'provided'` alone.
Neither that nor `'subject to'` has been precision-checked.

**Two substituted models, both documented in `HANDOFF.md`.** `CLAUDE.md`'s stack
table settles on BGE-M3 and BGE-reranker-v2; fastembed ships neither, so
`bge-large-en-v1.5` and `bge-reranker-base` are running instead. Note the
512-token limit does **not** bind — measured max node is ~300 tokens, zero nodes
exceed it. Also worth knowing: `jina-reranker-v2` benchmarks better but is
CC-BY-NC-4.0, non-commercial, and therefore disqualified for a product.
