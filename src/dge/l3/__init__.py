"""L3: model-extracted typed edges, and everything that makes them trustworthy.

Read in this order — it is the order the layer was built in, and the order it
runs in:

  `evidence`  CLAUDE.md invariant 10, in code. Built and tested BEFORE any
              model call existed, because every other module here is only worth
              having if this one holds.
  `sections`  one section per call (docs/05 5.3), and the cost gate that
              decides whether a section is worth a call at all.
  `schema`    Pydantic structured output with a closed edge-type enum and an
              explicit null option.
  `prompt`    the prompt program and its stable `prompt_hash` (invariant 5).
  `conflict`  competing non obstante clauses — flagged, never resolved.
  `run`       orchestration: gate -> call -> validate -> stamp -> reconcile.

No module in this package imports a vendor SDK. The model lives behind
`dge.interfaces.EdgeExtractor`; `dge.adapters.extract_llm` is the only file
that knows litellm exists.
"""
