# 06 — Evaluation and Roadmap

## 6.1 Build the eval before the engine

This architecture has a specific failure signature: **the seed is right and the
traversal is wrong, or vice versa** — and the two are indistinguishable from the
final answer alone. Graph systems are very good at appearing to work while
retrieving plausible-but-wrong neighborhoods.

Instrument from day one. For every query, log:

- the seed set (post-rerank)
- the expanded set, and **which edge types fired**
- the assembled context
- where the gold span landed: **in the seed / in the expansion / never reached**

That three-way split tells you exactly what to fix — the embedder, the edge
extractor, or the budget table. Without it you are tuning blind.

## 6.2 The failure taxonomy exercise — do this first

Before building anything, collect 50–100 real queries where current retrieval
produces discrepancies and label **why** each failed:

- lost referent
- lost scope
- lost exception
- wrong version
- needs aggregation
- embedding matched topic, not proposition

**This determines the build order.** If 70% are lost scope, Layer 1 ships next
week and the graph is optional. Graph construction is expensive and brittle;
paying for it before knowing it is the binding constraint is the most likely way
this project wastes a year.

## 6.3 Metrics

### Component metrics

| Component | Metric |
|---|---|
| Parse (L0) | Structural validity rate; table header resolution rate; manual spot-check on n=50 |
| Normalize (L1) | Referent resolution accuracy; restatement faithfulness (no added claims) |
| Lexicon | Definition-site precision/recall vs. hand-labeled; mention linking accuracy |
| Edges (L3) | Precision and recall **per edge type** — never aggregate; closure types matter far more than context types |
| Retrieval | Recall@k on seed; nDCG post-rerank |
| Traversal | Gold-span reach rate; tokens per correct answer |

### System metrics — the ones that matter

- **Soundness violation rate.** Fraction of answers citing a node whose inbound
  closure edges were absent from context. Target: zero, by construction.
- **Exception recall.** Of questions whose correct answer requires an exception,
  fraction where the exception reached the context.
- **Stale answer rate.** Fraction of answers drawn from superseded nodes.
- **Confident-wrong rate.** The headline number: answers that are wrong *and*
  unhedged. This is the metric the product exists to move, and it is the one to
  put in the demo.

### Baselines to beat — in this order

1. Flat chunking + dense retrieval (the naive bar).
2. **Normalized nodes + inherited context + hybrid + rerank, no traversal.**
   This is the honest baseline. If traversal does not beat it by a clear margin
   on labeled failures, the graph is not earning its ingest cost and the **edge
   extractor** is what to fix — not the traversal policy.
3. Long-context: whole document(s) in the prompt. Beats everything on small
   corpora; the comparison establishes where the crossover is and therefore
   where the product's market actually starts.

Report all three. A vendor who publishes the long-context baseline is more
credible than one who hides it.

## 6.4 MVP definition

**One domain. ~200 documents. Three edge types: `supersedes`, `exception_of`,
`defines`.**

Deliverables:

- Ingest pipeline, L0–L2, with parse-confidence gating
- Term symbol table with scope resolution and lint output
- Deterministic + pattern edges; L3 model extraction for the three types only
- Closure traversal with reverse index and the soundness check
- MCP server exposing the 04.9 tool surface
- Eval harness with the three-way instrumentation
- Public eval showing correct answers where flat-chunk RAG is **confidently
  wrong** — not merely worse, but wrong in a way that would cost someone money

**That demo is the whole pitch.** Everything in this document set is downstream
of whether it can be produced.

## 6.5 Milestones

| Phase | Goal | Exit criterion |
|---|---|---|
| **0. Validation** | Failure taxonomy on 50–100 real queries; measure closure-edge density on real documents | Sparsity assumption (04.2) confirmed; failure mix known |
| **1. Substrate** | L0 + L1 + L2, hybrid + rerank | Beats naive chunking; parse gating works; this is a shippable product already |
| **2. Lexicon** | Terms, scopes, shadowing, lint | Lint output that a domain expert calls useful |
| **3. Closure** | Three edge types, reverse traversal, soundness check | Exception recall and stale-answer rate move measurably |
| **4. Surface** | MCP server + SDK + bundle format | Drops into an agent stack without integration work |
| **5. Corpus** | Cross-doc identity, version chains | Multi-document supersession answered correctly |
| **6. Scale** | Selective L3, caching, cost instrumentation | Per-page ingest cost within pricing model |

Phase 1 is independently valuable and independently sellable. Reaching it and
stopping is a real outcome, not a failure.

## 6.6 Commercial shape

- **Price on ingest** (per page / per document), with a retrieval component.
  Never seats alone.
- **Deployment: VPC / on-prem capable from day one** (05.7).
- **Distribution: the MCP server**, not the format. The format opens later, if
  the tool wins.
- **The lint feature may be the wedge that opens the door** — "27 defined terms
  never defined" is legible to a buyer in five seconds, whereas retrieval
  quality requires them to trust a benchmark.
- **Positioning sentence:** retrieving a rule without its exceptions, or
  answering from a superseded version, is what makes document AI unusable in
  serious settings — this system prevents it by construction, and shows its
  work.

## 6.7 Open questions to resolve next

1. **Which domain?** Determines edge ontology, buyer, and sales motion. Contracts
   recommended (01.5) but not decided.
2. **What is the corpus change rate?** Changes cost model, pricing, and whether
   L3 is precomputed or lazy (05.4).
3. **Is the closure-sparsity assumption true in the target corpus?** The whole
   traversal design depends on it. Measure it in Phase 0.
4. **Can definition-site patterns reach acceptable precision** in the target
   domain, or does the lexicon need a model?
5. **Where is the long-context crossover?** Below some corpus size the product
   has no market. Know that number.
6. **Buy vs. build for parsing** — which vendor, and what is the fallback if
   pricing or terms change?
7. **Human review UX.** Low-confidence parses and edges need a review surface.
   In regulated domains this is a feature, not an admission — but it must be
   designed, not bolted on.

## 6.8 The standing risk

Long context and improving retrievers keep absorbing the simple end of this
market. The moat is not "we retrieve more relevant text" — that erodes with
every model release.

The moat is the **structural guarantee about exceptions and versions**, which no
context window fixes, because a model cannot apply a caveat it was never shown
was amended. Every product decision should be checked against whether it
strengthens or dilutes that claim.
