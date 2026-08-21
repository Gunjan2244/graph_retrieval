# HANDOFF.md

Working state as of 2026-08-20. Read `CLAUDE.md` (invariants, anti-goals) and
`BUILD_PLAN.md` (what is done, with evidence) first — this file only carries
what is **not** derivable from the repo.

---

## Run it

```bash
source .venv/bin/activate        # fastembed, mypy, ruff, litellm, pydantic, duckdb, pyarrow, sqlite-vec
export PYTHONPATH=src
export HF_HUB_OFFLINE=1          # embedding model already cached (~4.8G); without this fastembed reaches out and hangs

python3 -m dge.cli ingest samples/sample_act.txt -o /tmp/b.sqlite
python3 -m dge.cli embed  -b /tmp/b.sqlite --provider local
python3 -m dge.cli query  "transfer made in the ordinary course of business permitted" \
    -b /tmp/b.sqlite -k 1 --use-vectors --show-provenance

# L3. --dry-run needs no key and no network: it prices the corpus through the
# cost gate and reports the deterministic conflict findings.
python3 -m dge.cli extract -b /tmp/b.sqlite --dry-run
# A real run needs ONE free-tier key (see "L3 needs a key" below):
#   export GROQ_API_KEY=...
#   python3 -m dge.cli extract -b /tmp/b.sqlite
```

Checks, all currently green:

```bash
ruff check src scripts tests
mypy --strict src
python3 -m pytest tests/ -q          # 126 passed
```

---

## Traps that cost time

**Bundles carry the schema they were born with.** A bundle written before the
`node_vectors` table existed fails `dge embed` with `no such table:
node_vectors`. Re-ingest to fix. There is no migration path for existing
bundles — if that becomes a real constraint, it needs building.

**`HF_HUB_OFFLINE=1` matters.** First model fetch took ~60 minutes on this
connection. It is cached now, but fastembed will still try the network without
that flag.

**The venv is not optional.** `python3` outside it has no mypy, no ruff, no
fastembed. There is no system-wide install of any of them.

---

## L3 needs a key, and nothing has ever run against a real model

This is the single most important thing to know about the current state.
Phase 3's machinery is built, typed, and tested — **entirely against fakes**.
`BUILD_PLAN.md` Phase 3 is ticked for the plumbing and explicitly NOT for the
exit criterion, because the quality question L3 exists to answer cannot be
answered by a fake extractor.

**Get one free key.** The recommendation is **Groq** (`GROQ_API_KEY`, from
console.groq.com): generous free tier, native `json_schema` structured output
with `strict`, and `groq/llama-3.3-70b-versatile` is already the adapter
default. Google AI Studio (`GEMINI_API_KEY`, `--model gemini/gemini-3.6-flash`)
is an equally fine fallback. Provider is a `--model` string, never a code
path — `src/dge/adapters/extract_llm.py` is the only file in the repo that
knows litellm exists.

What to run first, and what to look at:

```bash
python3 -m dge.cli extract -b bundle.sqlite --dry-run     # free, prices it
python3 -m dge.cli extract -b bundle.sqlite               # needs the key
```

The number to watch is `discarded` in the output. That is candidates killed by
the evidence-span / window / enum checks, and it is the honest read on whether
the model is inventing edges. A discard rate near zero on a real corpus is
suspicious, not reassuring — check `L3Report.rejected` reasons directly before
believing it.

---

## The cost gate is much weaker than Phase 0 suggested

Measured on all 62 acts, on the rebuilt substrate
(`python3 scripts/phase3_gate_report.py`):

| granularity | calls admitted | characters admitted |
|---|---|---|
| node | 30.6% | 37.8% |
| **section (what actually runs)** | **54.0%** | **76.0%** |

Phase 0's 22.3% was a per-node number on the old substrate and does not
transfer. L3 runs one section per call, a section is admitted if *any* node in
it carries *any* gate term, and the admitted sections are the long ones — so
the gate skips 46% of calls but only 24% of the bill.

Do not "fix" this in the engine; the gate is pack data and the boundary is
invariant 11. The lever is `LEGAL_GATE_TERMS`: 178 of 1272 admitted sections
are admitted by `'provided'` alone and 146 by `'subject to'` alone. Neither has
been precision-checked, and tightening them is the first real cost move
(BUILD_PLAN Phase 6).

---

## Open design question this phase deliberately did NOT decide

Should a closure relation asserted against a *section* propagate to that
section's *children*?

It came up while representing non obstante conflicts and it is load-bearing.
A `referenced` marker resolves to the named section's HEADING node, while the
clause making the claim is a sub-section. So for two mutually-overriding
provisions the closure cycle is between the two sections, not the two clauses,
and:

- seeding a **section heading** reaches the competitor on the reverse index —
  the guarantee holds;
- seeding the **bare clause** does not, because the only path runs through
  `part_of`, which is a budgeted CONTEXT edge.

