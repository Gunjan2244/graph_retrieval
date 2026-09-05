# decisions.md

Major decisions, with the reason. Newest first. A decision that is only a
restatement of `CLAUDE.md` does not belong here; this file is for the calls
that could reasonably have gone the other way.

---

## 2026-08-21 (later) — Re-ran Phase 3 on a real quota rather than accepting the caveat

**Decision: re-measure the whole exit criterion on Groq with full L3 coverage,
instead of shipping the negative result with "extraction was thin" attached.**

The first measurement carried a caveat that could have explained the result
away: model edges existed for 1 of 9 documents, because Gemini's free tier is
20 requests/day/model. A caveat that large is not a finding, it is an excuse.
Groq (`openai/gpt-oss-120b`, 1000 requests/day) allowed 147 calls over all 9
documents — 123 model edges, 96 of them `exception_of`, six times the previous
extraction.

The labeled-failure arms came back **identical**: A 10/15, B 11/15, C 11/15,
`lost_exception` 3/5 in both arms. Not one case moved. That converts the
diagnosis from "traversal did not help on a thin graph" into "traversal cannot
help, because the extractor cannot express the relation" — the Child Labour
carve-out still has zero inbound edges with full coverage, and the model did
not even repeat the within-window edges Gemini had proposed.

Extraction quality itself turned out fine (`exception_of` 16/20, `defines`
15/17 with direction right 16/17, 8.9% discard). Fixing the model was never the
lever; fixing the window is.

**Corollary worth keeping: a model id is data, and must be checked by asking
the provider.** `groq/llama-3.3-70b-versatile` — the adapter's own documented
default, recommended in three project documents — 404s. So did
`gemini-2.0-flash` before it. Neither was caught by any test, because tests
mock the transport.

## 2026-08-21 — The wire schema tightens; the parser stays lenient

**Decision: `response_json_schema` marks every property `required`, while
`ExtractedEdge` keeps its defaults.**

`confidence` has a Pydantic default, so it was absent from `required`. That is
correct JSON Schema and a hard 400 from every strict implementation. Because
the adapter reads a 400 as "this provider cannot do json_schema" and latches
into plain JSON mode for the remainder of the run, one defaulted field cost
every subsequent call the closed label enum — the mechanism that makes a
fabricated endpoint unrepresentable rather than merely invalid.

The alternative was dropping the default from the model. Rejected: then a
response omitting `confidence` fails to parse, and we would be discarding an
otherwise good edge over a missing optional field. Strict on what we ask for,
lenient on what we accept, is the right asymmetry — and it is the same
asymmetry `dge.l3.run` already applies to everything the model says.

**Vindication of invariant 10, with numbers.** On the Groq run 12 of 135
candidates were discarded, every one proposed at confidence >= 0.90: three
echoed the prompt's own `[N1] (heading)` scaffolding back as evidence, one
stitched two separate nodes into a span appearing verbatim nowhere, one cited
"the Council" (11 chars, caught by the length floor). "Enforce in code, not in
the prompt" stops being a slogan at that point.

## 2026-08-21 — Phase 3 exit: half met, and the shortfall is the extractor

**Decision: leave Phase 3's exit unchecked, and do not touch the traversal
policy.**

The measurement (`scripts/phase3_exit_report.py`, evidence in `BUILD_PLAN.md`):
soundness violation rate is 0 over 170 real queries and the check is
demonstrably not vacuous — the same check against a flat-RAG context is unsound
on 75.3% of them. But on all 15 labeled failure cases, closure traversal
changed nothing: seeds-only reaches 10/15, the full pipeline 11/15, and the one
extra case arrived on the context frontier rather than through a closure edge.
Exception recall is 3/5 in both arms.

docs/06 §6.3 pre-committed to what that means — "if traversal does not beat it
by a clear margin on labeled failures, the **edge extractor** is what to fix,
not the traversal policy" — so the policy was left alone. The tempting move,
making `part_of` closure-traversable so a clause reaches its section's
relations, would have turned several of these cases green and is precisely what
invariant 6 forbids doing casually. It would also have been treating the
symptom: the Child Labour carve-out has **zero inbound edges**, so no traversal
rule reaches it.

