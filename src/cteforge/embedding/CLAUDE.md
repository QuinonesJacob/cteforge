# src/cteforge/embedding/

I/O adapter: the text embedding model for meaning search (ADR 0023, DESIGN §19). Loads
model files (downloading them on first use) and turns text into vectors. Nothing else.
Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `base.py` — the `Embedder` protocol services code against: `model_name`, `dim`,
  `status() -> ModelStatus` (not_loaded | loading | ready | failed), `error`, `load()`
  (blocking; raises EmbeddingError), `load_in_background()`, `embed(texts, *, query=False)
  -> list[list[float]]` (unit-length vectors; `query=True` for search queries, otherwise
  documents/passages).
- `fastembed_model.py` — `FastEmbedEmbedder(cache_dir)`: `BAAI/bge-small-en-v1.5` (384
  dims) through fastembed (ONNX, CPU). Lazy (fastembed imported and the model opened on
  first `load`/`embed`); thread-safe (one load at a time, embeds serialized); a failed load
  is not retried for `RETRY_AFTER_SECONDS` (offline servers don't retry on every search);
  passages via `passage_embed`, queries via `query_embed` with bge's `QUERY_INSTRUCTION`.
  `download_model(cache_dir)` pre-downloads and checks the model (offline servers).

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- The search document text — domain/search.py. Storing vectors — store/ (`search_vectors`).
- Similarity, thresholds, fusion with keyword search, when to index — services/_meaning.py
  and services/search.py.
- Reading `CTEFORGE_EMBED_CACHE` / `CTEFORGE_SEARCH_EMBEDDINGS` — services/context.py.

## Interactions
Imports domain (errors) and fastembed only; the only package allowed to import fastembed,
onnxruntime or loguru (import-linter). Imported only by services. Must not import other
adapters.

## Tests
tests/unit/embedding/ with a fake fastembed model. Tests never download or open the real
model: tests/conftest.py refuses `FastEmbedEmbedder._open`, gives contexts no embedder by
default, and sets CTEFORGE_SEARCH_EMBEDDINGS=off / HF_HUB_OFFLINE=1 for subprocesses.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
