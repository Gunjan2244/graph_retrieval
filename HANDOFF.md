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
```

Checks, all currently green:

```bash
ruff check src scripts tests
mypy --strict src
python3 -m pytest tests/ -q          # 72 passed
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

**Sandbox note, not a code issue:** this sandbox's `/tmp` is a 3.6G tmpfs.
`bge-reranker-base` is ~1GB and downloading it there via fastembed's default
cache path (`$TMPDIR/fastembed_cache`) can exhaust it mid-download ("disk
quota exceeded"). Point `FASTEMBED_CACHE_PATH` at a real-disk directory (or
pass `cache_dir=` to the adapter) if that happens — it's an artifact of this
environment's `/tmp` sizing, not something the adapter code should special-case.

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
| Phase 0 corpus + validation | `scripts/fetch_corpus.py`, `scripts/phase0_density.py`, `scripts/phase0_taxonomy.py`, `corpus/` |
| Traversal (the differentiator) | `src/dge/traversal/{graph,expand,assemble,soundness}.py` |
| Bundle read side | `BundleGraph` / `open_bundle` in `src/dge/bundle.py` |
| Query pipeline | `src/dge/query.py`, `src/dge/retrieval/{lexical,hybrid}.py` |
| L2 adapters | `src/dge/adapters/{embed_local,embed_hosted,rerank_local,rerank_hosted}.py` |
| CLI | `src/dge/cli.py` — `ingest`, `embed`, `query` (`--use-vectors`, `--rerank`) |

The load-bearing test is
`tests/test_traversal.py::test_exception_is_only_reachable_via_the_reverse_index`.
Deleting the `incoming()` walk in `closure_neighbors` fails 7 tests including
every soundness test — verified, not assumed. If that walk is ever refactored,
confirm those tests still fail without it, or the guarantee is no longer being
enforced by anything.
