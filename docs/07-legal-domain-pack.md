# 07 — Legal Domain Pack

Why law is the right first domain, and what each legal edge type actually means.

## 7.1 Why law first

Statutory text **marks its own closure relations lexically**. "Provided that",
"notwithstanding anything contained in", "subject to the provisions of",
"nothing in this section shall apply" — these are not stylistic choices, they
are drafting conventions with settled meaning. A regex finds them.

That is unusual and it is the whole reason law is the wedge: a large share of
the highest-value edges in the corpus are obtainable at **zero inference cost**.
In most domains you pay a large model to guess at structure; here the drafter
already labelled it.

Two more properties that fit the architecture:

- **Documents are static after execution or enactment**, so Layer 3 cost
  amortises over the document's life. Ingest can be lavish.
- **The failure mode is legible to a non-technical buyer.** "You quoted me a
  section without its proviso" or "you relied on a judgment that was overruled"
  needs no benchmark to be understood as unacceptable.

## 7.2 The statutory closure relations

### Proviso — the archetype

A proviso carves an exception out of the clause immediately preceding it. It is
almost never semantically similar to the query that triggers the main clause,
which is exactly why flat retrieval misses it.

**Retrieving a section without its provisos is the canonical wrong answer.**
Provisos are `mandatory=True` in the traversal policy for this reason. Note that
"Provided further that" chains — a proviso to a proviso — genuinely occur, which
is why closure runs to a fixed point rather than to a depth.

### Non obstante — "notwithstanding anything contained in…"

An override clause. It tells you that where this provision conflicts with the
referenced one, this one wins. Resolve the reference to a node and write an
`overrides` edge.

Two competing non obstante clauses is a real, litigated conflict. **Flag it;
never silently pick one.** Surfacing "these two provisions each claim to
override the other" is more valuable than any answer you could synthesise.

### Subject to — the inverse

"Subject to the provisions of section X" means this provision *yields* to X.
Same mechanism, opposite direction. Getting the direction wrong inverts the
answer, which is why edges are directed and typed rather than scored.

### Explanation — binding, not commentary

An Explanation in an Indian statute is part of the enactment and can extend or
restrict meaning. Treat it as `CLOSURE`, not as helpful context. An Illustration,
by contrast, is genuinely illustrative — `CONTEXT`.

### Deeming provisions

"Shall be deemed to be" creates a legal fiction that displaces ordinary meaning.
A node using the term without the deeming provision is a wrong answer, so
deeming edges are closure.

### Repeal and savings

A savings clause preserves the operation of a repealed statute for prior acts.
So "repealed" does not mean "irrelevant" — the version chain must retain the
repealed provision with a `saves` edge and a temporal scope, not drop it. This
is why superseded nodes are **labelled** rather than filtered out.

### Amendment surgery

Amendment Acts operate by text substitution: "for the words X, the words Y shall
be substituted". This means the *as-amended* text often exists in no single
document. Two options, and you should decide explicitly:

1. **Store the amendment as edges only**, and assemble the as-amended reading at
   query time. Faithful, more expensive, always current.
2. **Materialise a consolidated version node**, keyed by corpus version and
   rebuildable from edges — a cache, never ground truth (invariant 8).

Option 1 first; option 2 as an optimisation once the edges are trusted.

## 7.3 Case law relations

**`overruled_by` and `per_incuriam` are the stale-answer failure in its purest
form.** Citing a judgment that is no longer good law is the legal equivalent of
answering from a superseded policy, and it is the demo that sells this product.

**`distinguished_by` is closure, not context** — a slightly non-obvious call.
Distinguishing narrows applicability; a precedent retrieved without the case that
distinguished it will be applied to facts it no longer governs.

`affirmed_by`, `followed_by`, and `relies_on` are context — they strengthen an
answer that is already correct.

Two things this pack does *not* attempt, deliberately:

- **Ratio vs obiter.** Which part of a judgment is binding is contested by
  lawyers about specific judgments. Do not have the extractor assert it. Mark
  candidate `obiter_in` at low confidence and surface it; never filter on it.
- **Bench strength and precedential hierarchy.** Worth capturing as node
  metadata (court, coram size, date) so the model can reason about it. Not worth
  encoding as automated authority rules.

## 7.4 Definitions: `means` vs `includes`

This distinction is litigated constantly:

- **"means"** — exhaustive. The definition replaces the ordinary meaning.
- **"includes"** — illustrative. The definition *extends* the ordinary meaning
  without displacing it.

Store which one it was on the term node. An engine that flattens both into
"definition" is giving wrong answers about scope, confidently.

Also, `"For the purposes of this section"` scopes a definition to that section
only — the same term may be defined differently elsewhere in the same Act. This
is precisely the shadowing case the symbol table exists for, and it is common
enough in Indian statutes to be a headline feature rather than an edge case.

## 7.5 Contracts

Same mechanism, different vocabulary:

| Contract construct | Edge |
|---|---|
| "Notwithstanding the foregoing" | `overrides` (preceding clause) |
| "Provided, however, that" | `exception_of` |
| "Except as set forth in Schedule X" | `exception_of`, cross-reference |
| "Subject to Clause 9.2" | `conditioned_on` |
| Survival clause | `survives_termination` |
| Liability cap | `capped_by` — qualifies **every** obligation |
| Amendment / side letter | `amends`, resolved into a version chain |
| Defined-terms clause | scoped definitions, shadowing the corpus glossary |

The liability cap is worth noting: it is a **hub node** with enormous fan-out.
It is mandatory closure, so it always comes along — and the degree penalty in
the frontier score is what stops the rest of the boilerplate from flooding in
with it.

## 7.6 Extending beyond law

The pack framework exists so that later domains slot in beside `legal` rather
than replacing it. What transfers:

| Domain | Closure analogue |
|---|---|
| Clinical guidelines | contraindications, "except in patients with…", superseded guideline versions |
| Technical docs | `deprecates`, `replaced_by`, version constraints, breaking changes |
| Finance / regulatory filings | restatements, amendments (10-K/A), covenant carve-outs |
| Academic | retractions, errata, corrigenda, replication failures |
| Standards | superseded revisions, national deviations from a base standard |

Each needs its own marker patterns and its own `should_run_l3` gate. **None of
them needs a change to traversal**, and that is the test of whether the
abstraction is right. If adding a domain requires editing `policy.py`, the pack
boundary has leaked.

## 7.7 India-specific notes

- **Citation formats to support:** SCC, AIR, SCR, SCC OnLine, and the neutral
  citations (`2023 INSC 118`, `2024 DHC 4412`) now used by the Supreme Court and
  High Courts. Neutral citations are the most reliable strong key for
  cross-document identity — prefer them for entity resolution.
- **Bare acts** are widely available in clean text form, which makes Phase 0
  cheap: you can measure closure density on real statutory language without
  solving PDF parsing first.
- **Recodifications** (e.g. the criminal law recodification) are a supersession
  problem at scale, and a good showcase: mapping old-section to new-section is
  exactly the version-chain mechanism, and getting it wrong is precisely the
  failure lawyers fear.
- **Amendment Acts** are published separately from the principal Act, so the
  as-amended text is a cross-document assembly problem by default. This is a
  strong demo: ask a flat-RAG baseline about a provision amended twice and watch
  it answer from the original.
