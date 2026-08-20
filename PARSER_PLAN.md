# PARSER_PLAN.md — rebuilding L0

Design and evidence for the BUILD_PLAN Phase 1 item *"`Parser` protocol +
Docling adapter"* and the one after it, *"Parse validation + confidence
score"*. Read `CLAUDE.md` and `HANDOFF.md` first. Everything below was measured
against the 62 fetched acts in `corpus/indian-acts/`, not assumed.

Reproduce any number here with `python3 scripts/proto_parser_lines.py`
(`--lines`, `--show <file>`). That script is a **prototype and a measuring
stick**, not something to import.

---

## 1. The diagnosis is worse and more specific than `HANDOFF.md` says

`HANDOFF.md` reports "median 77 chars, max 8375" across 4 acts. That number
averages **two opposite pathologies** and hides both. Measured per document:

| act | nodes | median | max | structural | proposition |
|---|---|---|---|---|---|
| Anti-Hijacking, 2016 | 22 | 758 | 2724 | 19 | 3 |
| Mines, 1952 | 89 | 819 | 8375 | 58 | 31 |
| Navy, 1957 | 189 | 676 | 7171 | 181 | 8 |
| Prisons, 1894 | 63 | 461 | 3734 | 60 | 3 |
| Actuaries, 2006 | 1036 | 69 | 104 | 49 | 987 |
| Consumer Protection, 1986 | 486 | 74 | 190 | 30 | 456 |

**Pathology A — blobbing** (top four). A whole Act collapses to 22–189 nodes.
Worse, almost every node is `STRUCTURAL`, because each blank-line block *starts*
with a section heading and `_HEADING_RE.match` classifies the entire block by its
first line. `extract_structural_edges` and `extract_marker_edges` both open with
`if node.kind is NodeKind.STRUCTURAL: continue`, so **the normative text is
invisible to L3a**. This is not merely "wrong edges from adjacency" — it is
near-total extraction failure:

```
Anti-Hijacking   part_of=3   unit_edges=0  marker_edges=1  skipped=3
Mines            part_of=29  unit_edges=0  marker_edges=6  skipped=7
```

Six marker edges for a 100-section Act, and they are the garbage `HANDOFF.md`
names (`exception_of` from `3. Act not to apply…` to `2. Definitions`).

**Pathology B — shredding** (bottom two). 1036 nodes at median 69 chars is not
good granularity; those are hard-wrapped **line fragments**. Provisos attach to
mid-sentence debris:

```
exception_of  'Provided that he shall be given an opportunity…'
           -> 'earlier by the Central Government and shall be'
```

**Both documents scored `parse_confidence == 1.0`.** Invariant 9 never fired.
That is the single most important fact in this file: the substrate gate exists
in `pipeline.py` and currently gates on nothing.

### Why: the corpus has two text dialects

`PlainTextParser._split_blocks` splits on `\n{2,}`. Blank lines are not the
document's structure in either dialect.

- **Dialect A** (~44 files, mean line length > 100). One provision per line,
  parenthesised enumerators `(1)`, `(a)`, `(i)`; marginal notes end `.-`. Blank
  lines separate *sections*, so a whole section becomes one node.
- **Dialect B** (~18 files, mean line length 39–60, 41–51 % blank). HTML→text
  output, hard-wrapped at ~75 columns with a blank line between every fragment,
  enumerators rendered `1.` / `a.` and frequently **alone on their own line**:

  ```
  1.

  SHORT TITLE, EXTENT AND COMMENCEMENT. –
  (1)

  This Act may be called the Child Labour (Prohibition and Regulation) Act, 1986.
  ```

Three further corpus facts the current code does not know:

1. **Amendment brackets.** India Code wraps amended text as `N[ … ]`, N being a
   footnote number, placed *before* the enumerator: `2[3. Act not to apply…`,
   `4[(1)] In this Act`. 798 occurrences across 33 files, 52 of them on section
   headings. `_HEADING_KEY_RE` in `edges.py` fails on these, so those sections
   never enter `section_registry` and every `referenced` marker aimed at them
   silently produces no edge.
2. **Footnotes are shaped exactly like section headings.** `1. Ins. by Act 42 of
   1983, s. 17 (w.e.f. 31-5-1984).` matches the heading pattern. 634 heading-
   shaped lines carry amendment-note signals.
