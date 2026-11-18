# 0029 — GUI redesign: true to the product, no GUI Run

Status: accepted · 2026-11-17 · amends ADR 0019, 0026, 0027

## Context
The user produced a visual redesign (`cteforge redesign.html`: Library, Definition, Import,
Proposal review, Join map, User menu, Log; light and dark). Parts of it showed features
cteforge does not have. The rule for building it: keep the look, stay true to the product,
add no buttons or features the product does not have. Running queries is an agent's job
(MCP); people schedule runs through the API or CLI.

## Decision

### Removed from the GUI
- **The Run tab and "Run it" button** on the definition page (and the run panel). The HTTP
  API `POST /run`, `POST /check` and the CLI keep running queries, e.g. for scheduled jobs
  with variables; agents run through MCP.
- Not built from the design: a "request to verify" proposal type, a review page, an impact
  preview, "ask for changes", generated "in plain words" summaries, a verified banner,
  "defaults" for variables, a join-map graph, proposing joins or definition changes from
  the join map, a "verified joins only" filter, ⌘K, a profile page.

### Layout
- Sidebar: **Library · Join map · Import · Log**, then **Settings** (one entry) holding
  Connections (the admin's default connection, ADR 0021), Team mode, Users & roles, and
  My tokens. Header: the default connection, and the user menu (name, email, role,
  Personal access tokens, Your tools, Theme, Sign out).
- Visual language from the design: warm neutral background, dark green primary, trust
  icons (verified ✓, LLM-annotated ◐, draft ◌), monospace for ids/SQL, light and dark.

### Library
- Search, filter chips (Trust, Approval, Created by, Verified by), sorted by relevance then
  trust. The "Waiting on you" panel lists proposals the user can decide (ADR 0026
  needs-my-review), **oldest first**, each with +/− changed-line counts; its link applies
  the Needs-my-review filter (oldest first). "Change pending" marks rows with a pending
  revision.

### Definition page
- Title, meaning, small "Verified by <user> on <date>" text (no banner), "Propose a change".
- Tabs: Overview · History · Edit.
- Overview: Use when / Don't use when; variables agents can fill with **Original value**
  (a variable left out uses the value in the trusted SQL; there are no defaults) and
  allowed values; side card: grain, outputs, joins (link to Join map), used by, keywords,
  owner (a user). Composed definitions: parts and compiled SQL. Trusted SQL collapsible.

### Proposal page
- Kind, status, title, proposer and **their note** (the only summary). Diff with +/− line
  counts, **unified or side-by-side**, changes-only or full. Used by. Decision panel:
  approve, "keep verified" (approve with verify, where allowed), reject with a note;
  withdraw for the author. Activity: real events only (proposed, decided).

### Join map
- List view only: many side / one side (relationship), keys, join-type **variants** with
  counts (which join type each definition uses, and where: CTE / main query / subquery),
  used by, trust of the source definitions, known / confirmed. Row detail: source
  definitions with their join type and location, relationship evidence, traps, notes;
  people may annotate (relationship, traps, notes) through a proposal. No adding joins and
  no definition changes from here (agents can still `propose_join` via MCP).

### Users
- Users have an optional **display name** and **email**, set by themselves (`/me`) or an
  admin. Shown in the user menu, Users & roles, token lists (owner), and the Log's actor.
  Visible to the user, admins (`list_users`), and as the actor name in the Log; never in
  search results or definition cards beyond the existing user names.

### Log (the audit log)
- Real call names (`search`, `get_definition`, `compile_and_run`, `check_and_run`,
  `propose` (op), `review` (op), `admin` (op)…), actor and kind, definitions, trust label,
  outcome, rows, duration, compile ID; a row expands to the values, question, token prefix
  (`cfg_pat_…`) and connection. Result rows are never stored (ADR 0008).

## Consequences
- The GUI shows only what cteforge does. Running from a browser is gone; scheduled and
  agent runs are unaffected.
- Small backend additions: user display name / email, per-source join type and location
  on join edges, +/− line counts on proposals.
