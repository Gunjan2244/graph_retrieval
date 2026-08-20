# 04 — Retrieval and Traversal

This is the differentiator. Everything else is infrastructure that makes this
possible.

## 4.1 Pipeline shape

```
query
  ├─ hybrid search over nodes (dense + BM25, RRF)      → candidates
  ├─ cross-encoder rerank                              → seed set
  ├─ temporal / authority filter                       → current versions
  ├─ CLOSURE expansion    (fixed point, unbudgeted)    → soundness
  ├─ CONTEXT expansion    (best-first, budgeted)       → helpfulness
  ├─ assemble in document order + inherited context
  │                                                    → answer
  └─ post-answer soundness check on cited nodes        → expand & retry, or ship
```

## 4.2 Depth is the wrong knob

In a dense graph, depth-N reaches everything for small N. Depth-limited BFS is
uniformly wrong in both directions at once: it truncates a 4-hop chain of
critical exceptions while happily pulling 200 irrelevant 2-hop neighbors.

The reframe that makes bounded traversal possible:

> **Closure edges are sparse; context edges are dense. The "everything is
> connected" property of a document lives almost entirely in edges that do not
> affect correctness.**

Every sentence relates loosely to hundreds of others. Few sentences have
exceptions, and fewer still have exceptions-to-exceptions — these chains
typically run 1–3 deep and terminate on their own. So:

- **Closure edges → traverse to a fixed point.** No depth limit, no budget. Run
  until nothing new arrives.
- **Context edges → greedy, budget-bound, cut freely.**

**Validate the sparsity assumption on real documents before building.** If
closure edges turn out dense in the target corpus, this design needs rework.

## 4.3 Reverse traversal is mandatory for closure edges

The most commonly missed point in graph RAG. If the seed is *"returns accepted
within 30 days"* and the graph holds `(node_88, exception_of, node_12)`, the
exception is **downstream** of the seed. Following outgoing edges never reaches
it.

Retrieving a rule without its exception is worse than retrieving nothing — it
produces a confident wrong answer.

**Rule:** for `exception_of`, `supersedes`, `amends`, `conditioned_on`, traverse
the reverse index by default, and make inclusion **non-optional**. If a node has
an inbound closure edge, the neighbor comes along regardless of budget — or the
answer is flagged incomplete.

## 4.4 Best-first expansion for context edges

Replace BFS-by-depth with a scored priority frontier:

```
score = edge_type_prior
      × query_relevance(node, query)
      × decay^hops
      × 1 / log(1 + degree(node))
      × (cross_document ? 0.4 : 1.0)
      × confidence(edge)
```

Pop until the token budget is spent. A highly relevant node 5 hops out beats a
marginal one at 2 hops — which is the behaviour depth limits get exactly wrong.

**The degree term is IDF for graphs.** High fan-out nodes are hubs (boilerplate,
"the Agreement", ubiquitous terms). Hubs are precisely what make a graph *feel*
fully connected while carrying almost no information. Suppressing them by degree
collapses the effective branching factor dramatically. This one term does more
work than any depth limit.

### Per-edge-type policy table

A uniform policy is fatal — this is config, not code:

| Edge type | Direction | Policy |
|---|---|---|
| `exception_of` | reverse | Always, unbounded, non-optional |
| `supersedes` / `amends` | reverse | Always, resolve to head |
| `conditioned_on` | forward | Always, depth ∞ (chains are short) |
| `defines` | forward | Depth 1 full span, depth 2+ gloss only |
| `coref` | — | Resolve inline; do not add a node |
| `supports` / `exemplifies` | forward | Only for evaluative / "why" queries |
| `cites` | forward | Depth 1, cross-doc cost applies |
| `similar_to` | — | **Never traverse** — that is what seeding was for |

## 4.5 Termination: saturate, don't count

Cap on **total tokens**, not node count. Track entities, terms, and claims
already present in the assembled context; expand while new nodes introduce new
ones; stop when the last several arrivals add nothing.

Redundancy is the signal that the neighborhood is covered — and it fires at
different depths for different queries, which is exactly the desired behaviour.

Keep a visited set. Cycles are real: `A qualifies B`, `B qualifies A` occurs in
legal text.

## 4.6 Assembly

Sort the collected closure by **source document position**, not retrieval rank,
and emit with inherited-context prefixes:

```
[§4.2 Returns → General]     Returns are accepted within 30 days of delivery.
[§4.2 Returns → Exclusions]  Clearance items are excluded from the return policy.
```

The same two facts in similarity-rank order read to a model as a contradiction.
In document order they reconstruct the logic.

Splice term glosses inline (`Territory [= the countries listed in Schedule B]`)
rather than appending definition blocks.

Label superseded nodes explicitly rather than dropping them.

## 4.7 The soundness check — the guarantee

After the model answers with node citations:

1. For every cited node, look up its **inbound closure edges**.
2. If any such neighbor was not in the assembled context, the answer is unsound.
3. Expand to include it and re-run.

This is one index lookup per citation — cheap. It converts the architectural
claim from a hope into a stateable guarantee:

> The system cannot answer from a rule while omitting a known exception to it,
> or from a version known to be superseded.

Note the honest scope of that claim: *known* exceptions. It is a guarantee about
consistency between the graph and the answer, not about the completeness of
extraction. Extraction recall is an eval number (06), not a guarantee — and
saying so plainly is what makes the guarantee credible to a serious buyer.

## 4.8 Agentic expansion for the hard tail

Static policy cannot predict every multi-hop question. Expose `expand()` to the
model and let it request more after seeing an initial context — *"I have the
rule but it references Schedule B; fetch that."*

Static closure for the common case; agentic expansion for the residual, at the
cost of round trips.

## 4.9 Tool surface

The LLM never reads the format — it calls functions. This contract *is* the
product; the bundle behind it can change freely.

```
search(query, filters)                 → seed nodes with inherited context
get_node(id) / get_section(id)         → exact spans
neighbors(id, edge_types, direction)   → typed, directional
expand(id, budget, policy)             → budgeted closure
goto_definition(term, at_node)         → the binding in scope *here*
find_references(term, scope)           → every mention
glossary(node)                         → terms in scope with glosses
timeline(entity | node)                → version / supersession chain
lint(document)                         → undefined, unused, circular, shadowed
```

Ship as an **MCP server** plus an SDK. That is the distribution channel: it
drops into Claude, Cursor, and every agent framework without asking anyone to
change their stack.

The LSP-derived naming is deliberate — models already know `goto_definition` and
`find_references` semantics cold, which means less prompt engineering to get
correct tool use.

## 4.10 Setting the numbers

Do not guess budgets. On the labeled eval set, record for every query the hop
distance at which the gold span was reached. The distribution will show a sharp
knee — most answers within closure + one context hop, with a thin tail. Budgets
come from that curve.

It is also the fastest way to find mis-typed edges: **if a "context" edge type
keeps appearing in gold paths, it was a closure edge all along.**