Both halves are pinned in `tests/test_conflict.py` so this cannot quietly be
assumed away. The obvious fix — making `part_of` closure-traversable — would
unify the budgeted and unbudgeted halves of traversal behind one mechanism,
which CLAUDE.md invariant 6 forbids doing casually. It needs its own decision
with its own evidence, not a side effect of the conflict module.

---

## Gaps I chose not to paper over

**1. BGE-M3 is not what's running.** `CLAUDE.md`'s stack table settles on
`BGE-M3 (self-host)`. fastembed's model zoo does not ship BGE-M3 — verified
directly against the installed version's `TextEmbedding.list_supported_models()`.
Substituted `BAAI/bge-large-en-v1.5`: same BGE lineage, but **English-only and
512-token** where BGE-M3 is **multilingual with an 8192-token window**. That is
a real fidelity gap, not a cosmetic one, and it will matter if the corpus grows
long provisions or non-English text. Documented in
`src/dge/adapters/embed_local.py`'s docstring. Real BGE-M3 is available via
`FlagEmbedding` (pulls torch) as a second adapter if the gap starts to bite.

**2. BGE-reranker-v2 is not what's running either, same shape of gap.**
`CLAUDE.md`'s stack table settles on BGE-reranker-v2. fastembed's cross-encoder
zoo ships `BAAI/bge-reranker-base` but not a v2 model — verified directly
against `TextCrossEncoder.list_supported_models()`. Substituted the base model;
documented in `src/dge/adapters/rerank_local.py`'s docstring, same discipline
as gap 1. Reranker is now built (`Reranker` protocol, local fastembed adapter +
hosted Voyage `/v1/rerank` adapter, wired behind `--rerank` on `dge query`),
so L2 is `[x]` in `BUILD_PLAN.md`.

**`bge-reranker-base` is now downloaded, verified, and cached at
`/home/someone_practicing/.cache/fastembed`** — not `/tmp` (a 3.6G tmpfs that
exhausts mid-download on a ~1GB file). `dge query --rerank` has run live and
correctly ranked real candidates. Full account of what it took (three
attempts: a silent-incomplete download, a stalled connection recovered with
`curl -C - --speed-limit ... --retry`, and an `HF_HUB_OFFLINE` sequencing
gotcha specific to fastembed's cross-encoder loader) is in `README.md`'s
"fastembed's model cache" section — read that before touching this again on a
fresh machine.

---

## Done: the parser rewrite (was "next rock")

`PlainTextParser` no longer splits on blank lines alone. Full design and
corpus evidence in `PARSER_PLAN.md` — summary here.

The old parser broke in two opposite ways depending on which of the corpus's
two hard-wrap dialects a document used: whole multi-section blobs collapsing
into single `STRUCTURAL` nodes invisible to L3a (Mines Act: 89 nodes, max 8375
chars, 6 marker edges for 100+ sections), or wrapped line fragments shredding
into scraps (Actuaries Act: 1036 nodes, median 69 chars). **Both scored
`parse_confidence == 1.0`** — invariant 9 never fired on either pathology.

Rewritten around one reflow rule instead of a dialect branch: a unit starts at
an enumerator/keyword line and absorbs continuation lines; a blank line is
absorbed only while what's open is still mid-wrap (bare enumerator, or its
last line ends without terminal punctuation), not on any blank-line run. Plus
a real nesting stack (chapter > section > sub-section > clause) so `PART_OF`
points at the immediate parent instead of the nearest heading, and a
per-document-heading-line calibration for the genuinely ambiguous case where
a bare `N.` line is a section heading in one place and a wrapped sub-section
marker in another (same shape).

Verified against all 62 fetched acts with `scripts/parser_corpus_report.py`:
median-of-medians 137 chars, worst max 1493 (was 8375), **60/62 documents
parse at full confidence**; the 2 genuinely ambiguous ones
(`Regional_Rural_Banks_Act,_1976`, `All_India_Services_Act,_1951`) now
correctly gate below the 0.5 review threshold instead of silently passing.
The two named garbage edges from the old parser (`exception_of` "3. Act not
to apply" → "2. Definitions"; "Chapter II / 5. Chief Inspector" → "4.
References to time of day") are confirmed gone — `dge.edges`'s
`preceding`/`referenced` resolution was also rewritten to walk the real
parent/sibling structure the new parser emits, instead of flat document-order
adjacency.

**Phase 0's density gate was re-run on the new substrate** (10x more nodes,
correctly shaped) since the original PASS was measured on the broken one:
still **chain p95 = 3, closure density = 9.7%**, unchanged. The traversal
design's core assumption holds on real granularity, not just on the
accidental granularity blank-line splitting happened to produce.

