# 03 — Graph Model

## 3.1 Why relations cannot live in the embedding

The tempting shortcut is to push relational information into each sentence
vector so that similarity alone recovers structure. It does not work, for
reasons that are structural rather than empirical:

- **Directionality.** "B supersedes A" ≠ "A supersedes B", but `sim(a,b) =
  sim(b,a)` always. Cosine similarity cannot represent direction.
- **Typing.** "contradicts", "is an exception to", "defines a term in" all
  collapse to one scalar. The label — the useful part — is destroyed.
- **Queryability.** Even if the relation were somehow encoded, no operation
  extracts it. You can ask "what is near this?" You cannot ask "what qualifies
  this?"
- **Capacity.** If every sentence absorbs its section's shared context, all
  sentences in a section drift toward each other and become *less*
  distinguishable. Discriminative budget spent on context identical across
  siblings. This is a measured failure mode when late chunking is pushed too
  far: section-level recall up, sentence-level precision down.

The field already ran this experiment. Knowledge graph embeddings (TransE,
RotatE, ComplEx) converged on `h + r ≈ t` — the relation needs **its own**
representation and cannot be folded into the entity. A decade of papers settled
it.

**Therefore:** vector = what this node is *about*, computed with document
context so referents resolve. Edge = an explicit typed directed record stored
outside the vector, traversed by a tool after seeding.

Not a compromise — the correct factorization, and it keeps each half
debuggable. When retrieval goes wrong you can tell whether the seed was bad or
the traversal was.

## 3.2 What actually gets embedded

Standard RAG compares the query vector against **one pooled vector per chunk**.
Two consequences worth designing around:

- **Asymmetry.** A short question and a 400-token passage are different objects
  forced into one space; a question and its answer often share little
  vocabulary. Use retrievers trained for asymmetric query–passage search.
- **Dilution.** A chunk about five things is the average of five things and sits
  near none of them. This is the entire chunk-size tension.

Mitigations, all of which change *what is indexed* rather than how matching
works — and note how many are just "index a normalized self-contained
restatement, return the original span," i.e. Layer 1:

| Technique | Effect |
|---|---|
| Proposition / small-to-big | Embed small units for precision, return enclosing section for context |
| Contextual retrieval | Prefix each unit with LLM-written situating context before embedding |
| Late chunking | Encode the whole document, then pool per unit — pronouns and implicit subjects carry information |
| doc2query / hypothetical questions | Index questions the node answers; attacks asymmetry directly |
| HyDE | Embed a hallucinated answer instead of the query; answer-to-answer matching |
| Multi-vector (ColBERT) | No pooling, no dilution; larger index |
| Hybrid + RRF | Sparse catches identifiers, part numbers, rare terms that embeddings smear |
| Cross-encoder rerank | Real query–passage interaction; usually the single biggest quality jump |

**Baseline stack:** late-chunked embeddings over Layer 1 normalized text +
BM25 + RRF + cross-encoder rerank. The graph must beat this to justify itself.

## 3.3 Node types

Three, and conflating the first two is the usual source of confusion:

**Proposition nodes** — assertions. Carry: raw span + byte offsets, normalized
self-contained restatement, inherited context (section path, temporal scope,
subject, governing conditions), assertive flag, version metadata.

**Term nodes** — identifiers, not assertions. Keyed by `(surface_form, scope)`.
Carry: canonical form, variants, one-sentence gloss, pointer to the defining
span, scope.

**Structural nodes** — sections, tables, figures, footnotes. Cheap, deterministic,
and carry the hierarchy that most inherited context comes from.

A relation that is itself searchable should be **promoted to a node**: "the
30-day return window does not apply to clearance items" becomes a first-class
indexed proposition linked to both parents, so it is reachable by similarity
*and* by traversal.

## 3.4 Edge ontology

Split by consequence of omission — this distinction drives everything in 04:

### Closure edges (soundness — omitting one makes the answer *wrong*)

| Edge | Direction | Notes |
|---|---|---|
| `exception_of` | exception → rule | Must be traversed **backward** from the rule |
| `supersedes` / `amends` | new → old | Usually derivable from document metadata |
| `conditioned_on` | claim → condition | Often recoverable from section structure |
| `defines` | definition → term | Scope-resolved, see 3.5 |
| `retracts` / `deprecates` | domain-specific variants of supersession |

### Context edges (helpfulness — omitting one makes the answer *thinner*)

