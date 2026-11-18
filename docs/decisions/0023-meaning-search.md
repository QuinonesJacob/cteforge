# 0023 — Search by meaning: a search document per definition, embedded on the server

Status: accepted · 2026-11-17 · amends ADR 0017

## Context
Keyword search misses synonyms ("revenue" vs "sales") unless analysts list them. Agents
search with their own words. Meaning search needs an embedding model: a fixed function
from text to numbers (not an LLM or an agent). A self-hosted tool must not need a second
server or send definitions to a third party.

## Decision
- **Search document**: one Markdown text per definition version, generated (never edited)
  from its fields: title, meaning, use/don't-use notes, keywords, variables with
  descriptions, outputs, grain, tables and joins. The structured fields stay the source
  of truth; the document is derived.
- **Embedding model runs inside the cteforge server**: `BAAI/bge-small-en-v1.5` via
  `fastembed` (ONNX on CPU, no PyTorch, MIT/Apache licences). Downloaded once on first use;
  it can be pre-downloaded for servers without internet access.
- **Vectors live in cteforge's own database** (a plain table, whatever `CTEFORGE_DB_URL`
  is), tagged with the model name; compared in Python (libraries are thousands of
  definitions, so milliseconds). No separate vector database. A model change re-embeds.
- **Hybrid ranking**: keyword (ADR 0017) and meaning results merged by reciprocal rank
  fusion; one `search` call for agents. Keyword still wins on exact names (table names,
  acronyms, IDs). If the model can't load, search falls back to keyword-only and says so.
- Past questions in the search document: later, opt-in (they may hold sensitive wording).

## Implementation notes (2026-11-17)
- The model fastembed ships for bge-small-en-v1.5 is a quantized ONNX file, about 65 MB on
  disk. Documents are embedded as passages; queries as queries with bge's retrieval
  instruction prepended (fastembed does not add it).
- The search document leaves out the version, state and verifier (verifying must not
  change it), owner, profile and the SQL; its joins are the ones mined from the SQL plus
  the declared join keys. A vector is kept per (definition, version, model) with the
  document's SHA-256; a different hash or model means re-embed.
- Vectors are written when a version is applied, only if the model is already loaded (an
  approval never waits for a download); searches fill in missing ones; `serve` and stdio
  `mcp` load the model and backfill in the background and answer by keyword meanwhile;
  `cteforge search-index rebuild | download-model` for admins and offline servers.
- Fusion: RRF with k = 60 (equal scores share a rank). Only definitions with cosine
  similarity ≥ 0.58 and within 0.08 of the query's best similarity count as meaning
  matches; others contribute nothing, so they cannot reorder keyword hits.
- Calibration (real model, demo library, 12 related and 14 unrelated queries): related
  queries scored 0.57–0.81 against their definition, unrelated ones 0.37–0.60 against the
  best definition ("weather forecast tomorrow" 0.44; "how much of the money we billed did
  we actually receive" 0.59 → realization_rate, 0.58 → booked_vs_collected). There is no
  clean gap for business-flavoured but unrelated questions ("inventory levels in the
  warehouse" 0.60): 0.58 keeps the paraphrases and drops the clearly unrelated; such
  meaning-only hits rank below hits that also match words.

## Consequences
- Synonyms work without keyword lists; general models still don't know company jargon,
  so keywords and meaning text still matter.
- Adds `fastembed` (and its ONNX runtime) to the server install.
