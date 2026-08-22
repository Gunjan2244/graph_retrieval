# NEXT_STEPS.md

Open this first in a new session. It is the shortest path from cold start to
useful work. Everything here is actionable; background and evidence live in the
other docs.

**Reading order:** this file → `CLAUDE.md` (invariants) → `BUILD_PLAN.md`
(what's done, with evidence) → `README.md` (status, environment gotchas) →
`HANDOFF.md` (open questions) → `PARSER_PLAN.md` (L0 detail).

---

## Where the project stands

**15 of 40 tasks done.** 155 tests pass, `mypy --strict` and `ruff` clean.

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

**Updated 2026-08-22 — the arms have moved for the first time: A 10/15,
B 11/15, C 12/15, `lost_exception` C 3/5 -> 4/5, and the Child Labour case is
now reached by closure traversal and by nothing else.** The cause was not the
model: the cross-reference carve-out form matched no marker in the legal pack,
and the resolver that would have handled it was itself 26% wrong on the sites
it already accepted. Both fixed deterministically, no quota spent. Exit is
still not met — one case is not a clear margin. Detail below and in
`BUILD_PLAN.md` Phase 3. The paragraphs that follow describe the state before
that fix and are kept because the reasoning still holds for the model path.

**Confirmed on a fully-extracted graph.** The first measurement had model edges
for 1 of 9 documents, because Gemini's free tier is 20 requests/day/model. It
was re-run on Groq `openai/gpt-oss-120b` (1000/day): 147 calls, 123 model edges,
96 of them `exception_of`, all 9 documents — and the labeled-failure arms came
back **identical, 10/11/11**. Six times the extraction, zero cases moved. That
removes "extraction was too thin" as an explanation.

Extraction quality itself is decent — 12 of 135 candidates discarded (8.9%),
`exception_of` 16/20 correct, `defines` 15/17 with direction right 16/17. The
problem is not that the model extracts badly. It is that the relation the
labeled cases need **cannot be expressed** in a one-section window.

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

- **Model string: `groq/openai/gpt-oss-120b`** (now the adapter default, and
  what the Phase 3 measurements were run on). Two dead ids to know about:
  `gemini-2.0-flash` and `groq/llama-3.3-70b-versatile` both 404.
  `gemini/gemini-3.6-flash` works but see the quota note below.
- **Gemini 3.x is a reasoning model** — reasoning tokens count against
  `max_tokens`. A call capped at 10 tokens spent 59 thinking and returned
  `content=None`. The L3 adapter sets no cap, so it is safe; anything that adds
  one gets silent empty responses that look like "no edges found."
- `.env` lives at the repo root, mode 600, git-ignored. (It was in
  `.mypy_cache/.env`, which mypy can wipe — moved.)
- **Quota: use Groq, not Gemini.** Gemini's free tier is **20 requests per DAY,
  per model** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, confirmed
  on `gemini-3.6-flash` and `gemini-2.5-flash`) — a 49-call run gets ~19
  through. Groq gives **1000 requests/day at 8000 tokens/min**; the 171-call
  corpus run needs ~8s of pacing between calls to stay inside the TPM budget
  (`DGE_PROBE_PACE_S` in the scratch probe). `--dry-run` prices characters,
  which is the right number for Groq and the wrong one for Gemini.
- **`tenacity` must be installed** or litellm's `num_retries` raises
  `tenacity import failed` instead of retrying. It is installed now; it is not
  in `pyproject.toml`'s `llm` extra.

Checks that must stay green:

```bash
ruff check src scripts tests && mypy --strict src && python3 -m pytest tests/ -q
```

---

## Done: sub-section citations (2026-08-22)

The section pointed here to fix this; it is done, measured, and did not
require model quota. Full account in `BUILD_PLAN.md` Phase 3 and
`decisions.md`. Summary: ~40% of intra-document citations name a sub-section
("sub-section (1)") and were invisible before this — not mis-resolved,
unmatched. A second registry keyed `(enclosing section, enumerator)` resolves
them; range citations ("(2) to (4)") are expanded rather than truncated to
their endpoint. 151 tests pass, 10 new, 8 of them individually falsified by
reverting their mechanism (2 more are structurally guaranteed rather than
sharply falsified — see `decisions.md` for the honest account of which).

**The labeled-failure arms (A 10/15, B 11/15, C 12/15) did not move.** None
of the 15 cases turns on a sub-section citation. What moved: self-referential
exception linkage 89% -> 90% corpus-wide. Phase 0 density gate re-confirmed
unchanged (p95=3, 9.8%).

## Immediate next step: unblock SEEDING — the evidence now points there

**Three consecutive rounds have closed a real, measured extraction gap without
moving `A 10/15 B 11/15 C 12/15`.** Sub-section citations, then cited
"for the purposes of", then the `edge_id` collision. The most recent is the
clearest signal: the Betwa `lost_scope` gold node went from **zero inbound
edges to exactly the right one**, and the case still reports `never`. The
graph is correct for that case; retrieval never starts from the provision
that would reach it.

That is standing blocker 3, promoted from caveat to operative constraint:
**seeding is still lexical.** `BAAI/bge-large-en-v1.5` is not cached and three
download attempts died on the documented network stall. Until hybrid + rerank
seeding runs, arm A is weak, and a weak arm A flatters traversal while hiding
exactly the wins these rounds are producing.

Do this before any further extraction work:

1. Get `BAAI/bge-large-en-v1.5` cached (see README's fastembed section for the
   three download gotchas). A different network, a manual HF download, or a
   mirror all count — this is an environment problem, not a code one.
2. Re-run `scripts/phase3_exit_report.py` on the SAME bundle with hybrid
   seeding. Record all three arms. Absolute recall will move; the question is
   whether arm C's margin over arm A grows.
3. Only then decide whether the extractor still needs work.

**Second, and independent: the labeled failure set is 15/50.** Three measured
corpus-wide improvements now have no case positioned to register them. That is
itself evidence the set is too small to answer the margin question.

## Parked: the AMU heading bug

**Model: Sonnet for either.** Two independent, small options — pick based on
which you want more right now:

**Option A — `Aligarh_Muslim_University_Act,_1920`'s heading misclassification.**
Bracketed amendment headings (`10[17. The Chancellor.`) are landing as
`PROPOSITION` instead of `STRUCTURAL` in at least one document, confirmed
while measuring the sub-section fix above. This is a `dge.parsing`
classification issue, not an `edges.py` one — the sub-section registry's
first-writer-wins-and-warn policy degrades it safely today (a miss, never a
wrong link), but fixing the root cause would recover real sections currently
invisible to `section_registry` entirely, not just their sub-sections.
Verify with `scripts/parser_corpus_report.py` before and after; this
document is presumably not the only one affected.

**Option B — the labeled failure set is 15/50** (Phase 0, still open). This is
now doubly motivated: the margin question always needed more than 15 cases,
and now there is a measured 9-point corpus-wide linkage improvement with no
case in the set positioned to register it. `scripts/phase0_taxonomy.py`
enforces the same verbatim-grounding discipline as invariant 10 — extract
gold spans by anchor string, not by hand; the source text's non-breaking
spaces and doubled newlines silently fail hand-transcription.

**Do not spend model quota on either.** Both are deterministic/corpus work.

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
