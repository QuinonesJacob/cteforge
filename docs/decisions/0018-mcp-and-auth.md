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
  refused unless the server binds to localhost, and every request must arrive on a
  loopback socket (ASGI peer and local address; never trusted from the Host header).

## Consequences
- Every call has an actor for the audit log.

## Addendum (2026-11-16): four-eyes rule
- An author may not approve their own proposal, and may not verify a definition version
  they authored — unless they are an `admin` (this keeps single-user local mode workable).
- Agent-kind users can never be given `approver` or `admin` roles' verify power: verify
  checks `kind == human` at call time, whatever the role (ADR 0007).

## Addendum (2026-11-16): four-eyes across metadata-only versions; no stdio bootstrap
- "A version they authored" means any part of it: the author of the newest version up to
  it that changed the logic, and the author of every metadata-only version since. On
  approve(verify=True) of a metadata-only revision, the base version's authors count too.
  Otherwise the logic's author could verify their own SQL after anyone's cosmetic edit.
- stdio MCP (`cteforge mcp`) never creates CTEFORGE_USER, even in an empty store: the
  process at the other end is an MCP client, usually an agent, and must not become the
  first human admin. A person creates users first (`cteforge user add --bootstrap`). The
  CLI and auth-off HTTP (a person at this machine) still create the first local admin.

## Addendum (2026-11-16): the admin exemption is for human admins only
- The four-eyes exemption above applies only to admins with `actor_kind=human`. An agent
  with the `admin` role never approves its own proposal (whatever the target's state) and,
  as before, never verifies anything (ADR 0007).

## Addendum (2026-11-17): per-user default SQL dialect — superseded by ADR 0021 (never released)
- A user's dialect is a property of their account ("what my database is"), not something
  to pick on every import. `User.default_dialect` (optional) is stored in a separate
  `user_preferences` table (`metadata.create_all` adds it to existing databases; no
  migration, no schema-version bump).
- Any authenticated user — every role, humans and agents — may set or clear **their own**
  default (`PUT /api/v1/me/preferences`); setting another user's needs `manage_users`
  (admin; CLI `cteforge user dialect`). Audited as `admin` (`op=set_default_dialect`).
- When `parse_query` / `propose_definition` get no dialect: explicit → the user's default →
  the profile's dialect → PolicyError. Behaviour for a dialect that differs from the
  profile is unchanged (ADR 0013 addendum: `dialect_mismatch`, transpiled checks).
- `GET /profiles` also says which profile is the default (`default: true`), so the GUI
  can show the same fallback before parsing.

## Amendment (2026-11-17): roles, tokens, admin agents — see ADR 0027
Implemented: configurable roles, many named personal access tokens per user with
per-token tool lists, admin agents managing non-admin users and verifying.
