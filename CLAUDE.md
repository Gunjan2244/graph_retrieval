# CLAUDE.md — Document Graph Engine

Read this fully before writing code. It encodes decisions already made after
long design work. Do not relitigate them in code.

## What this is

An ingestion engine that turns documents into a layered, LLM-traversable graph:
normalized proposition nodes, a term symbol table, and typed directed edges.
Retrieval seeds with vectors and collects by graph traversal.

**The product claim it must support:** the system cannot answer from a rule
while omitting a known exception to it, or from a version known to be
superseded.

Full design rationale is in `docs/` (six numbered documents). Read `01` and `04`
before touching retrieval code.

## Invariants — violating these is a bug even if tests pass

1. **The original bytes are immutable and authoritative.** Never modify a source
   document. Every derived artifact points back via byte offsets.
2. **Normalized text never replaces raw text.** `nodes.raw` is written once and
   never updated. `nodes.normalized` is a separate column.
3. **Relations are edges, never embeddings.** Do not attempt to encode
   directionality or relation type in a vector. Do not use attention weights as
   edges.
4. **Relations are edges, never copied content.** Cross-document enrichment
   writes edges and status flags, never text copied from another document.
   Copied context breaks staleness, access control, and reproducibility.
5. **Every enrichment carries provenance and confidence.** `model_id`,
   `prompt_hash`, `evidence_span`, `confidence`. Filter at traversal time; never
   delete at ingest time.
6. **Closure edges are traversed to a fixed point, unbudgeted.** Context edges
   are budgeted. This split is the architecture; do not unify them behind one
   depth parameter.
7. **Closure edges are traversed on the reverse index.** An exception points at
   the rule it modifies, so following outgoing edges never finds it. The
   `idx_edges_dst` index is load-bearing, not an optimization.
8. **Derived layers are recomputable.** If deleting a cache loses information,
   it was not a cache. Cross-document state must be an order-independent
   function of the current corpus, not an accumulation of ingest-time patches.
9. **Degrade to labeled low confidence, never to confident wrong.** Low parse
   confidence blocks the pipeline and goes to a review queue. It does not
   proceed with a corrupt substrate.
10. **An edge whose `evidence_span` does not appear verbatim in the model's
    input window is discarded.** Enforce in code, not in the prompt.

## Anti-goals — do not build these

- A PDF parser. Use Docling behind the `Parser` protocol.
- A vector database. Use pgvector / sqlite-vec.
- A graph database. Postgres recursive CTEs are sufficient; adding Neo4j
  creates a second consistency domain.
- An answer-generation layer or chat UI. We supply context, not answers.
- Anything built on LangChain or LlamaIndex. Use standalone components. Borrowing
  their parser adapter code is fine; depending on their abstractions is not.
- Enriched-PDF output. The bundle is a sidecar.

## Architecture in one screen

```
L0 substrate    deterministic parse → structure + byte offsets + confidence
L1 normalize    small model: coref, restatement, inherited ctx, assertive flag
L2 index        embeddings + sparse — DISPOSABLE, rebuild freely
L3 relations    large model: typed edges — EXPENSIVE, run selectively
L4 corpus       entity resolution, version chains, aggregates — SEPARATE CADENCE
```

Each stage is idempotent, independently re-runnable, and writes a version key
`(substrate_hash, layer, layer_version, model_id, prompt_hash)`.

L4 never runs inline with per-document ingest.

## Stack — settled

| Concern | Choice |
|---|---|
| Language | Python 3.12+ |
| Server store | Postgres + pgvector + tsvector FTS |
| Bundle | SQLite + sqlite-vec, **same DDL** |
| Parsing | Docling (behind `Parser` protocol) |
| Queue | Postgres `jobs` table, `FOR UPDATE SKIP LOCKED` |
| Embeddings | Pluggable: voyage-context-4 (hosted) / BGE-M3 (self-host) |
| Rerank | BGE-reranker-v2 |
| LLM access | LiteLLM router, Pydantic structured output |
| Term matching | `pyahocorasick` |
| Tool surface | Official MCP Python SDK (FastMCP) |
| Eval | Own harness → Parquet → DuckDB |
| Domain | `legal` pack first (`src/dge/domains/legal.py`) |

11. **Domain knowledge lives in packs, never in the engine.** Marker patterns,
    edge ontologies, definition patterns, and cost gates are data in
    `src/dge/domains/`. The traversal engine knows about closure vs context and
    nothing about law. **If adding a domain requires editing `policy.py`, the
    pack boundary has leaked** — fix the boundary, not the symptom.
    See `docs/07-legal-domain-pack.md`.

12. **Multi-tenancy is enforced at traversal, not by filtering results.**
    Unauthorised neighbours are excluded from expansion; the user sees a gap.
    Because enrichment is edges rather than copied text, this degrades safely —
    see invariant 4.

Two deployment targets, one schema. Keep everything expressible in portable SQL.

## Code conventions

- `src/` layout, typed throughout, `mypy --strict` clean.
- Protocols (`typing.Protocol`) for every external dependency: parser, embedder,
  reranker, LLM. No vendor SDK imported outside its adapter module.
- Pure functions for traversal logic; I/O at the edges. Traversal must be
  testable against a fixture graph with no database.
- No silent excepts. Failures write a row with a reason.
- SQL in `.sql` files or clearly delimited constants, not scattered f-strings.

## Definition of done for any stage

1. Idempotent — re-running produces identical output for identical input.
2. Writes its version key and provenance.
3. Has a failure path that records a reason rather than crashing the batch.
4. Instrumented: timing and cost per document written to the ingest ledger.
5. Unit tested against fixtures; traversal changes also run the eval harness.

## Things that will tempt you and are wrong

- "Just add a depth limit to traversal." No — see invariant 6. Depth truncates
  critical chains while admitting hundreds of irrelevant neighbors.
- "Cache the assembled context into the node row." Only as a cache keyed by
  corpus version, never as ground truth.
- "Merge these two entities, they're obviously the same company." Only on strong
  keys. A wrong merge invents facts present in no document.
- "Similarity is high, make it a `contradicts` edge." Never. Unverified
  similarity is never a typed edge.
- "Run L3 over every section." Gate on assertive propositions and lexical
  exception markers. L3 is the dominant cost line.
