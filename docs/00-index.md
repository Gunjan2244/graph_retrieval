# Document Graph Engine — Design Report

An enrichment engine that ingests documents and emits a layered, LLM-traversable
representation: normalized proposition nodes, a term symbol table, and typed
directed edges — retrieved by vector seeding and collected by graph traversal.

**Status:** design consolidated; `legal` domain pack and the Phase 0 density
script are shipped. Engine not built yet. This set of documents is the
specification to build against and the argument for why the design is shaped
this way.

---

## The one-paragraph thesis

Flat-chunk RAG fails in a specific, expensive way: it retrieves a rule without
its exception, or answers from a superseded version, and the model produces a
confident wrong answer with no signal that anything is missing. This is not a
ranking problem and better embeddings do not fix it — the missing information is
*structurally* related to the retrieved text, not *semantically similar* to the
query. The fix is to separate the two jobs: embeddings find what a passage is
**about**; explicit typed edges record how passages **govern each other**. Seed
with vectors, collect by traversal, and verify before answering that no known
exception or superseding version was omitted.

## Document index

| File | Contents |
|---|---|
| [01-scope-and-boundaries.md](01-scope-and-boundaries.md) | Problem definition, what is and isn't in scope, non-goals, assumptions, competitive boundary, risk register |
| [02-architecture.md](02-architecture.md) | Layer model (0–4), invalidation and versioning, bundle format, why the format is a sidecar |
| [03-graph-model.md](03-graph-model.md) | Node types, edge ontology, the term symbol table, cross-document identity, supersession |
| [04-retrieval-and-traversal.md](04-retrieval-and-traversal.md) | Seeding, closure vs context edges, best-first expansion, saturation, the soundness check, tool/MCP surface |
| [05-engine-implementation.md](05-engine-implementation.md) | Pipeline stages, storage schema, model selection, throughput, unit economics, failure handling |
| [06-evaluation-and-roadmap.md](06-evaluation-and-roadmap.md) | Eval harness, metrics, MVP definition, milestones, pricing and deployment, open questions |
| [07-legal-domain-pack.md](07-legal-domain-pack.md) | Legal edge semantics — provisos, non obstante, deeming, repeal/savings, case-law treatment, `means` vs `includes`; how other domains slot in |
| [08-rust-migration.md](08-rust-migration.md) | Where time actually goes, why Rust is a distribution decision not a speed one, and the strangler path |

## Reading order

If you read only two: **01** (what we are and are not building) and **04**
(the mechanism that constitutes the actual differentiator).

If you are about to write code: **05**, then **02** for the storage contract.

## The three claims everything else depends on

1. **Relations cannot live inside embeddings.** Cosine similarity is symmetric
   and untyped; relations are directional and typed. The factorization into
   *vector for aboutness* + *edge for structure* is forced, not a compromise.
   (Detail: 03.)

2. **The dense connectivity of a document lives in edges that don't affect
   correctness.** Closure edges — exception, supersession, scoped definition —
   are sparse and terminate in 1–3 hops. Context edges are dense but optional.
   This is what makes bounded traversal possible at all. (Detail: 04.)

3. **The file format is not the product.** The product is a traversal interface
   (an MCP server) that happens to be backed by a format. Formats are adopted
   because a tool people wanted emitted them, never on their own merits.
   (Detail: 01, 02.)

## The demo that is the whole pitch

One domain, ~200 documents, three edge types, and a public eval showing the
system answering correctly on questions where flat-chunk RAG is confidently
**wrong** — not merely worse. Everything in these documents is downstream of
whether that demo can be produced. (Detail: 06.)
