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
- [ ] `Parser` protocol + Docling adapter. Emits sections, paragraphs,
      sentences, tables with header association, footnotes, byte offsets.
- [ ] Parse validation + confidence score. Below threshold → `review` status,
      pipeline halts for that document.
- [ ] Document classifier routing to parser configs (born-digital, multi-column,
      scanned, table-heavy).
- [ ] L1 normalizer: coref, self-contained restatement, inherited context
      (section path, temporal scope, subject, conditions), unit/date
      normalization, assertive flag.
- [ ] L2: embedder protocol + adapter; sparse index; hybrid retrieval with RRF;
      cross-encoder rerank.
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
- [ ] Pattern edges from `LEGAL_MARKERS`. STRONG-confidence hits become edges
      directly; MEDIUM become candidates for model verification.
- [ ] Two competing non obstante clauses = a real conflict. FLAG it; never
      silently pick one.
- [ ] Cost gate: `pack.should_run_l3(text)` before any L3 call.
- [ ] `EdgeExtractor` protocol + LLM adapter. Structured output, closed enum,
      one section per call, section path + document summary in context,
      explicit `null` option, mandatory `evidence_span`.
- [ ] Evidence-span validator — discard any edge whose span is not verbatim in
      the input window.
- [ ] Traversal: closure to fixed point on the reverse index, non-optional
      inclusion; context via best-first frontier with the degree penalty.
- [ ] Assembly in document order with inherited-context prefixes and inline
      gloss splicing.
- [ ] **Soundness check**: for every cited node, verify inbound closure edges
      were in context; expand and re-run if not.

**Exit:** soundness violation rate = 0. Exception recall and stale-answer rate
improve measurably over the Phase 1 baseline. Beat Phase 1 on the labeled
failure set by a clear margin — if not, fix the extractor, not the policy.

---

## Phase 4 — Tool surface and bundle

- [ ] MCP server: `search`, `get_node`, `get_section`, `neighbors`, `expand`,
      `goto_definition`, `find_references`, `glossary`, `timeline`, `lint`.
- [ ] Bundle writer/reader: single-file SQLite with original bytes, substrate,
      nodes, terms, edges, vectors, manifest. Debug variant as JSONL directory.
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