`elaborates`, `exemplifies`, `supports`, `contradicts`, `cites`,
`similar_to`, `part_of`, `elaborated_by`.

### Term-to-term edges

`abbreviation_of`, `variant_of`, `synonym_of`, `is_a`, `part_of`. Mostly
deterministic; the rest need one cheap LLM pass over the **lexicon only**
(hundreds of terms, not millions of sentences).

### Extraction order — cheapest first

1. **Free and deterministic:** section hierarchy, adjacency, list membership,
   table↔caption↔header, explicit cross-references ("see §4"), footnotes,
   citations, amendment headers naming their parent, shared identifiers,
   email thread structure. **In most corpora this is the majority of the real
   signal.**
2. **Pattern-derived:** definition sites, abbreviation introductions,
   "notwithstanding" / "except that" / "subject to" constructions — these are
   lexically marked in legal text and give high-precision `exception_of`
   candidates for free.
3. **Model-extracted:** one structured-output pass per section, emitting typed
   edges with confidence. Prompt includes section path + running document
   summary.
4. **Model-verified cross-document:** candidates from shared canonical entities
   or embedding similarity, verified pairwise, returning a type or `null`.

**Never let unverified similarity become a typed edge.** That is how a graph
ends up asserting "A contradicts B" because both mention the same company.

**On using attention weights as edges — don't.** Attention sinks dominate (mass
parks on BOS and punctuation); it is causal and asymmetric so the matrix is
triangular; aggregating ~1000 head-layer matrices is an unsolved heuristic;
FlashAttention never materializes the matrix so reading it costs O(n²) memory;
and a scalar is not navigable — `S12 → S47: 0.31` tells a tool nothing, whereas
`S47 exception_of S12` tells it everything. Generate typed structure with one
LLM pass instead of reading internal weights. Same cost order, labeled and
debuggable output.

## 3.5 The term symbol table — treat the corpus as a codebase

This is the mechanism that connects words to their explaining paragraphs, words
to words, and explanation-to-explanation, with one abstraction.

**Terms are identifiers, definitions are bindings, sections are lexical scopes.**

Definition sites are typographically marked and can be found without a model:
`"Confidential Information" means…`, `(hereinafter the "Agreement")`,
parenthetical capitalized quotes, glossary sections, bold-on-first-use,
`X is defined as`, `X refers to`. This yields a high-precision **lexicon**:
canonical form, variants, pointer to defining span.

Linking every *mention* is then one deterministic pass — build an
Aho–Corasick automaton over the lexicon and stream the corpus through it.
Millions of tokens per second, every mention edge, zero inference cost.

Borrowing compiler semantics gives, for free:

- **Scoping.** `Territory` in Schedule B may differ from `Territory` in the
  master agreement. Resolution walks outward: section → document → corpus →
  domain glossary.
- **Shadowing.** A local redefinition overrides the global one. Common, and a
  real source of RAG errors that nobody handles.
- **Imports.** "as defined in Schedule B" is an import statement; make it an
  explicit edge.
- **Linting.** Undefined symbols (terms used but never defined), unused
  definitions, circular definitions, shadowing conflicts. *"This contract uses
  27 defined terms it never defines and defines 4 it never uses"* is a shipping
  feature that sells before retrieval does.

**Transitive definition chains** (A's definition uses B, whose definition uses
C) are bounded by two rules:

1. **Gloss vs. span.** Hop 1 gets the full defining passage; hops 2+ get only
   the one-sentence gloss. A 3-deep chain costs three sentences, not three
   paragraphs.
2. **Depth 2 + token budget + visited set.** Depth 2 covers almost everything.
   Circular definitions genuinely occur in legal text — that is a lint finding,
   not a crash.

**Splice glosses inline at assembly**, not as appended nodes:
`Territory [= the countries listed in Schedule B]` reads far better to a model
than a wall of appended definitions, and costs fewer tokens.

**When no explicit definition exists** (most ordinary prose), degrade gracefully
and tag provenance: first-mention heuristic → one LLM pass for implicit
definitions → embedding similarity between term-in-context and candidate
explaining paragraphs, as a last resort. A pattern-derived definition outranks a
similarity-guessed one, always.