7 new regression tests in `tests/test_parsing.py` pin the specific bugs found
along the way (blank-absorption swallowing a complete heading's next
paragraph; a reclassified bare-digit subsection not being recognized as bare;
`heading_bare` missing from the structural-kind set entirely, so every
dialect-B section heading came out as `PROPOSITION` with no nesting). 67/67
tests pass, `ruff`/`mypy --strict` clean.

**Still open, unchanged from before:** a Docling adapter behind the same
`Parser` protocol for `samples/Indian-Penal-Code-1860.pdf` — not attempted
here, `.txt` corpus only. Also open: `sql/schema.sql` applied to both engines,
document classifier, L1 normalizer, Postgres job queue (see `BUILD_PLAN.md`
Phase 1's remaining unchecked items).

---

## Done: footnotes are no longer closure-edge targets

Amendment footnote lines (`"1. Subs. by Act 42 of 1983, s. 11, for certain
words (w.e.f. 31-5-1984)."`) were classified `footnote`/`footnote?` at the
line level but still emitted as `NodeKind.PROPOSITION` — indistinguishable
from real operative text. `Mines_Act,_1952` alone parses to 132 of them, and
one was reachable as a closure-edge target: a proviso's `exception_of`
resolved via `edges.py`'s sibling-chain walk to a footnote sitting between it
and the rule it actually modifies, instead of the rule itself.

Fixed with a distinct `NodeKind.FOOTNOTE` (`model.py`). Footnote nodes are now
excluded from the sibling chain `_build_cursor` builds (`edges.py`) — so a
footnote interposed between two real siblings no longer breaks the link
between them — and from marker/structural-unit/definition/mention matching
everywhere `NodeKind.STRUCTURAL` was already excluded (`edges.py`,
`lexicon.py`). They stay in the substrate (still byte-addressed, still
readable) rather than being dropped at parse time, since amendment history is
real information a later phase (version/supersession chains) may want.

Verified across all 62 corpus acts: 0 closure edges touch a footnote node
either as source or destination (was 1+ on `Mines_Act,_1952` alone). Two new
regression tests — `tests/test_parsing.py` (footnote lines get
`NodeKind.FOOTNOTE`, not `PROPOSITION`) and
`tests/test_edges.py::test_footnote_node_is_never_a_closure_edge_target`
(reproduces the exact Mines Act pathology as a fixture) — the latter confirmed
to fail if the `_build_cursor` exclusion is reverted.

---

## Loose thread

`corpus/failure_taxonomy.jsonl` holds **15 cases against a 50-case target**
(`BUILD_PLAN.md` Phase 0). All 15 are real and verbatim-checked against the
fetched acts, spanning all six failure causes. Left deliberately unchecked
rather than padded — this exercise determines build order (`docs/06` §6.2), so
inventing volume defeats its purpose.

When extending it: extract gold spans **programmatically by anchor string**, not
by hand. The source text carries non-breaking spaces and doubled newlines that
make hand-transcription silently fail the verbatim check.
`scripts/phase0_taxonomy.py` enforces that check — same discipline as invariant
10.

---

## Map of what was built

| Area | Files |
|---|---|
| L3 (Phase 3) | `src/dge/l3/{evidence,sections,prompt,schema,run,conflict}.py`, `src/dge/adapters/extract_llm.py`, `scripts/phase3_gate_report.py` |
| Phase 0 corpus + validation | `scripts/fetch_corpus.py`, `scripts/phase0_density.py`, `scripts/phase0_taxonomy.py`, `corpus/` |
| Traversal (the differentiator) | `src/dge/traversal/{graph,expand,assemble,soundness}.py` |
| Bundle read side | `BundleGraph` / `open_bundle` in `src/dge/bundle.py` |
| Query pipeline | `src/dge/query.py`, `src/dge/retrieval/{lexical,hybrid}.py` |
| L2 adapters | `src/dge/adapters/{embed_local,embed_hosted,rerank_local,rerank_hosted}.py` |
| CLI | `src/dge/cli.py` — `ingest`, `embed`, `query` (`--use-vectors`, `--rerank`) |

L3's load-bearing test file is `tests/test_evidence.py` — it is the enforcement
of CLAUDE.md invariant 10 and it was written before any model call existed,
because every model-extracted edge in the system is worth exactly what that
check is worth. The case to preserve above all others is
`test_recovered_span_always_slices_the_window_for_every_accepted_case`: it is
what makes accepting a whitespace-reflowed match safe rather than a quiet
loosening of the invariant. If evidence spans ever stop being slices of the
window, `edges.evidence_span` stops being checkable against the immutable
bytes and invariant 10 is enforced by nothing.

The load-bearing traversal test is
`tests/test_traversal.py::test_exception_is_only_reachable_via_the_reverse_index`.
Deleting the `incoming()` walk in `closure_neighbors` fails 7 tests including
every soundness test — verified, not assumed. If that walk is ever refactored,
confirm those tests still fail without it, or the guarantee is no longer being
enforced by anything.
