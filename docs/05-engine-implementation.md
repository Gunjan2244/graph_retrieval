# 05 — Engine Implementation

## 5.1 Pipeline stages

Each stage is idempotent, independently re-runnable, and writes its own version
key. Treat them as a DAG of content-addressed jobs, not a script.

```
ingest        → classify document, hash bytes, register
parse         → route by class → substrate + parse confidence         (L0)
validate      → structural checks; low confidence → review queue
normalize     → coref, restatement, inherited context, assertive flag (L1)
lexicon       → definition sites → term nodes
link          → Aho–Corasick mention pass → mention edges
edges:det     → structural + pattern edges                            (L3a)
edges:model   → LLM structured-output pass per section                (L3b)
embed         → late-chunked dense + sparse index                     (L2)
── corpus batch (separate cadence) ──
resolve       → cross-document entity resolution                      (L4)
chains        → version / supersession resolution                     (L4)
aggregate     → cluster summaries                                     (L4)
```

**Classification and routing at parse:** born-digital single-column, multi-column,
scanned, table-heavy, forms. Each routes to a different parser configuration.
There is no universal setting, and pretending otherwise is how corrupt
substrates get shipped.

## 5.2 Storage schema (SQLite / Postgres compatible)

```sql
CREATE TABLE documents (
  doc_id TEXT PRIMARY KEY,
  substrate_hash TEXT NOT NULL,
  source_uri TEXT,
  doc_class TEXT,              -- contract | amendment | policy | ...
  effective_date TEXT,
  status TEXT,                 -- current | superseded | draft
  parse_confidence REAL,
  ingested_at TEXT
);

CREATE TABLE nodes (
  node_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  kind TEXT NOT NULL,          -- proposition | term | structural
  seq INTEGER NOT NULL,        -- document order; drives assembly
  byte_start INTEGER, byte_end INTEGER,
  raw TEXT NOT NULL,           -- never modified
  normalized TEXT,             -- L1 self-contained restatement
  section_path TEXT,           -- '§4 > §4.2 > Exclusions'
  inherited_ctx JSON,          -- temporal scope, subject, conditions
  is_assertive INTEGER,
  layer1_version TEXT, model_id TEXT
);

CREATE TABLE terms (
  term_id TEXT PRIMARY KEY,
  surface_form TEXT NOT NULL,
  canonical TEXT,
  scope_node_id TEXT,          -- lexical scope of the binding
  definition_node_id TEXT,
  gloss TEXT,                  -- one sentence, used at hops 2+
  variants JSON,
  provenance TEXT,             -- pattern | model | similarity
  confidence REAL
);

CREATE TABLE edges (
  edge_id TEXT PRIMARY KEY,
  src TEXT NOT NULL, dst TEXT NOT NULL,
  type TEXT NOT NULL,
  class TEXT NOT NULL,         -- 'closure' | 'context'
  cross_doc INTEGER DEFAULT 0,
  confidence REAL,
  provenance TEXT,             -- structural | pattern | model | verified
  model_id TEXT, prompt_hash TEXT,
  evidence_span TEXT
);

CREATE INDEX idx_edges_src ON edges(src, type);
CREATE INDEX idx_edges_dst ON edges(dst, type);   -- REVERSE index: load-bearing
CREATE INDEX idx_nodes_doc_seq ON nodes(doc_id, seq);
```

The **reverse index on `edges(dst, type)`** is what makes 04.3 and the soundness
check cheap. It is not an optimization; the guarantee depends on it.

## 5.3 Model selection per layer

| Stage | Model class | Rationale |
|---|---|---|
| Parse | None (Docling / Marker / Azure DI) | Deterministic + specialized OCR |
| Normalize (L1) | Small instruct model, batched | High volume, mechanical task |
| Lexicon / link | None (regex + Aho–Corasick) | Deterministic, effectively free |
| Term relations | Mid model, lexicon only | Hundreds of items, not millions |
| Edges (L3) | Large model, structured output | Judgment-heavy; the quality bottleneck |
| Cross-doc verify | Mid model, pairwise | Only on thresholded candidates |
| Embed (L2) | Off-the-shelf asymmetric retriever w/ long context | Late chunking needs long context |
| Rerank | Cross-encoder | Biggest single quality jump per unit cost |

