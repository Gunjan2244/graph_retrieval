# Document Graph Engine

Turns documents into a layered, LLM-traversable graph: normalized proposition
nodes, a term symbol table, and typed directed edges. Retrieval seeds with
vectors and collects by graph traversal.

**The claim the whole system exists to support:** it cannot answer from a rule
while omitting a known exception to it, or from a version known to be
superseded.

Flat-chunk RAG fails in a specific, expensive way — it retrieves a rule without
its exception, and the model gives a confident wrong answer with no signal that
anything is missing. That is not a ranking problem, and better embeddings do not
fix it: the missing text is *structurally* related to the query, not
*semantically similar* to it. So the two jobs are split. Embeddings find what a
passage is **about**; typed edges record how passages **govern each other**.

- **`NEXT_STEPS.md`** — **start here.** Current status, the exact environment
  incantation, and a ready-to-paste prompt for the next task.
- **`CLAUDE.md`** — invariants and anti-goals. Read before writing code.
- **`BUILD_PLAN.md`** — every task, with evidence for what's done.
- **`HANDOFF.md`** — environment traps and open questions.
- **`PARSER_PLAN.md`** — the L0 rewrite, in detail.
- **`docs/00`–`08`** — the design argument. Read `01` and `04` before touching
  retrieval.

---

## Status: 15 of 40 tasks done

141 tests pass; `mypy --strict` and `ruff` are clean.

| Phase | State |
|---|---|
| 0 · Validate the assumption | **Passed.** Closure chains are sparse on real data |
| 1 · Substrate + baseline retrieval | Mostly done — L1 normalizer still missing |
| 2 · Term symbol table | **Not started** (7 tasks) |
| 3 · Closure edges + soundness | Soundness half **met and measured**; recall half **not met**, but closure traversal now wins a labeled case |
| 4 · MCP tool surface | **Not started** (2 tasks) |
| 5 · Cross-document | Not started (6 tasks) — post-MVP |
| 6 · Cost and scale | Not started (4 tasks) — post-MVP |

### What genuinely works

The full loop runs end to end on real Indian bare acts: **ingest → embed →
query**, with closure traversal and a soundness verdict.

Demonstrated on `samples/sample_act.txt`: seeding *only* on the rule, lexical
retrieval scores the rule **3.448** and its proviso **0.234** — rank 4, missed
at any sane cutoff, because "operation of law" shares almost no vocabulary with
the query. Closure traversal brings in both provisos anyway, including the
exception-to-the-exception. A flat-RAG answer citing only the rule is reported
`UNSOUND`, naming the exact node to add.

- **60 real Central Acts** fetched from a free HF mirror (India Code has no bulk API)
- **Closure sparsity holds**: chain p95 = 3, closure density 9.7%, re-verified
  after the parser rewrite rebuilt the substrate at ~10× granularity
- **Traversal is falsifiable**: delete the reverse-index walk in
  `closure_neighbors` and 7 tests fail, including every soundness test
- **Evidence-span validator holds**: paraphrases, fabrications, case changes and
  tiny fragments are all rejected, and every accepted span is stored as a
  verbatim slice of the substrate — not the model's rendering of it

### L3 has now run on real acts, and Phase 3's exit is half met

Measured 2026-08-21 on real Indian bare acts, not a fixture. Reproduce the
whole thing with no key and no network:

```bash
python scripts/phase3_exit_report.py --bundle bundle.sqlite
```

**Met — soundness violation rate = 0**, over 170 real queries on a 9-act
bundle. The check is demonstrably not vacuous: run against a seeds-only
(flat-RAG) context it reports **128/170 = 75.3% UNSOUND**, 427 violations.
That contrast is the product claim, measured.

**Not met — traversal does not beat the Phase 1 baseline.** On all 15 labeled
failure cases, seeds-only reaches 10/15 and the full pipeline 11/15; on the
five `lost_exception` cases both arms reach 3/5. The single case traversal
added came from the context frontier, not a closure edge. Per `docs/06` §6.3
that points at the **extractor**, not the traversal policy, and the traversal
policy was left alone.

