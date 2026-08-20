# 02 — Architecture

## 2.1 The layer model

The layers are **not stages of one pipeline**. They are independently versioned
projections over one immutable substrate. The reason is invalidation cadence:
if they are fused, swapping the embedding model forces re-running every
expensive LLM extraction pass.

```
Layer 4  corpus-level    entity resolution, version chains, cluster summaries
                         (function of the whole set, not of one document)
              ▲
Layer 3  relations       typed edges, definitions, canonicalization
                         LARGE MODEL · expensive · sparse · run least
              ▲
Layer 2  retrieval       dense vectors + sparse index
                         NO LLM · disposable by design · rebuild in hours
              ▲
Layer 1  normalization   coref, self-contained restatement, inherited context,
                         unit/date normalization, assertive flag
                         SMALL MODEL · high volume · batched
              ▲
Layer 0  substrate       section tree, paragraphs, sentences, tables w/ headers,
                         lists, footnotes, captions, byte offsets
                         DETERMINISTIC · no model · content-hashed
              ▲
         original bytes  immutable, authoritative, never modified
```

### Layer 0 — substrate

The only thing that *is* the document. Everything above is a derived artifact
that can be discarded and rebuilt.

Produces: canonical structure with byte offsets back into the source, plus a
content hash.

This layer is unglamorous and is where most pipelines actually fail. A table
parsed as prose poisons every layer above it and no downstream cleverness
recovers. Validate structurally: does every table row resolve to headers? Do
footnote markers resolve to footnotes? Does reading order look monotonic? Emit a
parse-confidence score; route low scores to review rather than onward.

### Layer 1 — normalization (readability)

Per sentence / proposition:

- coreference resolved (`it` → `the Q3 revenue figure`)
- **self-contained restatement** — never replaces the original, points at it
- **inherited context** — section path, temporal scope, subject, governing
  conditions
- normalized units, currencies, dates
- **assertive flag** — headings, boilerplate, and navigation are not claims and
  must not be indexed as such

Span-anchored throughout. Small-model or encoder-model work, batched, cheap
enough to run over everything.

### Layer 2 — retrieval index

Pure function of Layer 1 output plus a model ID. Dense vectors (computed with
document context — see late chunking, 03.2) plus BM25/sparse. **Disposable:**
new embedding model means drop and rebuild, touching nothing else.

### Layer 3 — relations and context

Typed edges, definitions, entity canonicalization, cross-references. Windowed by
section, with the section path and a running document summary in the prompt so
the model has enough context to type edges honestly.

This is the budget line. Run it least, and most selectively.

### Layer 4 — corpus level

Cross-document entity resolution, supersession chains, cluster/community
summaries. A function of the whole set, so it runs as a separate batch job on a
separate cadence triggered by corpus deltas. **Keep it out of the per-document
ingest path** or ingest latency becomes O(corpus).

## 2.2 Invalidation and versioning

Every node carries a version key per layer:

```
(substrate_hash, layer, layer_version, model_id, prompt_hash)
```

This makes invalidation surgical:

- edit one section → only that section's L1 and L3 outputs go stale
- swap embedding model → only L2 rebuilds
- improve an extraction prompt → only L3 rebuilds, and you can A/B against the
  old edges because provenance is recorded

Without this you have a batch job you are afraid to re-run, which in practice
means enrichment quality freezes at whatever shipped first.

**Every enrichment must carry provenance and confidence.** Provenance = which
model, which prompt version, which source span. Confidence = a score used to
*filter at traversal time* rather than to delete at write time, so thresholds
are tunable post-hoc without re-extraction.

## 2.3 The bundle format

Original document unchanged; enrichment in a sidecar. Portability comes from the
bundle being one self-contained file, not from hiding inside the PDF.

```
doc.bundle          (single-file SQLite, or a zip in the debug variant)
├── original.pdf    untouched bytes, content-hashed
├── substrate       structure + byte offsets                  (L0)
├── nodes           normalized propositions + inherited ctx    (L1)
├── terms           symbol table: surface forms, scopes, glosses
├── edges           typed, directed, provenance, confidence    (L3)
├── vectors         disposable                                 (L2)
└── manifest        per-layer version, model_id, prompt_hash,
                    parse confidence, ingest timestamps
```

**Why SQLite:** one artifact, queryable without a server, survives being emailed
around, transactional, and every language has a driver. A directory-of-JSONL
variant is useful for debugging and diffing; ship both, treat SQLite as
canonical.

**Why not inside the PDF:** welding enrichment into the file costs exactly what
the layered design bought — independent versioning, surgical invalidation, and
the original as immutable ground truth. PDF attachment and metadata facilities
will mangle it the moment any other tool touches the file. And an enriched PDF
still cannot be traversed without our reader, so it buys no interoperability in
exchange.

## 2.4 Why the format is not the product

New file formats are essentially never adopted on their own merits. Parquet,
SQLite, and PDF all won because a tool people already wanted emitted them. The
format rode in on the tool's back.

An LLM never reads the format — it calls functions. So the artifact the customer
actually experiences is the **tool surface** (04.6), delivered as an MCP server
plus an SDK. That is also the distribution channel: it drops into existing agent
stacks without asking anyone to change anything.

The bundle sits behind the tools and can change freely as long as the tool
contract is stable. Open the format later, if the tool wins.