3. **Provisos are frequently inline**, after a colon mid-line: `…where such
   Magistrate is an Executive Magistrate: Provided that the Magistrate may…`.
   193 occurrences. A line-based splitter alone will miss all of them.
4. **Mixed line endings.** 4 files contain CR; one contains a `\r\r\n` "blank"
   line that `\n{2,}` does not even see.

---

## 2. Decisions — settled, do not relitigate

### Decision 1 — Rewriting `PlainTextParser` is not the "don't write a parser" anti-goal

`CLAUDE.md` forbids writing *a PDF parser* and points at Docling. Docling does
not help here: these are `.txt` files with no layout to recover, only an
enumerator grammar. Docling is not installed and stays out of the offline core
path. A Docling adapter for `samples/Indian-Penal-Code-1860.pdf` is a separate,
still-open Phase 1 item behind the same `Parser` protocol.

### Decision 2 — One reflow rule, no dialect branch

Do **not** detect the dialect and switch algorithms. The following single rule
handles both, and was verified across all 62 acts:

> A logical unit **starts** at a line that begins a recognised enumerator or
> structural keyword, and **absorbs** every following line that does not.
> A blank line is absorbed while the open unit owns an enumerator (it is a
> wrapping artifact); in free prose, a blank line separates.

That last clause is what keeps preambles from swallowing a document while
letting dialect B's `1.\n\nSHORT TITLE…` rejoin. Measured result:

| act | before (nodes / med / max) | after (units / med / max) |
|---|---|---|
| Mines, 1952 | 89 / 819 / 8375 | 615 / 128 / 1202 |
| Navy, 1957 | 189 / 676 / 7171 | 900 / 152 / 1337 |
| Anti-Hijacking | 22 / 758 / 2724 | 108 / 162 / 750 |
| Actuaries, 2006 | 1036 / 69 / 104 | 172 / 182 / 1660 |
| Consumer Protection | 486 / 74 / 190 | 198 / 121 / 879 |

Across all 62: **median-of-medians 141 chars, median max 875, worst max 4318.**
Both pathologies collapse under the same rule.

Dialect detection still has a job — see Decision 4 — but only as a
*confidence signal*, never as control flow.

### Decision 3 — `node.raw` stays a byte-exact slice of the original

Reflow joins physical lines, so a unit's text is *not* whitespace-identical to
its source span. Resist storing the whitespace-collapsed string in `raw`.

`tests/test_parsing.py::test_byte_offsets_round_trip_to_original_bytes` asserts
`raw[node.byte_start:node.byte_end].decode() == node.raw`. **Keep that test
passing.** It is the executable form of invariants 1 and 10; once `raw` stops
being a verifiable slice, every `evidence_span` check becomes fuzzy and
invariant 10 is enforced by nothing.

This is safe because reflow only ever joins *consecutive* physical lines, so
every unit — including an inline-proviso split, which cuts one line at a
character offset — is a **contiguous** byte span. `raw` therefore keeps its
internal newlines.

The cost is that pattern matching must tolerate `\n` where it expects a space.
This is already nearly free: the patterns in `domains/legal.py` are written with
`\s+` throughout, and exactly one of the 47 contains a literal space — the
character class in the citation pattern at `legal.py:354`, `[A-Za-z ,]`, which
becomes `[A-Za-z\s,]`. Do not store a second copy of the text to work around
this.

### Decision 4 — Parse confidence becomes a real measurement, and the residue goes to review

Three files resist the rule set and must not be papered over. All three are
dialect B where sub-sections render as bare `N.` — **formally identical to a
section heading**:

- `Regional_Rural_Banks_Act,_1976` — 75 % of units unclassified; `2. It extends
  to the whole of India.` is read as a section heading when it is sub-section (2)
  of section 1. The mis-numbering then cascades into `section_registry`.
- `All_India_Services_Act,_1951` — `1.` alone on a line, 9 units for the file.
- `Credit_Information_Companies_(Regulation)_Act,_2005` — sub-sections glued
  into their heading rather than split.

The disambiguation signal that works is the **marginal-note terminator**: a real
heading is a short noun phrase ending `.-` / `.—` / `:--`, while a sub-section is
a full sentence. Resolve it with a **per-document calibration pass**: decide the
document's heading convention once from the unambiguous evidence (headings that
carry a terminator), then apply that convention uniformly to the ambiguous
`N.` lines. Where the evidence does not settle it, that is exactly what invariant
9 is for — **score low, halt the document, queue it for review.** Do not guess.