**Why the extractor misses it, traced rather than assumed:** L3 runs one
section per call, so a carve-out naming ss. 7, 8 and 9 from inside s. 9 can
only ever be linked to units that shared its window — the model cannot propose
an endpoint it was not shown, and `validate_candidate` is right to reject one.
The deterministic path does not cover the gap either: `_REF_RE` takes a single
match and does not handle "Secs . 7, 8 and 9". Corpus-wide the split is sharp —
self-referential provisos link at 89% (463/521), cross-reference carve-outs at
62% (5/8), and the cross-reference form is the one two of the five labeled
`lost_exception` cases depend on.

## 2026-08-21 — "No opinion" beats "inferred denial" in L3 reconciliation

**Decision: a MEDIUM pattern edge is degraded only when the model was actually
in a position to confirm it — both endpoints together in one examined window,
after a call that succeeded.**

Two separate bugs shared this root, and both silently rewrote the graph:

- A section whose call raised was still marked examined, so a transient 429
  halved the confidence of every MEDIUM pattern edge in it. Measured:
  `Foreign_Marriage_Act,_1969` completed 0 calls and had 28 edges pushed below
  the traversal floor; 110 across one run.
- Reconciliation checked only that `edge.src` had been examined, never that
  `edge.dst` shared the window. Since L3 shows one section per call, every
  cross-section `defines` link — a provision to the Sec. 2 definition of a term
  it uses — was guaranteed to be degraded. On the Child Labour Act that was 17
  of 21 degraded edges.

The alternative reading is that a model shown *part* of a relation and not
proposing it is weak evidence against. Rejected: `validate_candidate` would
have discarded such a proposal anyway, so there is no answer the model could
have given that counts as confirmation. Degrading on that basis is inferring a
denial from a question never asked, and it makes the graph a function of window
boundaries and network weather rather than of the corpus (invariant 8).

## 2026-08-21 — A rate limit is not evidence about structured-output support

**Decision: the one-way `_structured_output` latch in the LiteLLM adapter trips
only on errors the provider actually raised about the request. Transient
failures (429, 5xx, timeouts, connection resets) propagate as a recorded
section failure.**

The latch exists for a good reason — a provider that cannot do `json_schema`
will not start being able to mid-run, and retrying per section doubles the
bill. But it was keyed on *any* exception. One 429 flipped the whole run into
JSON mode, and a later section came back with `rel_type` instead of `type` and
was lost to a validation error. Classification reads `status_code` when the
exception carries one, falling back to the class name, so it stays vendor-
neutral and needs no litellm import at module scope.

## 2026-08-21 — Measure extraction quality by replay, not by re-spending quota

**Decision: record every L3 wire response, then re-run the fixed pipeline
against the recording.**

The free tier is 20 requests/day/model (verified on two models), so a live
re-run after each fix was not available — and would not have been reproducible
anyway. Replaying the recorded responses through the real parse, validation and
reconciliation path measured the fix against the exact model output that
exposed the bug, and confirmed unconfirmed-edge count dropping 21 -> 4 with no
new calls.

## 2026-08-22 — Audit the edges that RESOLVE, not just the ones that don't

**Decision: before widening reference resolution, hand-check every citation it
already resolves.**