**Prior art to borrow from:** entity linking / wikification (resolution), SKOS
and TEI `<term>`/`<glossterm>` (representation), LSP (interface). The LSP
analogy is not just cute — `goto_definition` and `find_references` are exactly
the operations an agent needs, and reusing the vocabulary means less prompt
engineering to get models to call the tools correctly.

## 3.6 Cross-document identity

Within a document, "the Agreement" is unambiguous. Across a corpus you have
`ACME Corp`, `ACME Corporation`, `Acme Inc.`, and `the Supplier`.

**Merge only on strong keys:** explicit identifiers (contract numbers, DOIs,
statute citations, SKUs, tickers), exact normalized strings, explicit
cross-references. Everything else becomes a low-confidence `possibly_same_as`
edge that traversal surfaces rather than silently collapsing.

**Wrong merges are much worse than missed merges** — a wrong merge invents facts
that exist in no document.

Cross-document edges are also quadratic and cannot be extracted the way
within-document edges are: no pass sees all pairs. Use candidate generation →
verification, and pay for verification only where both nodes are assertive
claims about the same canonical entity.

**Traversal cost differs too:** crossing a document boundary should cost 2–3× a
within-document hop, and only strong edge types (`amends`, `supersedes`,
`defined_in`, `cites`) should be crossable at all. Otherwise one seed pulls in
the neighborhood of the entire corpus.

## 3.6b Enrichment writes edges, never copied content

A tempting shortcut: when a new document arrives, retrieve related documents and
*copy* their relevant context into the new document's enrichment — then write the
new document's context back into the old ones it references. The bidirectional
instinct is right; the copying is not. Four things break:

- **Staleness cascades.** If C's enrichment contains copy of B's, which contains
  a copy of A's, editing A silently invalidates a copy two hops away through an
  unrecorded dependency. An edge is a pointer — resolve it at query time and you
  always get current content. A copy is a snapshot with no expiry.
- **Ingest order becomes significant.** Documents arrive out of order constantly
  (the 2019 master agreement is scanned six months after the 2024 amendment).
  Incremental patching makes the corpus non-reproducible. Cross-document state
  must be an **order-independent function of the current corpus**, recomputed,
  not accumulated.
- **Access control.** If Y's content is baked into X's enrichment, anyone with
  permission on X reads Y — counterparty A's terms inside counterparty B's
  document. Edges degrade safely (filter unauthorised neighbours at traversal;
  the user sees a gap). Copies cannot be un-leaked, and a portable bundle then
  carries content from documents it was never meant to contain.
- **Cost.** Every ingest triggers retrieval plus verification, and every
  back-write touches N old documents — quadratic without blocking keys.

**What to write instead**, when a new document lands:

1. Retrieve candidates (cheap; canonical entities and identifiers).
2. Verify each pair into a typed edge or `null`.
3. Write the edge **once**. Because `edges` is indexed on `dst` as well as `src`,
   it is already bidirectional — there is no separate content back-write. This is
   what the reverse index was for.
4. Recompute affected **version chains** and update `status` on the old nodes.
   This is the one legitimate mutation of an existing document, and it touches a
   status flag, not text.
5. Invalidate caches touching the affected nodes.

Step 4 is where the instinct genuinely pays: the old document does not need the
new one's *content*, it needs its **authority** updated — a small, recomputable,
order-independent fact.

Materialisation is legitimate only as a **cache** keyed by corpus version and
rebuildable from edges. The test: if deleting it loses information, it was not a
cache, and the derived layer has silently become authoritative.

## 3.7 Versioning and authority

Multiple related documents almost always means multiple versions of overlapping
truth: policy v3 and v4, master agreement plus two amendments, paper plus
erratum. A graph that treats these as peers retrieves both and produces a
confident contradiction.

Every node carries, at ingest: source document, effective date (or version),
document status (current / superseded / draft).

Retrieval applies a **temporal and authority filter before assembly**: resolve
the chain, retrieve the head, carry superseded versions only for historical
queries. When a superseded node is included, **label it** rather than dropping
it silently — a model reasons better with "this was replaced in 2024 by X" than
with a gap.

## 3.8 The corpus-level layer is a separate mechanism

Local traversal answers "find the right specific thing." It cannot answer "what
are the recurring themes across these 400 reports," because that answer is in no
neighborhood. That needs precomputed aggregates: entity-level summary nodes, or
clustering plus per-cluster summaries (GraphRAG community summaries, RAPTOR
summary trees).

Route queries between the two. Do not try to make traversal do aggregation.
