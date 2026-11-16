# 0010 — Python 3.12 + uv

Status: accepted · 2026-11-15

## Context
sqlglot (best open-source SQL parser/transpiler), the official MCP SDK and all target warehouse drivers are Python.

## Decision
Python 3.12, uv for environments and packaging, ruff, pytest, import-linter. GUI later in React/TypeScript under web/.

## Consequences
- Two languages once the GUI exists; the boundary is the HTTP API.