`NEXT_STEPS.md` pointed at a coverage problem: `_resolve_target` handled only
the singular "section N" form, so plural and list citations ("Secs . 7, 8 and
9") were dropped, and cross-reference carve-outs linked at 62% against 89% for
self-referential provisos. The prescribed fix was to widen the pattern.

Widening first would have been a mistake. Measuring the *unresolved* population
answers "what are we missing" and cannot answer "what are we getting wrong",
and the second question turned out to have the worse answer. Across 63 Central
Acts, 14 of the 54 resolved sites — **26%** — were wrong, in two shapes:

- **6 fabrications.** `section N of the <Other> Act` resolved against *this*
  document's registry. "Notwithstanding anything contained in section 12 of the
  Central Goods and Services Tax Act" wrote a STRONG-confidence `supersedes`
  edge against this Act's s.12, a provision the sentence never mentions.
- **8 borrowed targets.** The resolver searched the whole node and took the
  first hit, so a marker acquired a citation from an unrelated clause:
  "...paid into court under section 98, such court shall, notwithstanding
  anything contained in..." pointed the non obstante clause at s.98.

Every one of the 14 was CLOSURE-class — the edges traversal follows unbudgeted
and mandatorily (invariant 6). A wrong closure edge does not merely add noise;
it is retrieved on every query touching either endpoint, and invariant 9 says
degrade to a labelled gap, never to confident wrong. Widening a resolver with
26% error would have multiplied the error before fixing it.

**How the fix was decided, and why it is not a tuned threshold.** The obvious
repair for borrowed targets is a distance cap. The data refused it: sorted by
distance the correct and incorrect sites interleave (a correct one at 48
characters, an incorrect one at 32). Sorted by *whether a clause break
intervenes* they separate exactly — all 23 correct citations have no comma
between the marker and the citation, all 16 incorrect ones do. So the rule is
"the citation must be in the marker's own clause", which is a claim about
English syntax that can be stated and tested, not a constant fitted to 39
points. The clause break is looked for in the gaps *between* citations, never
inside one, because "sections 3, 4 and 5" is a single citation containing
commas.

Result: 54 edges of which 40 were right, to 33 edges of which 33 are right
(hand-checked, all of them). Precision 74% -> 100%, recall on correct edges
40 -> 33 with the losses all being citations to instruments outside the corpus,
where no edge is the right answer.

## 2026-08-22 — The cross-reference carve-out had no marker at all

**Decision: fix the pack, not the engine — and only after the resolver was
trustworthy.**

With resolution corrected, the Child Labour carve-out ("Nothing in Secs . 7, 8
and 9 shall apply to any establishment wherein any process is carried on by the
occupier with the aid of his family") was *still* unreachable. The reason was
not resolution. `LEGAL_MARKERS` had `nothing_shall_apply`, which requires
"nothing in **this** section" — the self-referential form. The cross-reference
form, which names *other* provisions, matched no marker in the pack, so the
resolver was never invoked on it.

This is precisely the shape invariant 11 predicts: the symptom looked like an
engine limitation ("L3's one-section window makes the relation
unrepresentable") and the cause was missing pack data. `BUILD_PLAN` had
concluded the relation "is not something the extractor can express, at any
extraction volume" — true of the *model* path, and it made the deterministic
gap easy to miss. No engine file changed to fix it.

The marker spans the citation (`ref_side="within"`) rather than stopping at
"Nothing in", so `evidence_span` is the claim itself. Its verb list is taken
from the corpus — apply, be, affect, authorise — not from imagination.

**Measured effect.** Corpus-wide it fires on 20 nodes and writes 5 edges; all
5 hand-checked correct, 0 false positives. The 15 non-firings are all correct
refusals (sub-section references, "clause (b)", spelled-out numerals). Cross-
reference linkage 62% -> 88%.

On BUILD_PLAN Phase 3's exit criterion, `phase3_exit_report.py` moves for the
first time:

    before   A 10/15   B 11/15   C 11/15     lost_exception A 3/5 B 3/5 C 3/5
    after    A 10/15   B 11/15   C 12/15     lost_exception A 3/5 B 3/5 C 4/5

The Child Labour case goes `never / never / expansion` — reached by arm C and
by neither seeds nor context expansion. The standing negative finding was
"closure traversal changed no labeled case", and that is now false. Soundness
held at 0/170 with the flat-RAG falsification at 77.1% unsound.

**Still not met.** One case is not "a clear margin" over the baseline, and the
labeled set is 15 cases. This narrows the gap; it does not close the exit.

## 2026-08-22 — Sub-section citations: ~40% of intra-document references were invisible

**Decision: recognize and resolve them via a second, section-scoped registry —
never fold them into `section_registry`.**

Measured before any code changed: `LEGAL_SECTION_REF` matches "section"
followed by a bare digit, so "sub-section (1)" — the digit sits inside
parentheses — never matched at all. Not mis-resolved; unseen. Across 25 of
the 62 corpus acts: 1323 sub-section nodes exist, 910 citations to a
*section* resolve today, 617 citations to a *sub-section* resolved to
nothing. Extrapolated corpus-wide, roughly 1500 citations — the largest
single unresolved population found so far, larger than every gap fixed in
the previous round combined.

**The registry key is `(enclosing section, enumerator)`, never the
enumerator alone.** "(1)" recurs in nearly every section of every Act; an
unscoped registry would map all 1323 sub-section nodes onto whichever "(1)"
was written first — the same shape of fabrication `LEGAL_FOREIGN_REF` exists
to prevent for section citations, at roughly 100x the scale and entirely
inside closure edges that traversal follows unbudgeted. The enclosing section
is found by walking the real `PART_OF` nesting `dge.parsing` already builds,
via a new `_enclosing_section` helper — not re-derived from document order.

**A genuine parser interaction surfaced while measuring, not invented to
justify the design.** Probing duplicate enumerators found bracketed amendment
headings (`10[17. The Chancellor.`) landing as `NodeKind.PROPOSITION` rather
than `STRUCTURAL` in at least one document (`Aligarh_Muslim_University_Act,
_1920`), which would collapse several real sections onto one ancestor. The
registry's first-writer-wins-and-warn policy degrades this safely — confirmed
non-hypothetical when ingesting the 9-document eval bundle produced three real
duplicate-enumerator warnings on `Comptroller_and_Auditor-Generals_...Act,
_1971`. Left as a documented risk, not silently patched — the footnote/heading
classification is `dge.parsing`'s concern, out of this task's scope, and
"fixing" it without corpus evidence risks the same kind of error this whole
exercise was trying to prevent.

**A second, smaller gap found by hand-checking the edges this produced, not by
guessing in advance.** "sub-sections (2) to (4)" is a range — three
provisions — and the original separator set (`,` / `and` / `or` / `&`) has no
concept of "to", so only "(2)" was captured and "(3)", "(4)" silently
dropped. 6 occurrences across the corpus, all bare digits. Added "to" to the
pattern's separator alternation and a dedicated `_expand_subsection_enumerators`
step that expands the numeric range — but only when both endpoints are bare
digits. A lettered endpoint ("(2A) to (4)") has no defined successor, so only
the two named endpoints are kept; the middle is never guessed. Same
invariant-9 discipline as everywhere else in this file: degrade to a smaller,
correct set rather than a larger, guessed one.

**Measured effect, corpus-wide (62 acts):**

```
marker edges from a citing node mentioning a sub-section     108
  bare "sub-section (N)"                                      62
  list "sub-sections (N) and (M)"                              33
  "sub-section (N) of section M"                               13
duplicate (src, dst, type) rows                                 0
```

Hand-checked by shape rather than sampled in aggregate (docs/06 §6.3
discipline): all 108 correct given the documented range-expansion and
foreign-citation exclusions. One coarse-plus-precise redundancy was found and
kept deliberately rather than patched: "sub-section (1) of section 9" produces
BOTH a precise edge to the sub-section (this fix) and, independently, a
coarser edge to section 9's heading — because the substring "section 9" is
*also* matched by the pre-existing, untouched `LEGAL_SECTION_REF` scan over
the same window. Not a fabrication (section 9 genuinely is named), but an
over-broad companion edge; flagged in `test_a_subsection_citation_qualified_
by_another_section_resolves_there`'s docstring rather than silently
suppressed, since narrowing it needs corpus evidence this task did not
collect.

**Falsifiability, measured, not assumed:** eight of ten new tests each fail
when their specific mechanism is reverted, checked by reverting all eight in
turn. `test_a_node_with_no_structural_ancestor_resolves_to_nothing` is the
exception, disclosed rather than papered over: a mutation that makes a
citing node fall back to its OWN id as scope did not fail the test, because
`subsection_registry` structurally never contains a self-referential key on
this fixture, so the lookup still returns nothing by construction rather than
by the explicit `if scope is None` guard. The guard is still the correct,
invariant-9-mandated code — this is a note about the strength of one test's
pin, not about the correctness of the mechanism it pins.

**Phase 0 density gate re-run, essentially unchanged:** chain p95 = 3,
closure density 9.8% (was 9.7%) — expected, since this gate counts nodes
carrying a closure *marker*, not resolved edges, and this change alters
resolution, not marker presence.

**Phase 3 exit report re-run: the labeled-failure arms did NOT move** —
`A 10/15  B 11/15  C 12/15`, identical to the state at the start of this
task. None of the 15 labeled cases specifically turns on a sub-section
citation. What did move, corpus-wide: self-referential exception linkage
89% -> 90% (462 -> 471 of 521 exception-shaped nodes now carry an outgoing
closure edge, +9 provisions that previously had none at all). Soundness held
at 0/170; flat-RAG falsification 78.2% unsound (was 77.1%). Ingest confirmed
idempotent (2995 edges, identical on re-run) with zero duplicate
`(src, dst, type)` rows.

**Net:** a real, load-bearing gap (~40% of a citation population) is closed
with measured precision and falsifiable tests, but it does not move Phase 3's
headline exit number on this labeled set. The next lever for that number
remains what `NEXT_STEPS.md` already names — the `Foreign_Marriage_Act,_1969`
footnote misclassification and, more broadly, growing the labeled set past 15
so a 9-point linkage improvement has cases that can register it.

## 2026-08-22 — "For the purposes of X": one formula, two targets

**Decision: split the marker rather than guess a hint, and gate the cited
variant on a clause boundary.**

`for_the_purposes_of` required the literal "this" ("for the purposes of this
section") and targeted `following`. Measured over 62 acts: 119 such
self-referential sites, and **23 sites where a citation is the direct object**
("For the purposes of clause (ii) of sub-section (1), the expenditure ...").
The old marker matched none of the 23 — so `following` was never *wrong* on
them, they were simply invisible. Same shape as the sub-section gap: an
unmatched population, not a mis-resolved one. Added
`for_the_purposes_of_referenced` with `target_hint="referenced"`, disjoint
from the original by the literal "this".

**The precision problem, found by hand-checking every edge rather than
sampling.** The first version wrote 21 edges, of which ~5 were wrong — all one
shape: the phrase used *referentially* inside a noun phrase ("any tax
authority **prescribed for the purposes of** sub-section (1) **may** require
...") rather than as a scoping preface. Those name a provision but define
nothing for it.

**The discriminator is a clause boundary, and it was tested before it was
adopted.** "For the purposes of X" scopes a definition when the citation
prefaces a rule (comma or dash follows) and is merely referential when it runs
on into a verb or noun phrase. Hand-labelling the population separated
**10/10** scoping uses from **4/4** referential ones on that rule alone. This
is a statable claim about English syntax, not a constant fitted to a sample —
the same discipline as `clause_break_pattern`. Result: 21 edges -> **16, all
16 hand-checked correct**, with no true positive lost.

The lookahead deliberately consumes nothing after "of ", so `ref_side` stays
`"after"` and `foreign_ref_pattern` still sees a trailing "of the Indian Penal
Code" — several of the 23 cite other instruments, and a consuming ("within")
match would have hidden that from the guard and fabricated a local target.

**A latent `edge_id` bug this exposed, fixed at source.** Bundle write failed
with `UNIQUE constraint failed: edges.edge_id`. Cause: `edge_id` was keyed on
`dst`, but `MARKER_ORIENTATION` **inverts** DEFINES — there `dst` is the
citing node itself, so every target resolved from one node produced an
identical id. Latent until a DEFINES marker first resolved `referenced` with
more than one target ("for the purposes of sub-sections (1) and (2)"). Now
keyed on the resolved `target`; for every other edge type `dst` *is* the
target, so no existing id changes. Pinned by
`test_a_multi_target_defines_citation_produces_distinct_edge_ids`.

**Measured effect.**

```
edges written (62 acts)                16, all hand-checked correct
sites matched but resolving to nothing  4  (foreign-Act citations — correct)
Phase 0 density gate                    PASS, p95=3, 9.9% (was 9.8%)
idempotent                              yes, 3000 edges
duplicate (src,dst,type)                0
tests                                   155 pass
```

**The Betwa `lost_scope` case: the edge now exists, and the case still does
not move.** The gold node (`(2) For the purposes of clause (ii) of sub-section
(1), the expenditure on the Rajghat Dam ...`) had **zero inbound edges**
before this change and now has exactly the right one — an inbound `defines`
from sub-section (1), so a seed landing on (1) walks forward to the
definition. The labeled arms are nevertheless unchanged at
`A 10/15 B 11/15 C 12/15`.

That is a **seeding** failure, not an extraction or traversal one, and it
promotes standing blocker 3 from a caveat to the operative constraint:
seeding is still lexical because `BAAI/bge-large-en-v1.5` is not cached and
the documented network stall blocks the download. The traversal cannot start
from sub-section (1) if lexical seeding never ranks it. The graph is now
correct for this case and the retrieval front-end is what is short — a
different component from the one every previous round pointed at.

**Honest limit:** two rounds running, a real measured extraction gap has
closed without moving the headline number. That is now three separate
findings (sub-section citations, cited "for the purposes of", and this) all
pointing at the same two places: the labeled set is 15 cases, and seeding is
lexical. Neither is an extraction problem, and continuing to fix extraction
is unlikely to move the exit criterion.

## 2026-09-05 — Seeding unblocked: the margin survives an honest baseline

**Decision: measure all three seeding modes on one bundle before touching the
extractor again, and report the margin decomposition rather than the headline.**

Three prior rounds closed real extraction gaps without moving `A 10/15 B 11/15
C 12/15`. `NEXT_STEPS.md` named the cause — seeding was still lexical — and
made unblocking it the precondition for any further extraction work. Two
environment facts turned out to be wrong in the docs, and both mattered.

**The bundle was measuring a corpus with a hole in it.** `node_vectors` held
986 rows against 1601 nodes. `embed_bundle` writes per document, all-or-nothing,
so this was not thin coverage but one missing document: `Mines_Act,_1952`, 615
nodes, the largest in the corpus — and the holder of **4 of the 15 labeled
failure cases**. The Sep 2 embed run stopped before it. Any hybrid measurement
taken before today would have given those four cases no dense retrieval at all
and silently reported it as a traversal result. Re-embedded to 1601/1601.

**The vectors were written by an undocumented model.** Those 986 rows are
`fastembed:BAAI/bge-small-en-v1.5` (384-dim), not the `bge-large-en-v1.5` that
`README.md` and `HANDOFF.md` document. No `decisions.md` entry recorded the
substitution. Logged here because a 384-dim quantized model as the seeding
substrate is exactly the kind of silent drift the version key exists to prevent.

**`bge-large` was never a code problem and is no longer an environment one.**
Its ONNX weight blob was a 50MB `.incomplete` stub — three sessions read that as
a network stall. It downloaded and loaded on the first retry today (1024-dim,
5.5 min). The blocker was transient, and treating it as standing cost three
rounds of misdirected extraction work.

**Result — one bundle, three seeding modes, same 15 labeled cases.**

```
seeding            A      B      C     C-A    flat-RAG unsound   traversal
lexical           10/15  11/15  12/15   +2    78.2% (448 viol)   0/170
hybrid            10/15  11/15  12/15   +2    80.0% (694 viol)   0/170
hybrid-rerank     12/15  13/15  14/15   +2    75.3% (588 viol)   0/170
```

**Reranking, not dense retrieval, is what pays.** Plain hybrid is a wash: it
fixes the Betwa `lost_scope` case — confirming the 2026-08-22 prediction that
it was a seeding failure and not an extraction one, since that gold node had
already been given the right inbound edge — but loses a Mines `lost_exception`
case, netting zero. Adding the reranker recovers the Mines case, keeps Betwa,
and gains a `needs_aggregation` case: +2 on every arm.

**The margin decomposes, and the decomposition is the finding.** At
hybrid-rerank, `B - A = +1` is the Actuaries Council case via budgeted context
expansion, and `C - B = +1` is the Child Labour case, reached by closure
traversal and by nothing else (`never / never / expansion`). That single case is
the product claim in isolation: an exception no amount of seeding quality
reaches, recovered only by the unbudgeted reverse-index walk. Invariant 6's
split between budgeted and unbudgeted traversal earns exactly one case here,
and it is the case the system exists for.

**Against the Phase 3 exit criterion** (read with the CORRECTION below —
this measures a deterministic graph, not an L3 one). Soundness is 0/170 with the
falsification arm at 75.3% unsound, so the check is not vacuous. Those 170
queries are section headings sampled deterministically (seed 20260821) from
real corpus text, not human-written questions; the 15 labeled cases do carry
real questions, and their gold spans were re-checked as 15/15 verbatim in the
source files. Recall is
14/15 against a baseline of 12/15 — and arm A here *is* `docs/06` §6.3
baseline 2 (normalized nodes + hybrid + rerank, no traversal), not the weaker
lexical bar every previous measurement used. The margin did **not grow** with
better seeding; it held at +2 across all three regimes while the baseline itself
rose two cases. Surviving a stronger baseline is the stronger claim, and it is
the one the exit criterion actually asks for.

**CORRECTION (same day, before this entry was acted on): the measured graph
contains no L3 edges at all.** Checked after the fact rather than before, which
is the process failure here. `select count(*) from edges where
provenance='model'` returns **0** on both bundles; all 3000 edges are
deterministic — 2009 `structural` context, 904 `pattern` closure, 87
`structural` closure. The "L3 has run, 123 model edges on Groq" state described
in `NEXT_STEPS.md` belonged to an earlier bundle that no longer exists; the
2026-09-02 rebuild did not re-run L3, and this entry above repeated that framing
without verifying it against the bundle actually being measured.

The numbers are unaffected and remain real — 14/15 and 0/170 were produced by
real models over a real corpus. What changes is what they are evidence *for*:
closure traversal reaches 14/15 and stays sound **on a purely deterministic
graph**, with the pattern extractor in `domains/legal.py` supplying every
closure edge. That is a cleaner and cheaper result than the one claimed, and a
narrower one: it says nothing about whether L3 works, because L3 contributed
nothing. The Child Labour case that only closure traversal reaches is carried by
a `pattern` edge, not a model edge.

**Honest limit, unchanged and now binding.** n=15. A +2 margin on 15 cases
cannot be significant however stable it looks across three regimes, and the
stability itself is weak evidence — the same two cases move each time. The
remaining `never` in arm C is one Mines Act `lost_exception`. The taxonomy at
15/50 is no longer one of two candidate next steps; it is the only thing
standing between this result and a defensible one.

## 2026-09-05 — L2 was unbounded per document, and silently lost one

**Decision: cap the per-call text count via an opt-in adapter attribute, make
the stage resumable, and keep whole-document calls the default.**

`embed_bundle` passed an entire document to `embed_documents` in one call. The
count of texts was therefore unbounded, and on `Mines_Act,_1952` (615 nodes,
the corpus's largest) with `bge-large-en-v1.5` it drove ONNX Runtime's arena to
**3.9GB anon-rss and was OOM-killed** — confirmed in the kernel log, not
inferred. The same document had failed the same way on the 2026-09-02 run with
`bge-small`. Both times the failure was invisible: writes happen per document,
so the eight completed documents persisted and the run simply ended.

**The cost of that invisibility is the point.** The missing document held 4 of
the 15 labeled failure cases. A hybrid-seeding measurement taken against that
bundle would have given those four cases no dense retrieval whatsoever and
reported the result as a property of traversal. This is precisely the failure
mode invariant 9 exists to prevent — it degraded to confident wrong rather than
to labeled low confidence — and it violated definition-of-done 3, since a
kernel kill records no reason anywhere.

**Why batching is opt-in rather than always on.** The original one-call-per-
document design was not an oversight: a contextual embedder (`VoyageEmbedder`)
uses that grouping to contextualize each unit against its true siblings, so
splitting it silently changes what the vectors mean. Chunking unconditionally
would have fixed the memory bug by breaking the embedder the stack table
actually prefers. Instead `dge.interfaces.Embedder` documents an optional
`max_batch: int`; `FastEmbedEmbedder` declares 32 because it is explicitly
non-contextual (it embeds each text independently), and `VoyageEmbedder`
declares nothing and still receives whole documents. The knowledge lives in the
adapter, which is the only place that knows whether grouping carries meaning.

**Resumability, because L2 over a real corpus gets interrupted.** Nodes already
holding a vector from the same `model_id` are skipped. Without this, recovering
from the OOM meant re-embedding eight completed documents (~30 min) before
reaching the missing one — and then dying on it again. The skip is keyed on
`model_id`, so switching embedders still re-embeds everything rather than
silently keeping the old model's vectors. Re-running start to finish still
produces identical output for identical input, so idempotence (definition-of-
done 1) is unaffected.

**A docstring that was wrong in a load-bearing way.** `vector_model_ids` claimed
L2 rows are "keyed `(node_id, model_id)` conceptually" and that `INSERT OR
REPLACE` "only overwrites same-node-same-model rows". The schema PK is
`node_id` alone, so a new model replaces whatever vector a node held. Corrected,
because the resume logic depends on reading that relationship correctly.

**Falsification, measured rather than asserted.** Four mechanisms, each reverted
in turn to confirm a test fails:

```
revert chunking (step = whole doc)        -> 2 fail  (max_batch, failure-path)
revert the resume filter                  -> 1 fail  (resumed-run skip)
make the skip model_id-agnostic           -> 1 fail  (different-model re-embed)
chunk even without a declared max_batch   -> 1 fail  (contextual guarantee)
```

That last one guards the direction the others do not: it fails if batching ever
stops being opt-in, which is what would quietly break Voyage.

163 tests pass (was 157), `ruff` and `mypy --strict` clean.

**Verified on the real path, not only against `FakeEmbedder`.** Working
agreement 1 forbids claiming this on unit tests alone, so the shipping
`embed_bundle` was made to re-embed the exact document that killed it —
`Mines_Act,_1952`, 615 nodes, real `bge-large-en-v1.5`, vectors deleted first:

```
documents  1        nodes 615      skipped 986 (already embedded)
elapsed    553s     rc=0
peak RSS   2.63 GB  (the unbatched run died at 3.90 GB anon-rss)
```

Peak stayed flat rather than growing with document size, which is the property
`max_batch` is supposed to buy. The resume path was measured separately against
the finished bundle: **1601 skipped, 0 embedded, 8.9s**, where a full re-run is
~45 min. Both mechanisms exercised through the CLI, on the real model.

## 2026-09-05 — A labeled set sized to detect an effect, not to look thorough

**Decision: mine cases from closure edges, generate questions with the gold
withheld, and keep only what the STRONG baseline fails.**

The 15-case set could not answer the question it existed for. Arm C's context
structurally contains arm A's, so C can never lose a case A wins; the comparison
is therefore a count of RESCUES, and there were 2 (exact binomial p = 0.50).
The binding quantity is not total cases but `m`, the number the strong baseline
fails — only those can carry evidence. The old set had m=3, because it was
written against a naive baseline that reranking has since outgrown, leaving 12
cases at the ceiling where nothing can be measured.

**Corpus widened to 30 acts** (4708 nodes, 8871 edges): `exception_of` 81 -> 226,
`supersedes` 5 -> 21. Embedded with `bge-small-en-v1.5`, since the same-day
measurement showed 384-dim and 1024-dim give identical arms on every category.
`Regional_Rural_Banks_Act,_1976` parsed to zero nodes and was held
`review-pending` — invariant 9 doing its job, so the bundle is effectively 29.

**The generator never saw the gold span.** Questions were written from the RULE
and its heading only, exported as three prompt files and produced externally
(the Groq key was dead at that point). Verified mechanically: 0 of 120 gold
spans appeared anywhere in the exported prompts. A question paraphrased from the
carve-out would test lexical matching rather than retrieval, and no prompt
instruction can be trusted to prevent that — hence a coded overlap guard, which
rejected 8 of 120.

**Selection consulted arm A only.** Arm C is never called in
`build_hard_cases.py`; selecting on C-success would have chosen the cases that
flatter the graph and manufactured the result being measured.

**The funnel, and the number that changes the design.**

```
questions generated            120
rejected: echoed the gold        8   (overlap guard)
rejected: gold not verbatim      0
rejected: BASELINE FOUND IT     23
survivors (baseline failures)   89   <- 79.5% baseline-failure rate
```

79.5%, against 20% for the hand-written set. Mining from closure edges targets
the hard population an order of magnitude more efficiently than writing
questions by hand — this, not the labeling effort, was the real bottleneck.

**The answerability judge earned its place, and its rejections are the
interesting part.** 89 -> 49 kept. The rejections expose a mining artifact worth
recording: a rule often carries SEVERAL provisos, and mining pairs the question
with whichever carve-out the edge happens to point from. One rejected case asked
at what age the Comptroller and Auditor-General must vacate office and was
paired with the *resignation* proviso rather than the age-65 one — the human
set's version of that same case has the correct gold. The judge caught it. Nine
cases hit Groq's TPM cap and were kept unverified rather than discarded (an
infrastructure failure is not evidence); re-judged at slower pacing, 5 of those
9 were also rejects.

**Final: 49 cases, 49/49 verbatim-grounded, 21 acts — and badly skewed.**

```
lost_exception          47  (96%)
lost_scope               2  (4%)
lost_referent            0
needs_aggregation        0
topic_not_proposition    0
wrong_version            0   <- all 4 candidates rejected by the judge
```

**This must be reported as two instruments, not one.** The new set has the power
to prove or kill the exception claim (95% CI half-width ~±13% on the rescue
rate; it clears significance even if the true rescue rate is as low as 10%,
where the old set could not clear it at 67%). It says NOTHING about five of the
six failure modes. In particular `wrong_version` is empty, so the half of the
product claim about superseded versions is untested — a corpus limit, not a
sampling one: only 2 amendment acts exist in the 62 available.

**Honest limits.** Questions are LLM-written; withholding the gold and guarding
overlap bounds the echo risk but does not make them user questions. The set is
enriched by construction, so `A 0/49` is definitional and there is no unbiased
recall number in it. And it still needs human review — the judge says the gold
is relevant, not that a lawyer would accept it as the answer.