Signals to fold into the confidence score, all cheap and already measured by the
prototype:

- fraction of non-blank lines assigned to a recognised unit (dialect A files sit
  at 9–15 % unclassified; the broken ones at 72–94 % — this metric separates good
  from bad cleanly and is the primary signal);
- monotonicity violations in the section sequence;
- ambiguous bare-`N.` lines that calibration could not resolve;
- node-length distribution outside a sane band (a max over ~4000 chars means a
  unit did not split);
- decode damage and CR handling (keep the existing check).

---

## 3. Tasks — all done

Each had a checkable exit criterion. All eight are complete; notes below
record what actually happened, including two places reality diverged from
the plan as written.

1. **[x] `src/dge/parsing.py` — line tokenizer.** Physical lines with exact
   `(byte_start, byte_end)` via `_iter_physical_lines` (handles `\n`, `\r\n`,
   bare `\r`). Byte offsets computed by `_ByteCursor`, a forward-only char→byte
   converter that encodes only the delta since the last call — O(n) total
   instead of the old `_byte_len(text[:char_pos])`'s O(n²).
   *Exit met:* every node's `raw` round-trips to its byte span on all 62 acts
   (`tests/test_parsing.py::test_byte_offsets_round_trip_to_original_bytes`
   plus the corpus-wide check embedded in `scripts/parser_corpus_report.py`).

2. **[x] Line classifier.** `_classify_line` in `src/dge/parsing.py`, ported
   from `scripts/proto_parser_lines.py` with the `_AMD` amendment-bracket
   prefix on every enumerator regex, plus keyword-prefixed (`Section N.`) and
   markdown-heading forms the prototype didn't need. Kinds: `chapter`,
   `schedule`, `md_heading`, `heading`, `heading_bare`, `subsec`, `clause`,
   `clause_d`, `proviso`, `explanation`, `illustration`, `exception`,
   `footnote`, `footnote?` (ambiguous/non-monotonic), `text`, `blank`.

