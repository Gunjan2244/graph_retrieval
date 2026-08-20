# 08 — On Rust

Short answer: yes, incrementally, and you should design for it now — but Python
is almost certainly not your bottleneck, and rewriting for speed would be
solving the wrong problem.

## 8.1 Where the wall-clock time actually goes

Profile before believing anything below, but the expected shape for this system:

| Stage | Dominated by | Python's share |
|---|---|---|
| Parse (L0) | Docling model inference (PyTorch/C++) | negligible |
| Normalize (L1) | LLM API latency | negligible |
| Lexicon / mention linking | `pyahocorasick` (C) | negligible |
| Edge extraction (L3) | LLM API latency | negligible |
| Embedding | GPU or remote API | negligible |
| Retrieval | Postgres | negligible |
| Traversal | Postgres index lookups | small |

Ingest is **I/O- and inference-bound**, not interpreter-bound. Expect >90% of
wall time in model calls you do not control. A Rust rewrite of the orchestration
would move a number that is already close to zero.

The two places Python genuinely costs you:

1. **Traversal on large in-memory graphs** — if you ever load a whole corpus
   graph into process memory and run millions of frontier expansions.
2. **The deterministic pattern pass** at very large corpus scale, if you outgrow
   what `pyahocorasick` handles comfortably.

Neither is a Phase 1–4 problem.

## 8.2 The real argument for Rust — and it is not speed

**The bundle reader.** A Rust core that reads a `.bundle` and executes traversal
gives you:

- a **single static binary** with no Python runtime — which matters enormously
  for on-prem legal deployments where "install Python 3.12 and these 40 packages"
  is a procurement conversation
- **WASM**, so the same reader runs client-side: in a browser, in a VS Code
  extension, in a Word or drafting-tool plugin — with the bundle never leaving
  the machine
- a stable ABI other languages can bind to

For a product selling into law firms, "one binary, works air-gapped, reads the
file locally" is a commercial feature, not a performance one. That is the reason
to write Rust, and it is a good one.

## 8.3 Keep the port cheap: where to draw the line now

The starter is already shaped for this, and the shape is worth preserving:

- **`traversal/policy.py` is pure functions over plain data** — no database, no
  I/O, no framework. That module is the natural first port: it is the hot path,
  it is self-contained, and it has no dependencies to replace.
- **Everything external is behind a `Protocol`.** Swapping a Python
  implementation for a PyO3-backed one is an import change.
- **The schema is portable SQL.** Rust reads the same SQLite bundle with
  `rusqlite`; nothing about the format assumes Python.
- **Domain packs are data** — regexes and tables. They port to Rust as data, not
  as rewritten logic.

Two rules to keep this true as the code grows: no vendor SDK types in core
signatures, and no business logic inside I/O functions.

## 8.4 Migration path

Strangler pattern, hot module first:

1. Ship everything in Python. Measure.
2. If traversal shows up in the profile, port `policy` + frontier expansion to
   Rust, expose via **PyO3/maturin**. Python keeps orchestration; Rust does the
   inner loop. Same tests must pass against both implementations — keep the
   Python version as the reference oracle, do not delete it.
3. Build the **bundle reader** in Rust as a deliberate product decision
   (single binary + WASM), regardless of profiling.
4. Consider porting the deterministic pattern pass (`aho-corasick` and `regex`
   crates are excellent and faster than the Python bindings).

**Never port:** the LLM orchestration, the parser adapters, or the eval harness.
The Rust ecosystem for model clients and document parsing is thin, and you would
be maintaining bindings instead of building the product. Docling is Python; that
alone settles where ingest lives.

## 8.5 If you want Rust anyway

A defensible split, if you would rather write Rust than Python:

- **Rust:** bundle format reader/writer, traversal engine, pattern/lexicon pass,
  MCP server.
- **Python:** ingest pipeline (parser + model calls), domain pack authoring,
  evaluation.

They meet at the SQLite bundle, which is a clean process boundary requiring no
FFI at all. This is more work up front and a slower path to Phase 3, so only
take it if the single-binary distribution story is load-bearing for your first
customer — which, for on-prem legal, it plausibly is.

The order that matters: **get the edges right first.** A Rust engine traversing
a graph with bad `exception_of` extraction is a fast wrong answer.
