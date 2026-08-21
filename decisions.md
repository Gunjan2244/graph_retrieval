# decisions.md

Major decisions, with the reason. Newest first. A decision that is only a
restatement of `CLAUDE.md` does not belong here; this file is for the calls
that could reasonably have gone the other way.

---

## 2026-08-21 — Phase 3 exit: half met, and the shortfall is the extractor

**Decision: leave Phase 3's exit unchecked, and do not touch the traversal
policy.**

The measurement (`scripts/phase3_exit_report.py`, evidence in `BUILD_PLAN.md`):
soundness violation rate is 0 over 170 real queries and the check is
demonstrably not vacuous — the same check against a flat-RAG context is unsound
on 75.3% of them. But on all 15 labeled failure cases, closure traversal
changed nothing: seeds-only reaches 10/15, the full pipeline 11/15, and the one
extra case arrived on the context frontier rather than through a closure edge.
Exception recall is 3/5 in both arms.

docs/06 §6.3 pre-committed to what that means — "if traversal does not beat it
by a clear margin on labeled failures, the **edge extractor** is what to fix,
not the traversal policy" — so the policy was left alone. The tempting move,
making `part_of` closure-traversable so a clause reaches its section's
relations, would have turned several of these cases green and is precisely what
invariant 6 forbids doing casually. It would also have been treating the
symptom: the Child Labour carve-out has **zero inbound edges**, so no traversal
rule reaches it.

**Why the extractor misses it, traced rather than assumed:** L3 runs one
section per call, so a carve-out naming ss. 7, 8 and 9 from inside s. 9 can
only ever be linked to units that shared its window — the model cannot propose
an endpoint it was not shown, and `validate_candidate` is right to reject one.
The deterministic path does not cover the gap either: `_REF_RE` takes a single
match and does not handle "Secs . 7, 8 and 9". Corpus-wide the split is sharp —
self-referential provisos link at 89% (463/521), cross-reference carve-outs at
62% (5/8), and the cross-reference form is the one two of the five labeled
`lost_exception` cases depend on.

## 2026-08-21 — "No opinion" beats "inferred denial" in L3 reconciliation

**Decision: a MEDIUM pattern edge is degraded only when the model was actually
in a position to confirm it — both endpoints together in one examined window,
after a call that succeeded.**

Two separate bugs shared this root, and both silently rewrote the graph:

- A section whose call raised was still marked examined, so a transient 429
  halved the confidence of every MEDIUM pattern edge in it. Measured:
  `Foreign_Marriage_Act,_1969` completed 0 calls and had 28 edges pushed below
  the traversal floor; 110 across one run.
- Reconciliation checked only that `edge.src` had been examined, never that
  `edge.dst` shared the window. Since L3 shows one section per call, every
  cross-section `defines` link — a provision to the Sec. 2 definition of a term
  it uses — was guaranteed to be degraded. On the Child Labour Act that was 17
  of 21 degraded edges.

The alternative reading is that a model shown *part* of a relation and not
proposing it is weak evidence against. Rejected: `validate_candidate` would
have discarded such a proposal anyway, so there is no answer the model could
have given that counts as confirmation. Degrading on that basis is inferring a
denial from a question never asked, and it makes the graph a function of window
boundaries and network weather rather than of the corpus (invariant 8).

## 2026-08-21 — A rate limit is not evidence about structured-output support

**Decision: the one-way `_structured_output` latch in the LiteLLM adapter trips
only on errors the provider actually raised about the request. Transient
failures (429, 5xx, timeouts, connection resets) propagate as a recorded
section failure.**

The latch exists for a good reason — a provider that cannot do `json_schema`
will not start being able to mid-run, and retrying per section doubles the
bill. But it was keyed on *any* exception. One 429 flipped the whole run into
JSON mode, and a later section came back with `rel_type` instead of `type` and
was lost to a validation error. Classification reads `status_code` when the
exception carries one, falling back to the class name, so it stays vendor-
neutral and needs no litellm import at module scope.

## 2026-08-21 — Measure extraction quality by replay, not by re-spending quota

**Decision: record every L3 wire response, then re-run the fixed pipeline
against the recording.**

The free tier is 20 requests/day/model (verified on two models), so a live
re-run after each fix was not available — and would not have been reproducible
anyway. Replaying the recorded responses through the real parse, validation and
reconciliation path measured the fix against the exact model output that
exposed the bug, and confirmed unconfirmed-edge count dropping 21 -> 4 with no
new calls.