**Prompt discipline for L3:** structured output with a closed edge-type
enumeration, one section per call, section path + running document summary in
context, and an explicit `null` option. Every returned edge must include an
evidence span. Reject edges whose evidence span does not appear in the input —
that single check kills most hallucinated relations.

## 5.4 Unit economics

**The cost center is ingest, and it is per-page and recurring on every document
update.** Roughly, per page:

| Stage | Relative cost |
|---|---|
| Parse | low, fixed |
| L1 normalize | low–moderate (small model, but every sentence) |
| Lexicon + link | negligible |
| L3 edges | **dominant** — large model over every section |
| Embed | low |

Levers, in order of effectiveness:

1. **Deterministic edges first.** In most corpora structural + pattern edges are
   the majority of the real signal. Every edge obtained without a model call is
   pure margin.
2. **Selective L3.** Run the large model only on sections containing assertive
   propositions and lexical exception markers ("notwithstanding", "except",
   "subject to", "provided that"). Skip boilerplate entirely.
3. **Cascade.** Small model proposes edges; large model adjudicates only
   low-confidence cases.
4. **Cache by content hash.** Amendments and template contracts repeat
   enormously across a corpus. Hash-level dedup at the section granularity is a
   large real-world saving in contract corpora specifically.

**Price on ingest, not seats.** A seat-priced deal with a customer who ingests
millions of pages is a loss-making contract by construction.

### The change-rate question

The single fact that most changes the architecture:

- **Static corpora** (executed contracts, filed regulations, published papers):
  L3 cost amortizes over the document's life. Be lavish. Precompute everything.
- **High-churn corpora** (technical docs, wikis, drafts in negotiation): L3 cost
  recurs forever. The design must shift toward deterministic edges plus **lazy,
  on-demand extraction at query time**, with caching by section hash.

Decide this per target domain before committing to a pricing model.

## 5.5 Throughput and operations

- Stages as content-addressed jobs in a queue; workers idempotent; retries safe.
- Batch L1 aggressively — it is embarrassingly parallel.
- L3 concurrency is rate-limit bound, not CPU bound; design for backpressure.
- Corpus-level jobs (L4) run on a delta trigger, never inline with ingest.
- Keep a per-document ingest ledger with per-stage timing and cost. You cannot
  manage the unit economics you do not measure per document.

## 5.5b Cross-document ingest is a delta job

Cross-document processing is **triggered by** ingest but must not run **inside**
it, or per-document ingest latency becomes O(corpus).

```
document lands
  → per-document path (L0-L3) completes and commits
  → enqueue corpus delta job for the affected neighbourhood
  → delta job (async): candidate retrieval, pair verification, edge writes,
                       version-chain recomputation, cache invalidation
```

The delta job must be **idempotent and order-independent**: ingesting an
amendment before its principal Act must produce the same final graph as the
reverse order. Test this explicitly — it is the Phase 5 exit criterion, and it is
the property that incremental content-patching quietly destroys.

## 5.6 Failure handling

| Failure | Response |
|---|---|
| Parse confidence below threshold | Do not proceed. Review queue. Loud. |
| Table without resolvable headers | Flag section; exclude from assertive indexing |
| L1 restatement diverges from raw span | Fall back to raw + inherited context |
| L3 edge with unverifiable evidence span | Discard |
| Low-confidence edge | Keep, filter at traversal time by threshold |
| Entity match on weak key | `possibly_same_as`, low confidence, never merged |
| Circular definition | Lint finding, visited set prevents loop |
| Missing effective date | Status `unknown`; excluded from authority filtering, flagged |

The recurring principle: **degrade to a labeled, lower-confidence state rather
than to a confident wrong one.** Everything carries provenance and confidence so
that filtering is a runtime decision, not an irreversible ingest-time deletion.

## 5.7 Deployment

Assume serious buyers will not let documents leave their perimeter. Design for a
VPC / on-prem deployment from the start:

- No hard dependency on a hosted vector DB — pgvector or LanceDB embedded.
- Model calls via a pluggable provider interface, including self-hosted
  endpoints for L1 and cross-doc verification.
- The bundle is a file; it works air-gapped by construction.
- License and telemetry design that survives an environment with no egress.

Retrofitting this later is far more expensive than accommodating it now.
