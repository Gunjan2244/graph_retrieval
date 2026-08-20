# 01 — Scope and Boundaries

## 1.1 The problem, stated precisely

Retrieval-augmented generation over documents fails in four distinguishable
ways. They are not equally important and they do not have the same fix.

| Failure | Example | Fixed by |
|---|---|---|
| **Lost referent** | Chunk says "it declined 12%" — "it" is defined two paragraphs earlier | Layer 1 normalization |
| **Lost scope** | Chunk states a rule; the governing condition ("for EU customers after 2023") sits in the section header | Layer 1 inherited context |
| **Lost exception** | Rule retrieved; the clause that carves out an exception is not similar to the query and is never retrieved | Closure traversal |
| **Wrong version** | Answer drawn from a policy superseded two years ago | Version chains + filtering |

The first two are cheap to fix and account for a large share of observed
discrepancies. **They should be fixed first and shipped before any graph
work.** A baseline of "normalized self-contained nodes with inherited context +
hybrid retrieval + reranking" is a strong system on its own and is the bar the
graph must beat.

The last two are what justify the product. They share a property that makes
them immune to better embeddings: **the missing text is not semantically
similar to the query.** An exception to a rule frequently shares no salient
vocabulary with the question that triggers the rule. No amount of ranking
improvement surfaces it, because ranking is over similarity and the relationship
is not a similarity relationship. Larger context windows do not fix it either —
the model cannot apply a caveat it was never shown.

**This is the defensible claim:** the system structurally cannot answer from a
rule while omitting a known exception or superseding version of it.

## 1.2 In scope

- **Ingest** of PDF and plain text, with routed parsing by document class and an
  explicit confidence signal on parse quality.
- **Layer 1 normalization** — coreference resolution, self-contained
  restatement, inherited context, unit/date normalization, assertive vs
  non-assertive classification.
- **Term symbol table** — definition-site detection, mention linking, scope
  resolution, shadowing, glosses.
- **Typed directed edge extraction** — deterministic first, model-verified
  second, with per-edge provenance and confidence.
- **Version and supersession chains**, within and across documents.
- **Hybrid retrieval** (dense + sparse) with reranking, over normalized nodes.
- **Budgeted graph traversal** with a closure/context split and a post-answer
  soundness check.
- **A traversal tool surface** exposed as an MCP server, plus an SDK.
- **A bundle artifact** (single-file SQLite) carrying substrate, nodes, edges,
  vectors, and a manifest — original document bytes untouched.

## 1.3 Out of scope — and why

| Excluded | Reason |
|---|---|
| **Building a PDF parser** | Docling, Marker, Unstructured, LlamaParse, Azure DI are years ahead. Integrate and route; do not compete. Parsing quality is table stakes, not a wedge. |
| **Training embedding models** | Off-the-shelf retrievers plus a cross-encoder reranker are near the frontier and improve for free. |
| **Being a vector database** | Use pgvector / LanceDB / Qdrant. The index is a disposable layer by design. |
| **Emitting an enriched PDF** | Cannot be traversed without our reader anyway (so it buys no interoperability), bloats the file, and is destroyed by any tool that touches the PDF. Sidecar bundle instead. |
| **Answer generation / chat UI** | We supply context, not answers. Selling the retrieval substrate keeps us compatible with every agent stack instead of competing with them. |
| **OCR research** | Route to existing OCR. Flag low-confidence pages for review. |
| **Horizontal domain coverage at launch** | See 1.5. |
| **A general knowledge graph** | We model *document structure and governance*, not world facts. Entity-centric KG construction is a different, larger problem with worse precision. |

## 1.4 Explicit non-goals

- **Not a new open standard, initially.** The format may become one if the tool
  wins. Leading with format adoption is asking prospects to bet on an ecosystem
  that does not exist.
- **Not universal document handling.** "Any PDF" is a claim no one satisfies.
  Classify, route, and *fail loudly* rather than emitting a corrupt substrate
  that silently poisons every layer above.
