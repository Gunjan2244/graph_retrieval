# BUILD_PLAN.md

Sequenced tasks. Each has an exit criterion that is checkable without judgement.
Do not start a task until the previous one's exit criterion passes.

**Before Phase 1: run Phase 0.** It is measurement, not code, and it can
invalidate the whole design. Skipping it is the most likely way this project
wastes months.

---

## Phase 0 — Validate the assumption (no product code)

The traversal design rests on closure edges being **sparse**. If exceptions and
conditions are densely interlinked in the target corpus, bounded traversal does
not work.

- [x] `scripts/phase0_density.py` — SHIPPED. Runs with no dependencies against
      a directory of .txt/.md. Uses the `legal` pack's markers.
      `python scripts/phase0_density.py --corpus ./samples --domain legal`
- [x] Run it on ~50 REAL documents. Indian bare acts are available in clean text,
      so this needs no parser. Do not judge the design on synthetic samples —
      `samples/sample_act.txt` is all-markers and deliberately FAILS.
      DONE: `scripts/fetch_corpus.py` pulls real Central Acts from the
      `mratanusarkar/Indian-Laws` HF dataset (India Code itself has no bulk
      API). 60 real acts fetched to `corpus/indian-acts/` (2.2MB, zero
      fetch errors). Result: **PASS** — chain p95 = 3, closure density = 9.7%
      (well under the 25% gate). See `phase0_result.json` /
      `phase0_output.txt` for the full run. Caveat: the printed
      context:closure ratio (2.7x) reads far below the "order of magnitude"
      language below — investigated and it's a measurement artifact, not a
      design problem: every "context" hit in this corpus is `PART_OF`
      structural nesting (`sub_section`/`clause`, which fire on ~every
      numbered provision by construction), because the `legal` pack
      currently defines *no* context-classed markers (`elaborates` /
      `exemplifies` / `cites`) — only `illustration` as a structural unit,
      and this sample happened to contain zero. The two criteria the script
      actually gates on (chain p95, closure density) are unaffected and pass
      clearly; the qualitative "everything's loosely related" claim isn't
      measurable by a lexical-marker script at all — that needs L2 (Stage C).
      RE-RUN after the parser rewrite (PARSER_PLAN.md Task 8): the original
      PASS was measured on the old blobby/shredded substrate, so it needed
      re-checking on the ~10x-more-granular real substrate before trusting
      it. Re-ran `scripts/phase0_density.py` against all 62 fetched acts on
      the rewritten parser: 13,050 units (vs a much smaller, mis-shapen count
      before) — **chain p95 = 3, closure density = 9.7%**, both essentially
      unchanged from the original result despite the substrate being rebuilt
      from scratch at correct granularity. This is the load-bearing check for
      the whole rewrite: the traversal design's core assumption (closure
      chains are short and sparse) holds on real node granularity, not just
      on the accidental granularity blank-line splitting happened to produce.
