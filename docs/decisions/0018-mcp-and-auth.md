# 0018 — MCP transport and auth

Status: accepted · 2026-11-16

## Context
DESIGN open question: stdio vs HTTP for the self-hosted MCP endpoint, and how calls are
attributed for permissions and audit.

## Decision
- Official `mcp` Python SDK (FastMCP). Two transports: **stdio** (local, acts as the user
  named by `CTEFORGE_USER`) and **streamable HTTP** mounted on the API server.
- Auth on HTTP (API and MCP): bearer tokens. Tokens are random, shown once, stored as
  SHA-256 hashes; each maps to a user with a role: `viewer`, `contributor`, `approver`,
  `admin`. Agents get their own users with `actor_kind=agent`.
- Permissions: viewer → search/render/run; contributor → propose; approver → approve/reject
  and verify; admin → users and settings. A definition `owner` may approve changes to it.
  Agents can never verify (ADR 0007).
- Local single-user mode: `CTEFORGE_AUTH=off` makes every HTTP call act as the local admin;
  refused unless the server binds to localhost.

## Consequences
- Every call has an actor for the audit log.