Measured twice, on purpose. The first run had model edges for 1 of 9 documents
(Gemini's free tier is 20 requests/day). The second, on Groq
`openai/gpt-oss-120b`, covered all 9 — 147 calls, 123 model edges, 96 of them
`exception_of` — and produced **exactly the same 10/11/11**. Six times the
extraction changed nothing, so "extraction was too thin" is not the
explanation.

The mechanism is traced, not guessed: on the Child Labour case retrieval ranks
"7. HOURS AND PERIOD OF WORK" and "8. WEEKLY HOLIDAYS" at 3 and 4 — the seeds
are right — and the carve-out that answers the question has **zero inbound
edges**, because L3's one-section-per-call window cannot express a link to a
section it was never shown. Full evidence, per-edge-type precision, and the
three bugs the run exposed are in `BUILD_PLAN.md` Phase 3; the reasoning is in
`decisions.md`.

**The evidence-span validator earns its keep.** Across 135 real candidates,
**12 were discarded (8.9%)** — and every one was proposed at confidence >= 0.90.
Three echoed the prompt's own `[N1] (heading)` scaffolding back as evidence,
one stitched two separate nodes into a span that exists verbatim nowhere, and
one cited "the Council" (11 chars), exactly the case the length floor exists
for. Without the check those are twelve confident, wrong edges in the graph.

**Provider notes, both of which cost a run:** Gemini's free tier is **20
requests per day, per model** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
confirmed on `gemini-3.6-flash` and `gemini-2.5-flash`) — a 49-call run gets
~19 through. Groq gives 1000/day at 8000 tokens/min, which is what made the
full run possible; pace to the token budget, not the request count. And
`groq/llama-3.3-70b-versatile`, the adapter's old default, **404s** — the
default is now `groq/openai/gpt-oss-120b`.

**`--rerank` has now run live**, offline, against the real
`BAAI/bge-reranker-base` model. Verified: rerank ranked a return-window rule
above an unrelated board-meeting clause, above a return-exclusion clause, in
the correct relevance order. Getting the model cached took three attempts —
see "fastembed's model cache" below if you ever need to seed this on a new
machine.

---

## Run it

```bash
source .venv/bin/activate          # deps live here; system python has none of them
export PYTHONPATH=src
export HF_HUB_OFFLINE=1            # reranker is cached; see the cache note below

python3 -m dge.cli ingest samples/sample_act.txt -o /tmp/b.sqlite
python3 -m dge.cli embed  -b /tmp/b.sqlite --provider local
python3 -m dge.cli query  "transfer made in the ordinary course of business permitted" \
    -b /tmp/b.sqlite -k 1 --use-vectors --show-provenance

# L3 dry run — no key, no network. Prices the corpus, reports conflict findings.
python3 -m dge.cli extract -b /tmp/b.sqlite --dry-run
```

Checks:

```bash
ruff check src scripts tests && mypy --strict src && python3 -m pytest tests/ -q
```

---

## fastembed's model cache — where it's stored and how it actually resolves

`BAAI/bge-reranker-base` (rerank) is cached at
`/home/someone_practicing/.cache/fastembed` — **not** inside the repo, and
**not** `/tmp` (a small tmpfs that exhausts mid-download). Set
`FASTEMBED_CACHE_PATH` to that path if it's ever unset.

**`BAAI/bge-large-en-v1.5` (embeddings) is NOT cached.** An earlier version of
this file said it was; the 4.8G in `~/.cache/huggingface` is `microsoft/phi-4`,
unrelated. `dge embed` and `dge query --use-vectors` therefore do not work on
this machine right now — they fail with `Could not load model
BAAI/bge-large-en-v1.5 from any source`. Two download attempts on 2026-08-21
died on the network stall described below (once inside HF's xet CAS client,
once with `HF_HUB_DISABLE_XET=1` on a plain connection reset). Seeding it needs
the same `curl -C - --speed-limit ... --retry` treatment the reranker needed.

Getting `bge-reranker-base` (1.06GB) cached took three real attempts, worth
knowing if this ever needs redoing on a new machine:

1. **It can silently "succeed" while incomplete.** A background download
   reported exit 0 with only the tokenizer present — no ONNX weights. Always
   verify by loading the model and scoring something, never by trusting an
   exit code or a file-size check alone.
2. **This environment's network stalls on long sustained transfers**, twice,
   at fixed byte offsets with open sockets going nowhere — not "slow", genuinely
   stuck. `curl -C - --speed-limit 2048 --speed-time 15 --retry 50` (byte-range
   resume + abort-and-reconnect on stall) recovered where a single long-lived
   Python session hung indefinitely.
3. **fastembed's cross-encoder loader needs a live revision lookup even to
   confirm local files are complete** — `HF_HUB_OFFLINE=1` breaks it on a cache
   built any way other than fastembed's own downloader (its `local_files_only`
   path skips the verification `snapshot_download` needs). Loading with
   `HF_HUB_OFFLINE` **unset** let it resolve once, reuse the already-downloaded
   blob instantly (no re-fetch), and write the metadata file that makes offline
   loading work on every run after. Once that first online resolution has
   happened, `HF_HUB_OFFLINE=1` works fine — confirmed on the very next run.

None of this is a code issue; nothing in `src/dge/adapters/rerank_local.py`
changed. It's operational knowledge for re-seeding a cache from scratch.

---

## API keys

A `GEMINI_API_KEY` is already configured in `.env` (git-ignored, mode 600) and
verified working. Load it with `set -a; . ./.env; set +a`.

**Use `--model gemini/gemini-3.6-flash`.** `gemini-2.0-flash` has been retired
by Google and now returns HTTP 404; every reference in this repo was updated.

