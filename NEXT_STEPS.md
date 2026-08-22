# NEXT_STEPS.md

Open this first in a new session. It is the shortest path from cold start to
useful work. Everything here is actionable; background and evidence live in the
other docs.

**Reading order:** this file → `CLAUDE.md` (invariants) → `BUILD_PLAN.md`
(what's done, with evidence) → `README.md` (status, environment gotchas) →
`HANDOFF.md` (open questions) → `PARSER_PLAN.md` (L0 detail).

---

## Where the project stands

**15 of 40 tasks done.** 141 tests pass, `mypy --strict` and `ruff` clean.

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

## Immediate next step: make sub-section citations resolvable

**Model: Opus.** Same shape as the work below it, which is the argument for
doing it: that work moved the exit criterion, and this is the larger half of
the same population.

**What was just done (2026-08-22), because it changes what to believe.** The
previous instruction here was "widen reference resolution to handle plural
forms". Doing that first would have been wrong. Auditing the citations the
resolver already *accepted* found 14 of 54 were wrong (26%) — 6 fabricated from
citations to other Acts, 8 borrowed from an unrelated clause — and all 14 were
CLOSURE-class, the kind traversal follows unbudgeted. Fixed, hand-checked to
38/38, full detail in `BUILD_PLAN.md` Phase 3 and `decisions.md`.

Then the thing that actually moved the number: the cross-reference carve-out
**matched no marker at all**. `nothing_shall_apply` requires "nothing in THIS
section"; "Nothing in Secs . 7, 8 and 9 shall apply" had no marker, so the
resolver was never invoked on it. That is invariant 11's failure mode exactly —
`BUILD_PLAN` had concluded the relation was inexpressible in L3's one-section
window, which is true of the model path and made the missing *pack data* easy
to miss.

    before   A 10/15  B 11/15  C 11/15    lost_exception A 3/5 B 3/5 C 3/5
    after    A 10/15  B 11/15  C 12/15    lost_exception A 3/5 B 3/5 C 4/5

The Child Labour case is `never / never / expansion` — reached by closure
traversal and by neither seeds nor context. **"Closure traversal changed no
labeled case" is no longer true.** Exit is still NOT met: one case is not a
clear margin, and the labeled set is 15.

**Now do the sub-section half.** "Notwithstanding anything contained in
sub-section (1)" is the largest single unresolved form — 85 of 254 unresolved
corpus-wide, and 12 of the 15 nodes where the new marker fires but resolves
nothing. Unlike a cross-Act citation, a sub-section reference is local and
unambiguous, so every one of them is a recoverable closure edge.

It is a **registry** gap, not a resolver gap: `_build_cursor` keys
`section_registry` on section headings only, so "(1)" has nothing to resolve
against. The work is a sibling registry scoped to the enclosing section —
`parent_of` already exists in `_SectionCursor`, so the scoping is available.

Verify exactly as this round was verified, and in this order:

1. `python3 scripts/phase3_exit_report.py --bundle <b>.sqlite --corpus corpus/indian-acts`
   for the arms. Build the bundle from the 9 documents named in
   `corpus/failure_taxonomy.jsonl`.
2. **Hand-check every new edge, not a sample** — the populations here are
   small enough (5, 33, 38) that full enumeration is cheaper than sampling and
   is the only way the precision claim means anything.
3. **Audit what the change ACCEPTS, not only what it adds.** That is the whole
   lesson of this round.
4. Revert each mechanism in turn and confirm the named test fails.

**Do not spend model quota on this.** It is deterministic pack and registry
work, and a model call cannot resolve "(1)" against a registry that has no
entry for it.

Also still open, both found by hand-checking:

- **Explanations attach to their preceding sibling**, but an Explanation
  qualifies the whole section. Since `defines` traverses FORWARD, the
  provisions that actually use the term never reach it. 11/15 correct.
- **Mention linking fires inside proper nouns** — "Child Labour Technical
  Advisory Committee" links to the definition of "child". 17/20 correct.
- **`Foreign_Marriage_Act,_1969` s.24 parses as a FOOTNOTE**, so it never
  enters the registry and the last unlinked cross-reference carve-out resolves
  to nothing — correctly, but for the wrong reason. The footnote heuristic is
  load-bearing for `test_footnote_node_is_never_a_closure_edge_target`; do not
  touch it without that test in front of you.

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