- **Not fully automatic at high stakes.** Low-confidence parses and
  low-confidence edges go to a review queue. The reviewability *is* the product
  in regulated settings.

## 1.5 The wedge

A domain-agnostic enricher can only extract shallow universal edge types
(`defines`, `cites`, `elaborates`). Those are worth something, not much. The
edges that produce dramatic wins are domain-specific:

- **Contracts** — master agreement + amendments + SOWs + schedules.
  `supersedes`, `exception_of`, `defined_in` are the entire game. Buyers have
  budget, documents are static after execution, and the failure mode is
  legible to a non-technical buyer ("you quoted me a clause that was amended").
- **Regulatory / policy corpora** — same structure, larger scale, slower sales.
- **Clinical guidelines** — high value, highest liability, longest cycle.
- **Technical documentation** — `deprecates`, `replaces`; fast-changing, which
  inverts the cost model (see 05 unit economics).

**Recommendation: contracts.** Static corpus, clear buyer, edge types that map
one-to-one onto the architecture's strength, and a lint feature (undefined
terms, unused definitions, shadowing conflicts) that sells before retrieval
does.

Architect Layers 0–2 as domain-agnostic. Make the Layer 3 edge ontology a
**pluggable config per corpus type**, not code.

## 1.6 Competitive boundary

Occupied adjacent space: Reducto, Chunkr, LlamaParse, Unstructured (parsing);
Contextual.ai, Vectara (managed RAG); Microsoft GraphRAG, LightRAG, RAPTOR
(graph/hierarchical retrieval, open source).

- Nobody owns the category.
- **"Better parsing" is not a wedge** — it is a feature race lost on funding.
- GraphRAG's published weakness is the honest one: high ingest cost, wins mainly
  on global/aggregative queries rather than lookup. Our design differs by making
  the *soundness* guarantee the point, not the summarization.
- The positioning sentence is not "graph-enhanced retrieval." It is: *retrieving
  a rule without its exceptions is what makes document AI unusable in serious
  settings, and this system prevents it by construction.*

## 1.7 Assumptions the design rests on

1. **Closure edges are sparse.** Few sentences have exceptions; fewer have
   exceptions-to-exceptions. If this is false in the target corpus, bounded
   traversal fails and the design needs rework. **Validate this first, on real
   documents, before building anything.**
2. **Definition sites are typographically marked** in the target domain
   (`"X" means`, glossaries, bold-on-first-use). True in legal and technical
   text; weaker in ordinary prose.
3. **Version/supersession metadata is derivable** from document structure and
   metadata, not only from prose.
4. **Documents are relatively static.** Change rate drives the entire cost
   model — see 05.
5. **Buyers will not let documents leave their perimeter.** Plan VPC/on-prem
   from the start; retrofitting is expensive.

## 1.8 Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Long context + better retrievers absorb the simple end of the market | High | Moat is the structural guarantee, not "more relevant text." Emphasize soundness, provenance, and audit. |
| Layer 3 ingest cost makes unit economics unworkable | High | Price on ingest not seats; deterministic edges first; model-verify only high-value candidates. |
| Edge extraction precision too low to trust | High | Confidence + provenance on every edge; filter at traversal time; never let unverified similarity become a typed edge. |
| Entity resolution produces wrong merges | High | Merge only on strong keys. Weak matches become low-confidence `possibly_same_as`, surfaced not collapsed. A wrong merge invents facts present in no document. |
| Layer 0 garbage silently propagates | Medium | Parse-confidence scoring, structural validation, loud failure, review queue. |
| Graph looks like it works while retrieving plausible-but-wrong neighborhoods | Medium | Three-way instrumentation (seed / expansion / never-reached) from day one. See 06. |
| Format never adopted | Low | It doesn't need to be. Product is the tool surface. |
| Scope sprawl into horizontal coverage | Medium | One domain, three edge types, one reference customer before widening. |