**Gemini 3.x is a reasoning model, and reasoning tokens count against
`max_tokens`.** A call capped at 10 tokens spent 59 on thinking and returned
`content=None`. The L3 adapter sets no `max_tokens`, so it is unaffected — but
anything that adds one will get silent empty responses that look like "the
model found no edges" rather than "the call was truncated."

**Groq is an alternative, and Groq is not Grok.** Different companies. Groq
(`console.groq.com`, `GROQ_API_KEY`) is an inference-hardware company running
*open* models (Llama 3.3 70B) on custom chips — fast, generous free tier,
native `json_schema` with `strict`. Grok is xAI's model and is not involved
here. Provider is a `--model` string, never a code path —
`src/dge/adapters/extract_llm.py` is the only file that knows litellm exists.

Everything except L3 and L1 runs with zero keys and zero network.

---

## What to do next

### 1. Run L3 against a real model — Opus

Not more building. The machinery exists; what's missing is the only evidence
that matters. Get the Groq key, run `dge extract` on a handful of real acts, and
read the results critically.

**The number to watch is `discarded`** — candidates killed by the
evidence-span, window, and enum checks. A near-zero discard rate is *suspicious,
not reassuring*; inspect `L3Report.rejected` reasons directly before believing
it. Then hand-check a sample of surviving edges for precision, per edge type,
never aggregated. Phase 3's exit criterion is soundness violation rate = 0 plus
measurable improvement over the Phase 1 baseline.

Opus, because this is judgment about extraction quality, and because a bad
prompt contract costs real money at ingest scale.

### 2. Phase 2 — term symbol table — mixed

Seven tasks, and the highest commercial value in the plan. `docs/06` §6.6 argues
`lint()` is the wedge that opens the door — *"27 defined terms never defined"* is
legible to a buyer in five seconds, where retrieval quality demands they trust a
benchmark.

- **Sonnet**: definition-site detection from `LEGAL_DEFINITIONS`, mention
  linking via `pyahocorasick`, the lint checks themselves.
- **Opus**: scope resolution with shadowing (`"For the purposes of this
  section"` overriding the Act-level glossary), and storing `means` vs
  `includes` distinctly. Exhaustive-vs-illustrative is litigated constantly and
  flattening the two produces confidently wrong scope answers.

### 3. Phase 4 — MCP server — Sonnet

Ten tools over functions that already exist, so mostly plumbing. This is the
distribution channel: it drops into Claude Code and every agent framework
without asking anyone to change their stack.

### 4. Close the loose ends — Sonnet

- Failure taxonomy is at **15 of 50** cases. Deliberately unpadded. Extract gold
  spans programmatically by anchor — the source has non-breaking spaces that
  make hand-transcription silently fail the verbatim check.
- Run `--rerank` once so L2's `[x]` stops being optimistic (~1GB download).
- L1 normalizer (Phase 1) — needs a model, same key as L3.

### Deliberately deferred

Phases 5–6 (10 tasks) are post-MVP. Docling/PDF is untouched — the `.txt`
corpus made it unnecessary, and `samples/Indian-Penal-Code-1860.pdf` still
cannot be ingested.

---

## Two open questions that need real decisions

**Should a closure relation on a *section* propagate to its *children*?**
A `referenced` marker resolves to a section's heading node, while the clause
making the claim is a sub-section. So seeding a section heading reaches a
competing provision on the reverse index, but seeding the bare clause does not —
the only path runs through `part_of`, a budgeted context edge. Both halves are
pinned in `tests/test_conflict.py` rather than assumed. The obvious fix would
unify the budgeted and unbudgeted halves of traversal, which invariant 6 forbids
doing casually.

**The cost gate is much weaker than Phase 0 suggested.** Measured across 62
acts: it skips 46% of calls but only 24% of the bill, because L3 runs one
*section* per call and admitted sections are the long ones. Phase 0's 22.3% was
a per-node number and does not transfer. The lever is pack data
(`LEGAL_GATE_TERMS`), not engine code — 178 of 1272 admitted sections come in on
`'provided'` alone. Neither that nor `'subject to'` has been precision-checked.

---

## Model-selection heuristic

What actually separated the two in this project:

**Opus** for work where a wrong answer is expensive and invisible: architectural
decisions, invariant-dense logic (traversal direction, closure vs context),
prompt contracts, and judging quality from evidence. Traversal direction has to
be resolved *per edge type* — `exception_of` runs reverse, `defines` runs
forward — and a single global direction silently breaks one of them while all
tests still pass.

**Sonnet** for well-specified execution against an existing pattern: adapters
behind a Protocol, CLI plumbing, tests, lint cleanup, corpus fetching. It did
the parser rewrite and the reranker well.

The failure mode to watch, seen twice here: **a session marking work `[x]` that
only ever ran against a fake.** Both times the code was correct and the claim
was overstated. Ask for the command output, not the summary.
