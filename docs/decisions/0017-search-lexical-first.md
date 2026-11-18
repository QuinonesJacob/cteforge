# 0017 — Search: lexical ranking first

Status: accepted · 2026-11-16 · amended by ADR 0023 (hybrid meaning search)

## Context
Agents call `search` at the start of a turn. Embeddings need a model/API key and add an
external dependency to a self-hosted tool.

## Decision
Lexical scoring over title, keywords, meaning, use_when, dont_use_when, variable names and
descriptions, output columns and tables (BM25-style, pure Python in services over the store's
definition list). Verified definitions rank above llm_annotated at equal relevance; drafts
are excluded unless asked for. Embeddings are a later, optional plug-in.

## Consequences
- Works offline and deterministically; keywords/synonyms written by analysts matter.
