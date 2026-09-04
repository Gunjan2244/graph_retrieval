# Document Graph Engine (DGE)

An ingestion engine that turns documents into a layered, LLM-traversable graph:
normalized proposition nodes, a term symbol table, and typed directed edges.
Retrieval seeds with vectors and collects by graph traversal.

> **The claim the whole system exists to support:** it cannot answer from a rule
> while omitting a known exception to it, or from a version known to be
> superseded.

---

## Why

Flat-chunk RAG fails in a specific, expensive way. It retrieves a rule without
its exception — or answers from a superseded version — and the model returns a
confident wrong answer with no signal that anything is missing.

This is not a ranking problem, and better embeddings do not fix it: the missing
text is *structurally* related to the query, not *semantically similar* to it.
DGE splits the two jobs:

- **Embeddings** find what a passage is **about**.
- **Typed directed edges** record how passages **govern each other** — exception,
  supersession, scoped definition.

Retrieval seeds with vectors, then walks the edges to a fixed point and verifies,
before returning context, that no known exception or superseding version was
omitted. The output is **context, not an answer** — answer generation is
deliberately out of scope.

## How it works

```
L0  substrate     deterministic parse → structure + byte offsets + confidence
L1  normalize      small model: coref, restatement, inherited context, assertive flag
L2  index          embeddings + sparse retrieval — disposable, rebuild freely
L3  relations      large model: typed edges — expensive, run selectively
L4  corpus         entity resolution, version chains, aggregates — separate cadence
```

Each stage is idempotent, independently re-runnable, and records a version key
`(substrate_hash, layer, layer_version, model_id, prompt_hash)` plus provenance
and confidence for every derived artifact.

**Traversal** distinguishes two edge classes:

- **Closure edges** (exception, supersession, scoped definition) are sparse,
  terminate in 1–3 hops, and are traversed to a fixed point — unbudgeted — on the
  *reverse* index (an exception points *at* the rule it modifies).
- **Context edges** are dense, optional, and expanded best-first under a token
  budget.

Keeping these separate is what makes bounded, sound traversal possible. See
[`docs/04-retrieval-and-traversal.md`](docs/04-retrieval-and-traversal.md).

## Design principles

- **Original bytes are immutable and authoritative.** Every derived artifact
  points back via byte offsets. Normalized text is a separate column, never a
  replacement.
- **Relations are edges** — never embeddings, never copied content. Cross-document
  enrichment writes edges and status flags, not text copied from another
  document.
- **Every enrichment carries provenance.** `model_id`, `prompt_hash`,
  `evidence_span`, `confidence`. Filter at traversal time; never delete at ingest
  time.
- **Degrade to labeled low confidence, never to confident wrong.** Low parse
  confidence blocks the pipeline and goes to a review queue.
- **Derived layers are recomputable.** If deleting a cache loses information, it
  was not a cache.
- **Domain knowledge lives in packs** (`src/dge/domains/`), never in the engine.
  The traversal engine knows about closure vs context and nothing about law.

The full rationale is in [`docs/`](docs/) — nine numbered documents. Start with
`01-scope-and-boundaries.md` and `04-retrieval-and-traversal.md`.

## Stack

| Concern | Choice |
|---|---|
| Language | Python 3.12+, typed throughout, `mypy --strict` clean |
| Server store | Postgres + pgvector + tsvector FTS |
| Bundle | SQLite + sqlite-vec, **same DDL** |
| Parsing | Docling, behind a `Parser` protocol |
| Queue | Postgres `jobs` table, `FOR UPDATE SKIP LOCKED` |
| Embeddings | Pluggable: voyage-context-4 (hosted) / BGE-M3 (self-host) |
| Rerank | BGE-reranker-v2 |
| LLM access | LiteLLM router, Pydantic structured output |
| Term matching | `pyahocorasick` |
| Tool surface | MCP (FastMCP) |

Two deployment targets, one schema. The deterministic core
(parse / normalize / lexicon / pattern-edges / bundle) is **stdlib-only** — every
external model or vendor SDK lives behind a protocol in `dge.interfaces` and is an
adapter you opt into.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # core, no third-party dependencies

# Opt into adapters as needed:
pip install -e '.[embed]'   # local ONNX embedder (fastembed)
pip install -e '.[llm]'     # L1/L3 model adapters (litellm, pydantic)
pip install -e '.[parse]'   # Docling parser for PDF/DOCX
pip install -e '.[all]'     # everything
pip install -e '.[dev]'     # pytest, mypy, ruff
```

## Usage

```bash
export PYTHONPATH=src

# Ingest documents into a single-file bundle
dge ingest samples/sample_act.txt -o bundle.sqlite

# Compute L2 vectors (offline, no key)
dge embed -b bundle.sqlite --provider local

# Query: seed → expand closure to a fixed point → expand context on a budget
#        → assemble in document order → report a soundness verdict
dge query "transfer made in the ordinary course of business permitted" \
    -b bundle.sqlite -k 8 --use-vectors --show-provenance

# L3 dry run — no key, no network. Prices the corpus, reports conflict findings.
dge extract -b bundle.sqlite --dry-run
```

`dge query` prints the assembled **context** and a soundness verdict naming any
omitted exception — it does not generate an answer.

## Development

```bash
ruff check src scripts tests
mypy --strict src
python -m pytest tests/ -q
```

The traversal logic is pure functions tested against fixture graphs with no
database. Traversal changes also run the eval harness.

## Project status

Research prototype under active development. The full loop runs end to end on real
Indian bare acts — **ingest → embed → query** — with closure traversal and a
soundness verdict. The test suite (157 tests) passes; `mypy --strict` and `ruff`
are clean.

| Phase | State |
|---|---|
| 0 · Validate the assumption | Passed — closure chains are sparse on real data |
| 1 · Substrate + baseline retrieval | Mostly done — L1 normalizer outstanding |
| 2 · Term symbol table | Not started |
| 3 · Closure edges + soundness | Soundness half met and measured; recall half in progress |
| 4 · MCP tool surface | Not started |
| 5 · Cross-document | Deferred (post-MVP) |
| 6 · Cost and scale | Deferred (post-MVP) |

Measured result: over 170 real queries on a 9-act bundle, the soundness check
reports a **0% violation rate**; run against a seeds-only (flat-RAG) context the
same check reports **75% of answers unsound**. That contrast is the product
claim, measured. See [`BUILD_PLAN.md`](BUILD_PLAN.md) and
[`decisions.md`](decisions.md) for the full evidence trail.

## Repository layout

```
src/dge/
  pipeline.py        stage orchestration
  parsing.py         L0 deterministic substrate
  normalize.py       L1 normalization
  lexicon.py         term extraction
  edges.py           pattern-based edge detection
  retrieval/         dense + sparse seeding, RRF fusion
  traversal/         closure/context expansion, assembly, soundness (pure functions)
  l3/                model-based edge extraction, evidence-span validation
  adapters/          embedder / reranker / LLM adapters (behind protocols)
  domains/legal.py   the legal domain pack
  bundle.py          SQLite bundle reader/writer
  cli.py             command-line entry point
docs/                design documents 00–08
sql/schema.sql       portable DDL (Postgres + SQLite)
tests/               unit + fixture-graph tests
```

## License

Not yet licensed. All rights reserved by the authors pending a license decision.