3. **[x] Reflow to logical units**, then **split inline provisos.** Done, but
   Decision 2 as originally written ("absorb blank while the open unit owns
   an enumerator") turned out to be *insufficient*, not just imprecise — see
   §4 below, "what the plan got wrong."

4. **[x] Per-document heading calibration**, but implemented as **per-line**
   calibration instead of a single whole-document convention. Decision 4 said
   "decide the document's heading convention once ... apply uniformly"; the
   actual implementation (`_classify_line`'s sentence-shape check against
   `_SENTENCE_LIKE_RE`, applied locally to each ambiguous bare-`N.` line) is a
   deliberate improvement on that, not a shortcut — a single per-document flag
   can't be right for a document that mixes both shapes, which real acts do.
   *Exit met, partially as specified:* `Regional_Rural_Banks_Act` no longer
   misreads `2. It extends to the whole of India.` as a section heading (it's
   correctly a sub-section of section 1) — but the same file's overall
   heading structure is *still* messy enough elsewhere that it lands below
   the confidence gate, i.e. it took the "flagged for review" branch of the
   exit criterion, not "resolved correctly." That's judged as correct
   behavior, not a shortfall — see task 6.

5. **[x] Nesting.** Stack machine over `_NESTING_LEVEL` (chapter/schedule = 0,
   heading/`heading_bare` = 1, subsec = 2, clause/`clause_d` = 3); `PART_OF`
   points at the immediate parent. `InheritedContext.section_path` is built
   from the stack, e.g. `CHAPTER II > Section 3. Hijacking.- > (2)`.
   *Exit met:* `tests/test_parsing.py::test_three_level_nesting_produces_a_three_level_section_path`.

6. **[x] Real `parse_confidence`.** Combines: decode damage (unchanged, -0.3);
   fraction of units with no structural marker at all (-0.55 above 50% — see
   below); fraction of non-monotonic heading-shaped lines, i.e. likely
   footnotes that weren't resolved (-0.2 above 10%); and an outlier max-unit-
   length check (-0.3 above 4000 chars, catching a reflow failure directly).
   *Exit met, with the threshold tuned from the plan's guess:* the corpus has
   a clean gap in the text-fraction signal — `All_India_Services_Act` (65%)
   and `Regional_Rural_Banks_Act` (53%) vs. the next-highest real file at 29%
   — so the *threshold* (50%) needed no tuning, but the *penalty weight* did:
   0.4 (the plan's placeholder) left `All_India_Services_Act` at 0.60, above
   the 0.5 gate; 0.55 puts both named files below it (0.25 and 0.45) while
   every other document stays at exactly 1.0 (60/62). Anti-Hijacking / Mines /
   Navy — the three blobbing-pathology files named in HANDOFF.md — all parse
   at 1.0. `Credit_Information_Companies_(Regulation)_Act`, the third file
   named in the original HANDOFF diagnosis, is no longer a review case at
   all: task 4 + 5 resolve it correctly outright (verified directly — its
   section 1/2/3 sub-sections and provisos now nest and extract cleanly).
   Deliberately did not add a "deliberately corrupt input" fixture beyond
   what already existed (`test_decode_errors_lower_confidence_below_default_gate`) —
   the corpus already supplies two real corrupt-*shaped* documents, which is
   stronger evidence than a synthetic one.

7. **[x] `edges.py` follow-ups.** `_HEADING_KEY_RE` now sees through `N[`.
   `_resolve_target`/`_build_cursor` rewritten to use the real parent/sibling
   structure from `dge.parsing`'s own `PART_OF` edges (passed in as a new
   `structural_edges` parameter, default `()` for the old flat behavior small
   fixtures rely on) instead of re-deriving a flat "last STRUCTURAL node"
   scan — the old scan is what produced the garbage edges in the first place,
   since `sub_section`/`clause` structural units in `legal.py` used it too.
   `legal.py:354`'s character class fixed per Decision 3.
   *Exit met:* both named garbage edges are gone, confirmed directly against
   `Mines_Act,_1952` on the new substrate — `exception_of` "3. Act not to
   apply" → "2. Definitions" and "Chapter II / 5. Chief Inspector" → "4.
   References to time of day" no longer appear anywhere in
   `extract_structural_edges`/`extract_marker_edges`'s output for that file.

8. **[x] Re-run everything downstream.** `scripts/phase0_density.py` re-run
   against all 62 acts on the new substrate: **13,050 units** (vs. a much
   smaller, mis-shapen count before), **chain p95 = 3, closure density =
   9.7%** — unchanged from the original PASS despite the substrate being
   rebuilt from scratch. Recorded in `BUILD_PLAN.md` Phase 0.

**What the plan got wrong, worth knowing before touching this code again:**
Decision 2's rule ("absorb blank while the open unit owns an enumerator") was
necessary but not sufficient. It handles a bare `1.` absorbing its own wrapped
title, but dialect B *also* wraps a complete heading's title, and ordinary
prose, across a blank line the identical way — so the naive version of the
rule either swallowed a heading's own trailing paragraph forever (a `Section
1. Title.` heading absorbing the *next*, unrelated paragraph, since "heading"
counted as enumerated with no way to tell "done" from "still wrapping") or
under-absorbed and re-shredded dialect B (once bareness alone gated
absorption, a heading's title split across a blank stopped mid-word). The fix
that actually worked is content-based, not kind-based: absorb a blank line
when EITHER the open unit is still a bare enumerator with no content yet
(`1.`, `(1)`), OR its last line doesn't look sentence/title-complete (no
terminal punctuation, or a dangling comma — `_LOOKS_COMPLETE_RE` in
`src/dge/parsing.py`). Two more small-but-real bugs surfaced by the same
process, both now covered by regression tests: a bare-digit line reclassified
from `heading_bare` to `subsec` via the sentence-lookahead rule wasn't
recognized as "bare" by the absorption check (different regex shape, same
semantic bareness); and `heading_bare` was missing entirely from
`_STRUCTURAL_KINDS`/`_NESTING_LEVEL`, so every dialect-B section heading came
out as a `PROPOSITION` with no children nested under it — silent and would
not have been caught by a byte-offset or confidence check, only by looking at
actual parsed output.

**Do not break:** `ruff check src scripts tests`, `mypy --strict src`,
`python3 -m pytest tests/ -q` (67 passing, 60 original + 7 new regressions
in `tests/test_parsing.py`), and the `samples/sample_act.txt` end-to-end path
in `HANDOFF.md` — re-verified via `dge ingest` → `dge embed` → `dge query
--use-vectors --show-provenance`: soundness still reports sound, the proviso
chain still assembles correctly, and section paths are now richer (e.g.
`Section 12. Limitation on transfer. > (2)`) than before the rewrite.
