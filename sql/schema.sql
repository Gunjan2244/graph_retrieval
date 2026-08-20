-- Document Graph Engine — core schema
-- Portable between Postgres and SQLite. Vector columns are added by the
-- dialect-specific migration (pgvector / sqlite-vec); everything else is shared.

CREATE TABLE documents (
  doc_id            TEXT PRIMARY KEY,
  substrate_hash    TEXT NOT NULL,
  source_uri        TEXT,
  doc_class         TEXT,          -- contract | amendment | policy | ...
  effective_date    TEXT,
  status            TEXT NOT NULL DEFAULT 'unknown',
                                   -- current | superseded | draft | unknown
  superseded_by     TEXT REFERENCES documents(doc_id),
  parse_confidence  REAL,
  review_state      TEXT NOT NULL DEFAULT 'none',   -- none | pending | cleared
  tenant_id         TEXT NOT NULL,      -- see note below; never nullable
  acl_tag           TEXT,               -- matter/client/deal scope within a tenant
  ingested_at       TEXT NOT NULL
);

-- TENANCY NOTE (load-bearing in law: counterparty A's terms must never surface
-- inside counterparty B's answer). Enforce at TRAVERSAL, not by filtering the
-- final result set: an unauthorised neighbour is never expanded into, and the
-- user sees a gap. This degrades safely ONLY because enrichment is edges rather
-- than copied text — see CLAUDE.md invariant 4. If you ever materialise another
-- document's content into a node row, this control silently stops working.
CREATE INDEX idx_documents_tenant ON documents(tenant_id, acl_tag);

CREATE TABLE nodes (
  node_id           TEXT PRIMARY KEY,
  doc_id            TEXT NOT NULL REFERENCES documents(doc_id),
  kind              TEXT NOT NULL,  -- proposition | term | structural
  seq               INTEGER NOT NULL,   -- document order; drives assembly
  byte_start        INTEGER,
  byte_end          INTEGER,
  raw               TEXT NOT NULL,      -- written once, never updated
  normalized        TEXT,               -- L1 self-contained restatement
  section_path      TEXT,               -- '§4 > §4.2 > Exclusions'
  inherited_ctx     TEXT,               -- JSON: temporal scope, subject, conditions
  is_assertive      INTEGER NOT NULL DEFAULT 1,
  status            TEXT NOT NULL DEFAULT 'current',
  layer1_version    TEXT,
  model_id          TEXT
);

CREATE TABLE terms (
  term_id           TEXT PRIMARY KEY,
  surface_form      TEXT NOT NULL,
  canonical         TEXT,
  scope_node_id     TEXT REFERENCES nodes(node_id),   -- lexical scope of binding
  definition_node_id TEXT REFERENCES nodes(node_id),
  definition_kind   TEXT,               -- 'means' (exhaustive) | 'includes' (illustrative) | null
  gloss             TEXT,               -- one sentence; used at hop 2+
  variants          TEXT,               -- JSON array
  provenance        TEXT NOT NULL,      -- pattern | model | similarity
  confidence        REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE edges (
  edge_id           TEXT PRIMARY KEY,
  src               TEXT NOT NULL,
  dst               TEXT NOT NULL,
  type              TEXT NOT NULL,      -- exception_of | supersedes | defines | ...
  class             TEXT NOT NULL,      -- closure | context
  cross_doc         INTEGER NOT NULL DEFAULT 0,
  confidence        REAL NOT NULL DEFAULT 1.0,
  provenance        TEXT NOT NULL,      -- structural | pattern | model | verified
  model_id          TEXT,
  prompt_hash       TEXT,
  evidence_span     TEXT,               -- must appear verbatim in extractor input
  created_at        TEXT NOT NULL
);

-- Forward traversal.
CREATE INDEX idx_edges_src ON edges(src, type);

-- REVERSE traversal. Load-bearing: an exception points AT the rule it modifies,
-- so the soundness guarantee is a lookup on this index. Do not drop it.
CREATE INDEX idx_edges_dst ON edges(dst, type);

CREATE INDEX idx_edges_class ON edges(class, type);
CREATE INDEX idx_nodes_doc_seq ON nodes(doc_id, seq);
CREATE INDEX idx_terms_surface ON terms(surface_form);

-- Per-layer version keys, for surgical invalidation.
CREATE TABLE layer_versions (
  doc_id            TEXT NOT NULL REFERENCES documents(doc_id),
  layer             TEXT NOT NULL,      -- L0 | L1 | L2 | L3
  layer_version     TEXT NOT NULL,
  model_id          TEXT,
  prompt_hash       TEXT,
  computed_at       TEXT NOT NULL,
  PRIMARY KEY (doc_id, layer)
);

-- Idempotent, content-addressed job queue.
CREATE TABLE jobs (
  job_id            TEXT PRIMARY KEY,
  stage             TEXT NOT NULL,
  doc_id            TEXT,
  input_hash        TEXT NOT NULL,      -- dedup key: same input, same result
  state             TEXT NOT NULL DEFAULT 'pending',
                                        -- pending | running | done | failed | blocked
  attempts          INTEGER NOT NULL DEFAULT 0,
  failure_reason    TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE INDEX idx_jobs_claim ON jobs(state, stage);
CREATE UNIQUE INDEX idx_jobs_dedup ON jobs(stage, input_hash);

-- Cost and latency per stage per document. You cannot manage unit economics
-- you do not measure per document.
CREATE TABLE ingest_ledger (
  doc_id            TEXT NOT NULL REFERENCES documents(doc_id),
  stage             TEXT NOT NULL,
  duration_ms       INTEGER,
  input_tokens      INTEGER,
  output_tokens     INTEGER,
  model_id          TEXT,
  recorded_at       TEXT NOT NULL
);

-- Consolidated 'as-amended' text. A CACHE, never ground truth: rebuildable from
-- edges, keyed by corpus version, safe to delete. Amendment Acts operate by text
-- substitution, so the as-amended reading often exists in no single document.
-- Start by assembling it at query time; add this table only as an optimisation
-- once the amendment edges are trusted.
CREATE TABLE consolidated_cache (
  node_id           TEXT NOT NULL REFERENCES nodes(node_id),
  corpus_version    TEXT NOT NULL,
  as_amended_text   TEXT NOT NULL,
  applied_edge_ids  TEXT NOT NULL,     -- JSON array: provenance of the assembly
  computed_at       TEXT NOT NULL,
  PRIMARY KEY (node_id, corpus_version)
);

-- Bundle sidecar: original bytes, content-hashed, untouched. Base64 text keeps
-- this file dialect-portable (Postgres BYTEA vs SQLite BLOB) like every other
-- column here. See CLAUDE.md invariant 1 — this is the immutable ground truth
-- every derived row points back to via byte offsets.
CREATE TABLE document_blobs (
  doc_id            TEXT PRIMARY KEY REFERENCES documents(doc_id),
  raw_base64        TEXT NOT NULL,
  content_type      TEXT,
  filename          TEXT
);

-- Bundle-level key/value manifest: schema version, domain pack, layer
-- versions, counts, tool that produced it. Read by the bundle reader before
-- anything else so a mismatched reader/writer version fails loudly.
CREATE TABLE manifest (
  key               TEXT PRIMARY KEY,
  value             TEXT NOT NULL
);

-- Eval instrumentation. The three-way split (seed / expansion / never reached)
-- is the only way to attribute a retrieval failure to the right component.
CREATE TABLE eval_traces (
  trace_id          TEXT PRIMARY KEY,
  query             TEXT NOT NULL,
  seed_node_ids     TEXT,               -- JSON array, post-rerank
  expanded_node_ids TEXT,               -- JSON array
  edge_types_fired  TEXT,               -- JSON array
  gold_node_ids     TEXT,               -- JSON array
  gold_location     TEXT,               -- seed | expansion | unreached
  gold_hop_distance INTEGER,
  tokens_assembled  INTEGER,
  soundness_ok      INTEGER,
  recorded_at       TEXT NOT NULL
);