- [x] Hand-check 30 marker hits for precision (target >= 0.8).
      DONE: 32 hits hand-checked (script overshoots the requested 30
      slightly by design), ~93-97% true positive. Found and fixed one real
      misfire: the `affirmed` marker (`affirmed|upheld|approved` + `in|by`)
      matched ordinary administrative "approved in/by" language, not
      judicial affirmation — `approved` dropped from the pattern in
      `src/dge/domains/legal.py`. Tests (20/20) and the density gate both
      re-verified passing after the fix. `for_the_purposes_of` has a milder,
      unfixed precision issue (fires on generic "for the purposes of this
      Act" phrasing that doesn't always precede a definition) — left as-is
      since it's already `Confidence.MEDIUM`, meaning Phase 3's own design
      already routes it through L3 verification before it becomes a
      committed edge, not a direct one.
- [ ] `scripts/phase0_taxonomy.py` — 50–100 real failing queries, labeled by
      cause: lost referent / lost scope / lost exception / wrong version /
      needs aggregation / topic-not-proposition.
      PARTIAL: script + validator built (enforces verbatim-grounding on
      every case, same discipline as invariant 10). 15/50 minimum seeded so
      far in `corpus/failure_taxonomy.jsonl`, all real and verbatim-checked
      against the fetched acts, spanning all six causes. Not yet at target —
      left unchecked deliberately rather than padded to look done.

**Exit:** closure chain length p95 ≤ 3, and closure edge density an order of
magnitude below context edge density. Failure taxonomy documented with counts.

**If p95 > 3 or density is comparable — stop and redesign traversal.**
**If <20% of failures are exception/version — Phase 3 is not the priority.**

---

## Phase 1 — Substrate and baseline retrieval

This phase is independently shippable and is the honest baseline the graph must
beat later.

- [ ] `sql/schema.sql` applied to both Postgres and SQLite from one file.
- [x] `PlainTextParser` rewritten against the real 62-act corpus — Docling
      adapter for PDF/DOCX still open, tracked separately below.
      DONE: design and evidence in `PARSER_PLAN.md`. The old parser split on
      blank lines alone and broke in two opposite ways depending on dialect —
      whole sections collapsing into single nodes on one dialect (max 8375
      chars), wrapped line fragments shredding into scraps on the other
      (median 69 chars) — and both scored `parse_confidence == 1.0`, so
      invariant 9 never fired. Replaced with a line classifier + one reflow
      rule (a unit starts at an enumerator/keyword line and absorbs
      continuation lines; a blank line is absorbed only while what's open is
      still mid-wrap, judged from bareness AND sentence-completeness, not
      blank-line runs) plus a real nesting stack (chapter > section >
      sub-section > clause, `PART_OF` to the immediate parent, not the
      nearest heading). Verified with `scripts/parser_corpus_report.py`
      across all 62 acts: median-of-medians 137 chars, worst max 1493 chars
      (was 8375), 60/62 documents parse at full confidence, the 2 genuinely
      ambiguous ones (`Regional_Rural_Banks_Act,_1976`,
      `All_India_Services_Act,_1951`) correctly gate below the 0.5 review
      threshold instead of silently passing. `edges.py`'s `preceding`/
      `referenced` target resolution now walks the real parent/sibling
      structure instead of flat document-order adjacency — the two named
      garbage edges (`exception_of` "3. Act not to apply" → "2. Definitions";
      "Chapter II / 5. Chief Inspector" → "4. References to time of day") are
      gone, verified directly against `Mines_Act,_1952`. 7 new regression
      tests in `tests/test_parsing.py`; 67/67 tests pass, `ruff`/`mypy
      --strict` clean.
      FOLLOW-UP FIX: footnote lines were still classified `footnote`/
      `footnote?` at the line level but emitted as `NodeKind.PROPOSITION` —
      indistinguishable from real operative text, so `Mines_Act,_1952` alone
      produced 132 footnote nodes in the graph, and `edges.py`'s sibling-chain
      resolution let one become a closure-edge target directly (a proviso's
      `exception_of` resolved to an amendment footnote instead of the rule it
      modifies). Fixed with a distinct `NodeKind.FOOTNOTE` (`model.py`),
      excluded from the sibling chain `_build_cursor` builds in `edges.py`
      (so real siblings link past an interposed footnote rather than through
      it) and from marker/structural-unit/definition/mention matching
      everywhere else `NodeKind.STRUCTURAL` was already excluded
      (`edges.py`, `lexicon.py`). Verified: 0 closure edges touch a footnote
      node across all 62 corpus acts (was 1+ on `Mines_Act,_1952` alone).
      2 new regression tests (`tests/test_parsing.py`,
      `tests/test_edges.py::test_footnote_node_is_never_a_closure_edge_target`),
      the latter confirmed to fail without the `_build_cursor` fix.
- [x] Parse validation + confidence score. Below threshold → `review` status,
      pipeline halts for that document.
      DONE, folded into the parser rewrite above: confidence combines decode
      damage, fraction of units with no structural marker, fraction of
      non-monotonic heading-shaped (likely footnote) lines, and outlier unit
      length — see `PlainTextParser.parse` in `src/dge/parsing.py`. Thresholds
      calibrated empirically against the corpus (`scripts/parser_corpus_report.py`),
      not guessed.
- [ ] Document classifier routing to parser configs (born-digital, multi-column,
      scanned, table-heavy).
- [ ] L1 normalizer: coref, self-contained restatement, inherited context
      (section path, temporal scope, subject, conditions), unit/date
      normalization, assertive flag.
- [x] L2: embedder protocol + adapter; sparse index; hybrid retrieval with RRF;
      cross-encoder rerank.
      - `node_vectors` table in `sql/schema.sql`: base64-packed float32 in a
        TEXT column, same portability trick `document_blobs` already uses, so
        one DDL still works on SQLite and Postgres with no dialect branch.
        Brute-force cosine, no ANN index — fine at this corpus size, swap in
        sqlite-vec/pgvector later as a pure accelerator.
      - Two adapters behind the one `Embedder` Protocol:
        `adapters/embed_local.py` (fastembed, ONNX, offline, no key) and
        `adapters/embed_hosted.py` (Voyage contextualized API, stdlib urllib
        only). NOTE: CLAUDE.md's stack table names BGE-M3 for self-host, but
        fastembed's model zoo does not ship it — substituted
        `bge-large-en-v1.5` (same lineage, but English-only and 512-token vs
        BGE-M3's multilingual 8192). Real fidelity gap, flagged in the module
        docstring rather than silently drifting from what was settled.
      - `retrieval/hybrid.py`: RRF (not score averaging — cosine and TF-IDF
        scores are not on comparable scales). Pure fusion logic, tested with
        fake rankings, no model needed.
      - `dge embed` CLI + `--use-vectors` on `dge query`. Query-time embedder
        is chosen from the stored `model_id`, since embedding a query with a
        different model than the corpus is silent nonsense.
      - Verified end-to-end with the real model on `samples/sample_act.txt`:
        on a deliberately paraphrased query ("handed over" for "transfer"),
        sparse finds 3 nodes and misses the operative rule; dense finds 6 and
        ranks the section head first; hybrid keeps both signals. Traversal
        result is unchanged (closure still pulls both provisos, soundness
        still passes) — vectors improve seeding, they do not touch the
        guarantee.
      - GOTCHA worth knowing: a bundle carries whatever schema it was written
        with, so bundles created before `node_vectors` existed fail
        `dge embed` with `no such table`. Re-ingest to fix. There is no
        migration path for existing bundles yet.
      - Rerank (the previously-missing piece): `Reranker` protocol in
        `interfaces.py` implemented by two adapters, same pattern as the
        embedder — `adapters/rerank_local.py` (fastembed's ONNX
        cross-encoder, offline, no key) and `adapters/rerank_hosted.py`
        (Voyage AI's `/v1/rerank` endpoint, stdlib urllib only, contract
        verified against Voyage's docs). NOTE: CLAUDE.md's stack table names
        BGE-reranker-v2; fastembed's cross-encoder zoo does not ship a v2 BGE
        model (checked directly against `TextCrossEncoder.list_supported_models()`)
        so `BAAI/bge-reranker-base` is substituted — same gap shape as the
        embedder's BGE-M3 substitution, flagged in the module docstring, not
        silently drifted from.
      - `dge.query.rerank_seeder` composes with any `Seeder` (lexical-only or
        the dense+sparse hybrid one) rather than being a variant of hybrid
        seeding: it over-fetches `top_k * candidate_multiplier` candidates
        from the base seeder, reranks them, and truncates to `top_k`. Wired
        in as `--rerank` on `dge query`, independent of `--use-vectors`.
        Unit tested against a fake reranker (candidate over-fetch, node
        resolution, truncation) with no network or model dependency, same
        discipline `test_vectors.py` uses for the embedder.
      - Verified end-to-end with the real local cross-encoder
        (`Xenova/ms-marco-MiniLM-L-6-v2`, swapped in via `--rerank-model` for
        the smoke test — `bge-reranker-base` itself is ~1GB and this
        sandbox's `/tmp` is a small tmpfs, not a statement about the default)
        on `samples/sample_act.txt`: lexical-only seeding for "transfer made
        in the ordinary course of business permitted" pulls in an off-topic
        node at rank 3; `--rerank` correctly promotes the node containing
        that exact clause into the seed set instead. Traversal result stays
        sound either way — rerank improves seeding, it does not touch the
        soundness guarantee.
- [ ] Postgres job queue; per-document ingest ledger with stage timing and cost.

**Exit:** on the Phase 0 query set, this beats naive fixed-size chunking on
recall@10. Parse gating demonstrably blocks a deliberately corrupt PDF.

---

## Phase 2 — Term symbol table

- [ ] Definition-site detection using `LEGAL_DEFINITIONS`. Pattern-based, no model.
- [ ] **Store `means` vs `includes` distinctly on the term node.** Exhaustive vs
      illustrative is litigated constantly; flattening them produces confidently
      wrong scope answers.
- [ ] Scoped definitions: `"For the purposes of this section"` shadows the
      Act-level glossary. This is the headline symbol-table feature in statutes.
- [ ] Lexicon build: canonical form, variants, defining span, one-sentence gloss.
- [ ] Mention linking via `pyahocorasick` in one streaming pass.
- [ ] Scope resolution: section → document → corpus → glossary. Shadowing
      supported. Terms keyed `(surface_form, scope)`.
- [ ] `lint(document)`: undefined terms, unused definitions, circular
      definitions, shadowing conflicts.

**Exit:** definition-site precision ≥ 0.9 on a hand-labeled set of 100. Lint
output reviewed by a domain expert and called useful.

---

## Phase 3 — Closure edges and the soundness guarantee

Three edge types only: `supersedes`, `exception_of`, `defines`.

Legal specifics (`docs/07-legal-domain-pack.md`): provisos and Explanations are
CLOSURE; Illustrations are CONTEXT; `distinguished_by` is CLOSURE; repealed
provisions are LABELLED, not dropped, because savings clauses preserve prior
operation.

- [ ] Deterministic edges first: section hierarchy, adjacency, table↔caption,
      explicit cross-references, footnotes, citations, amendment headers.
      PARTIAL: hierarchy, adjacency, cross-references, footnotes and markers
      are built (`src/dge/edges.py`, `src/dge/parsing.py`). Table↔caption and
      amendment-header→parent are NOT — no table extraction exists at all
      (needs the Docling adapter, still open in Phase 1).
- [x] Evidence-span validator — discard any edge whose span is not verbatim in
      the input window.
      DONE, and built FIRST, before any model call existed — everything below
      is only worth having if this holds. `src/dge/l3/evidence.py`, 15
      adversarial tests in `tests/test_evidence.py`. Three verdicts rather
      than two: EXACT (byte-for-byte substring), REFLOWED (matches after
      collapsing whitespace, including the U+00A0 this corpus is full of and
      the internal newlines PARSER_PLAN.md Decision 3 leaves inside
      `node.raw`), REJECTED. **REFLOWED is accepted and this is a deliberate
      decision**: exact-only rejects true edges without catching a single
      fabrication, because the window a model sees contains hard-wrapped lines
      and every model returns them reflowed. What makes it safe is that the
      check returns the WINDOW's own slice, so what lands in
      `edges.evidence_span` is verbatim substrate text regardless of how the
      model rendered it — pinned by
      `test_recovered_span_always_slices_the_window_for_every_accepted_case`.
      Everything else stays strict: paraphrase, near-miss, case change
      (reported distinctly as `case_mismatch_only`), spans from another
      section, and spans under 12 chars are all rejected. The length floor
      matters — without it "the" is a valid citation of any legal text ever
      written. `dge.edges.validate_evidence_span` now delegates to the same
      function so the pattern and model paths cannot drift apart on what
      "verbatim" means.
- [x] Cost gate: `pack.should_run_l3(text)` before any L3 call.
      DONE and MEASURED — `scripts/phase3_gate_report.py`, run against all 62
      acts on the rebuilt substrate. **Phase 0's 22.3% does not transfer, and
      the honest number is worse than it looks:**

        gate at NODE granularity      30.6% of nodes, 37.8% of characters
        gate at SECTION granularity   54.0% of calls, 76.0% of characters

      The node number is the one comparable to Phase 0 (22.3% → 30.6%,
      explained by the substrate rebuild). **The section number is the one
      that bills.** L3 runs one section per call (docs/05 5.3) and a section
      is admitted if any node in it carries any gate term, so aggregation
      destroys most of the saving: the gate skips 46% of calls but only 24%
      of input tokens, and the sections it admits are the long ones.
      This is not a bug to route around — it is what statutory text is like,
      and it is the same property (docs/07 7.1) that makes law the right
      wedge. The lever is pack data, not engine code (invariant 11): 178 of
      1272 admitted sections are admitted by `'provided'` alone, 146 by
      `'subject to'` alone. Tightening those two is the first cost move and
      is left for Phase 6; neither has been measured for precision yet.
- [x] `EdgeExtractor` protocol + LLM adapter. Structured output, closed enum,
      one section per call, section path + document summary in context, explicit
      `null` option, mandatory `evidence_span`.
      **BUILT AND TESTED AGAINST A FAKE ONLY — never run against a real
      model.** Ticked for the plumbing, which is real and verified; the
      quality question this stage exists to answer is untouched, and the exit
      criterion below is correspondingly still open. `src/dge/l3/`:
      `sections.py` (windows + gate), `prompt.py` (prompt program +
      `prompt_hash`, stdlib-only), `schema.py` (Pydantic, closed enums
      injected into the wire schema for BOTH the type field and the unit
      labels), `run.py` (orchestration), and `src/dge/adapters/extract_llm.py`
      (LiteLLM — the only file that knows a vendor exists; Groq / Gemini /
      Ollama are `--model` strings, not code paths).
      The closed enum is exactly the three types this phase's header names —
      `exception_of`, `supersedes`, `defines` — plus an explicit `none`.
      Units are shown as `[N1]`-style labels and node ids are never shown, so
      a label the model cannot guess is an endpoint it cannot fabricate.
      Nothing downstream trusts the adapter: `run.validate_candidate`
      re-checks the type against the enum, re-checks both endpoints against
      the window it built, and re-checks the evidence span, so a provider that
      ignores `strict` (or a local Ollama with no schema support, which falls
      back to JSON mode) can produce junk that is discarded, never junk that
      is stored. 16 adversarial tests in `tests/test_l3_extract.py`,
      7 recorded-response tests in `tests/test_l3_adapter.py`, and
      5 bundle round-trip tests in `tests/test_l3_bundle.py`.
      Wired as `dge extract`, with `--dry-run` pricing a corpus with no key
      and no network.
- [x] Pattern edges from `LEGAL_MARKERS`. STRONG-confidence hits become edges
      directly; MEDIUM become candidates for model verification.
      DONE. Verification is AGREEMENT, not a second prompt: a MEDIUM marker
      edge is confirmed when the L3 pass independently proposes the same
      (src, dst, type) from the same window. Three outcomes —
      confirmed → `Provenance.VERIFIED` with the model stamped on it;
      contradicted → KEPT with confidence halved to 0.3 so it falls below
      the default traversal floor of 0.5 (invariant 5: filter at traversal
      time, never delete at ingest); never examined (the gate skipped that
      section) → untouched, because no model formed an opinion and the graph
      must not depend on the cost gate's mood. STRONG edges pass through
      untouched — a drafting convention beats a model's opinion about one.
- [x] Two competing non obstante clauses = a real conflict. FLAG it; never
      silently pick one.
      DONE — `src/dge/l3/conflict.py`, 12 tests. **Representation decision: a
      conflict is not an edge type and not a node.** Where both claims resolve
      it IS the cycle the `supersedes` edges already form; where a claim is
      act-wide ("notwithstanding anything contained in this Act") it resolves
      to no single node — an edge to every node is a hub, not information —
      and the derived finding is the whole representation. Rejected
      alternatives and why, in the module docstring: a `conflicts_with` edge
      type would restate what the graph already carries and would need a
      `policy.py` entry (invariant 11 leak); a synthesised conflict node would
      have `raw` that exists in no document (invariant 1) or copied from both
      (invariant 4); a winner field is the one thing docs/07 forbids outright.
      **Correction to the obvious claim, measured not assumed:** "closure
      traversal pulls the competitor in anyway" is only half true. A
      `referenced` marker resolves to the named section's HEADING while the
      claim sits in a sub-section, so the cycle is between sections, not
      clauses. Seeding a section heading does reach the competitor on the
      reverse index; seeding the bare clause does NOT, because the hop from a
      clause to its own heading is `part_of`, a budgeted CONTEXT edge. Both
      halves are pinned in `tests/test_conflict.py`. So the finding does real
      work for clause-level seeds, and `dge query` reports it beside the
      soundness verdict.
      **OPEN, deliberately not decided here:** whether a closure relation
      asserted against a section should propagate to that section's children.
      Making `part_of` closure-traversable would unify the budgeted and
      unbudgeted halves behind one mechanism, which invariant 6 forbids doing
      casually. That needs its own decision, not a side effect of this module.
      Also fixed while measuring: `non_obstante` required "contained" before
      "to the contrary" and so missed "notwithstanding anything TO THE
      CONTRARY CONTAINED in this Act" entirely — 170 → 181 matches across the
      corpus, conflicts found in 1 → 2 documents. Regression test added.
- [x] Traversal: closure to fixed point on the reverse index, non-optional
      inclusion; context via best-first frontier with the degree penalty.
      DONE: `src/dge/traversal/graph.py` (Graph Protocol + FixtureGraph),
      `expand.py` (`closure_fixpoint` / `context_frontier`, kept as two
      functions per invariant 6). Direction is resolved PER EDGE TYPE, not
      globally — `exception_of`/`supersedes` walk the reverse index while
      `defines` walks forward, and a single global direction would break one
      of them. Verified falsifiable: deleting the `incoming()` walk fails 7
      tests including every soundness test.
- [x] Assembly in document order with inherited-context prefixes and inline
      gloss splicing.
      DONE: `src/dge/traversal/assemble.py`. Sorts by `(doc_id, seq)`, never
      retrieval rank; superseded nodes labeled, not dropped.
- [x] **Soundness check**: for every cited node, verify inbound closure edges
      were in context; expand and re-run if not.
      DONE: `src/dge/traversal/soundness.py`, exposed as
      `dge.query.verify_answer`. Reuses `expand.closure_neighbors` so the
      check and the traversal cannot drift apart in what counts as a closure
      neighbour. Demonstrated on `samples/sample_act.txt`: seeding on the
      rule alone ranks its proviso 4th at 0.234 vs the rule's 3.448 (flat
      retrieval misses it at any sane cutoff), closure pulls it in, and a
      flat-RAG answer citing only the rule is reported UNSOUND with the exact
      node to add.

**Exit:** soundness violation rate = 0. Exception recall and stale-answer rate
improve measurably over the Phase 1 baseline. Beat Phase 1 on the labeled
failure set by a clear margin — if not, fix the extractor, not the policy.

**Fixed after the first live run (2026-08-21):** L3 wrote a model edge and a
pattern edge describing the SAME (src, dst, type) under different edge_ids
(`model:defines:...` vs `mention:...`). Both landed, and a duplicate silently
inflates the degree penalty in `dge.traversal.policy.frontier_score` — the term
that exists to tell hubs apart from genuinely well-connected nodes (measured:
degree 6 vs 5 unique relations, a 20% inflation on that node). This is the same
hazard `_dedupe_edges` guards at ingest; L3 bypassed it because model and
reconciled edges arrive as separate lists. Fixed in `dge.pipeline.extract_bundle`;
the reconciled pattern edge wins, because after reconciliation it carries
VERIFIED provenance (pattern-detected AND model-confirmed), which is strictly
stronger evidence than an unconfirmed model proposal for the identical relation.
Pinned by `test_model_edge_does_not_duplicate_an_existing_pattern_relation` and
verified falsifiable — reverting the fix fails the test.

**EXIT NOT MET — now measured rather than assumed (2026-08-21).**
Half the criterion is met; the other half is not, and the reason is the
extractor. Reproduce all of it with
`python scripts/phase3_exit_report.py --bundle <b>.sqlite` (no key, no
network — it replays the graph the bundle already holds).

**MET: soundness violation rate = 0.** 170 real queries over a 9-act bundle,
0 unsound. Falsified, not merely asserted: the identical check against a
seeds-only (flat-RAG) context is UNSOUND on **128/170 = 75.3%** with 427
violations. Without that second number the zero would only prove the check
never fires.

**NOT MET: exception recall / stale-answer rate do not beat the Phase 1
baseline.** Three arms over the SAME seeds on all 15 labeled failure cases:

    A  seeds only (docs/06 6.3 baseline 2)      10/15 gold reached
    B  seeds + CONTEXT expansion                11/15
    C  seeds + CLOSURE + CONTEXT (full)         11/15

    lost_exception   A 3/5   B 3/5   C 3/5   <- the ones Phase 3 exists for
    wrong_version    A 2/2   B 2/2   C 2/2

**Closure traversal changed no labeled case.** The one case traversal added
arrived on the CONTEXT frontier, not through a closure edge. docs/06 6.3 is
explicit about what that means: fix the EXTRACTOR, not the traversal policy.

**Why, mechanically — traced, not guessed.** On the Child Labour case the
seeds are *right*: ranks 3 and 4 are literally "7. HOURS AND PERIOD OF WORK"
and "8. WEEKLY HOLIDAYS". The carve-out that answers the question — "(3)
Nothing in Secs . 7, 8 and 9 shall apply to any establishment wherein any
process is carried on by the occupier with the aid of his family" — has
**zero inbound edges**, so reverse traversal from those seeds cannot reach
it. Its only outgoing `exception_of` edges point at Sec. 9's sub-sections
(1) and (2), which are simply the units that happened to share the L3 window.
Two extractor causes, both real:

  - **L3's one-section-per-call window makes a cross-section exception
    unrepresentable.** The model can only propose edges between units it was
    shown, and `validate_candidate` correctly rejects anything else. A
    carve-out naming ss. 7, 8 and 9 from inside s. 9 can never be linked to
    ss. 7 and 8.
  - **`_resolve_target`'s `referenced` hint takes one match from `_REF_RE`,
    which does not handle the plural/list form.** "Secs . 7, 8 and 9" yields
    no target at all.

Corpus-wide (60 acts), the split matters:

    SELF-REFERENTIAL (proviso, "nothing in this section")   463/521 linked (89%)
    CROSS-REFERENCE  ("nothing in section 28")                5/8  linked (62%)

The deterministic layer handles the common proviso well. The cross-reference
form is the leaky one, and it is the form two of the five labeled
`lost_exception` cases depend on — including `Mines_Act,_1952` s. 37
("Nothing in section 28, section 30, section 31, section 34 ... shall apply
to persons ... employed in a supervising capacity"), which is verbatim one
of the taxonomy's own cases.

**Per-edge-type precision, hand-checked, never aggregated (docs/06 6.3).**
Model (L3) edges exist for one document only — see the quota note below.

    MODEL edges (gemini-2.5-flash / gemini-3.6-flash, Child Labour Act)
      exception_of   5 distinct relations, both models found all 5      5/5
      defines        3 distinct relations; 3.6-flash 2/2 correct,
                     2.5-flash 1/3 — it emitted DEFINITION -> USE twice,
                     and DEFINES traverses FORWARD, so a reversed edge
                     means seeding the use never reaches the definition    3/5
      supersedes     0 proposed in 37 successful calls — UNMEASURABLE

    DETERMINISTIC edges (the bulk of the graph)
      exception_of / structural   20/20   proviso -> the provision it qualifies
      supersedes   / pattern        1/1   "notwithstanding ... section 6" -> s.6
      amends       / pattern        3/3
      defines      / pattern       17/20  2 wrong: one proper-noun false
                                          positive ("Child Labour Technical
                                          Advisory Committee" -> def. of
                                          "child"), one garbage source node
      defines      / structural    11/15  systematic: an Explanation is
                                          attached to its PRECEDING SIBLING,
                                          but it qualifies the whole section,
                                          so the provisions that actually use
                                          the term do not reach it
      exception_of / pattern         n=4  1 clearly wrong: "(6) The provisions
                                          of sub-sections (1), (2) and (4)
                                          shall not apply" resolved to (5)

`exception_of` is in good shape where the target is local, and the sample
target of 20 was met for the structural population. It was NOT met for any
model population — see below.

**`discarded` was 0, and the reason is not reassuring.** Across 37 successful
calls on real statutory text: 15 candidates, **0 discarded**, 0 unresolvable
labels. But 74–78% of calls returned `{"edges": []}` and the model proposed
~0.4 edges per call. So the checks are not firing because there is almost
nothing to reject — under-extraction, not a permissive prompt and not
echoing. With n=15 candidates this does not license any claim that the
evidence-span validator is well calibrated on real text; it is simply
untested at volume.

**What is still blocking, in order:**

1. **Free-tier quota, and it is much harder than "generous".** The key's
   limit is `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
   **quotaValue 20 — twenty requests per day, per model.** Verified on both
   `gemini-3.6-flash` and `gemini-2.5-flash`. A 49-call run over five small
   acts gets ~19 calls through and 429s the remaining 30. Pricing first was
   right and did not help: the wall is requests/day, not tokens. Getting
   ≥20 model edges per type — what docs/06 6.3 asks for — needs a paid key
   or a second provider (Groq was the original recommendation and remains
   untried; no `GROQ_API_KEY` is set).
2. **L3 has model edges for 1 of 9 documents.** Everything above therefore
   measures a mostly-deterministic graph. The negative result stands for
   what was measurable, but "the graph does not beat the baseline" is not
   yet a verdict on a fully-extracted graph.
3. **Seeding was lexical, not hybrid + rerank.** `BAAI/bge-large-en-v1.5` is
   NOT cached on this machine (README claimed otherwise; the 4.8G in
   `~/.cache/huggingface` is `microsoft/phi-4`), and two download attempts
   died on the documented network stall. This makes the baseline WEAKER than
   docs/06 6.3 baseline 2, which can only flatter traversal — so the
   negative conclusion is robust to it, though the absolute recall numbers
   will move.
4. **The labeled failure set is 15/50** (Phase 0, still open). All 15 are now
   measured; the margin question deserves more than 15 cases.
5. **The eval harness still does not write `eval_traces`.** The three-way
   split is computed in `scripts/phase3_exit_report.py` and printed, not
   persisted.

**Three bugs the first real corpus run exposed, all fixed and all pinned by
a test that fails when the fix is reverted:**

- **A failed call marked its section "examined".** `Foreign_Marriage_Act,_1969`
  completed **0** successful calls and still had **28** MEDIUM pattern edges
  halved to 0.3, below the 0.5 traversal floor — 110 across the run. A
  transient 429 was silently rewriting the graph, which is the "cost gate's
  mood" failure `dge/l3/run.py`'s own docstring rules out and a breach of
  invariant 8: the graph must be a function of the corpus, not of the
  network. Nodes are now marked examined only after the call returns.
  (`test_medium_marker_in_a_section_whose_call_FAILED_is_left_alone`)
- **Reconciliation degraded edges the model could not possibly have
  proposed.** It checked only that `edge.src` was examined, never that both
  endpoints shared one window — but L3 shows one section per call. On the
  Child Labour Act **17 of 21** degraded edges were cross-section `defines`
  links from a provision to the Sec. 2 definition of a term it uses: the
  most valuable edges in the graph, disabled by a denial inferred from a
  question never asked. Now requires both endpoints in one examined window;
  unconfirmed on that document drops 21 -> 4.
  (`test_a_pattern_edge_across_two_sections_is_not_degraded_by_a_model_that_never_saw_both`)
- **The structured-output latch tripped on a 429.** One rate limit flipped
  `_structured_output` off for the whole pass; a later section then answered
  with `rel_type` instead of `type` and was lost to a validation error. A
  429 is evidence about load, never about schema support. Transient errors
  (429, 5xx, timeouts, connection failures) now propagate as a recorded
  section failure and leave the latch alone; a 400-class rejection still
  falls back to JSON mode.
  (`test_a_rate_limit_does_not_permanently_downgrade_the_run_to_json_mode`)

Also environmental, and it cost a whole run: **`tenacity` was not installed**,
so litellm's `num_retries=2` raised `tenacity import failed` instead of
retrying. Now installed; worth adding to the `llm` extra.

---

## Phase 4 — Tool surface and bundle

- [ ] MCP server: `search`, `get_node`, `get_section`, `neighbors`, `expand`,
      `goto_definition`, `find_references`, `glossary`, `timeline`, `lint`.
- [x] Bundle writer/reader: single-file SQLite with original bytes, substrate,
      nodes, terms, edges, vectors, manifest. Debug variant as JSONL directory.
      READER DONE: `BundleGraph` / `open_bundle` in `src/dge/bundle.py`
      implements the traversal `Graph` Protocol against SQLite, backing
      `incoming()` with `idx_edges_dst`. `dge query` is wired in `cli.py`.
      Vectors are still absent (no L2 yet) and the JSONL debug variant is
      not built.
- [ ] Round-trip test: bundle → fresh machine → identical traversal results.

**Exit:** an agent with no prior context answers a multi-hop question correctly
using only the tools, with no bespoke prompt engineering.

---

## Phase 5 — Cross-document

- [ ] Candidate generation via retrieval over canonical entities and identifiers.
- [ ] Pairwise verification → typed edge or `null`. Never similarity-to-edge.
- [ ] Entity resolution on strong keys only; weak matches → `possibly_same_as`,
      low confidence, never merged.
- [ ] Version/supersession chain resolution; `status` recomputed as an
      order-independent function of the corpus.
- [ ] Cross-document delta job triggered by ingest, run asynchronously.
- [ ] Cross-document traversal cost multiplier; only strong edge types crossable.

**Exit:** out-of-order ingestion of an amendment before its master agreement
produces the same final graph as in-order ingestion.

---

## Phase 6 — Cost and scale

- [ ] Selective L3 gating on assertive propositions + exception markers.
- [ ] Section-hash caching for repeated boilerplate.
- [ ] Cascade: small model proposes, large model adjudicates low confidence.
- [ ] Per-page cost dashboard from the ingest ledger.

**Exit:** measured per-page ingest cost inside the pricing model.

---

## Standing instrumentation (build in Phase 1, never remove)

For every eval query log: seed set, expanded set, edge types fired, assembled
context, and where the gold span landed — **seed / expansion / never reached**.

That three-way split is the only way to tell whether to fix the embedder, the
extractor, or the budgets. Without it you are tuning blind, and graph systems
are very good at looking like they work while retrieving plausible-but-wrong
neighborhoods.
