# 0026 — One library: review in the Library, composed definitions replace recipes, joins from definitions

Status: accepted · 2026-11-17 · not yet implemented · amends ADR 0019 (GUI pages), DESIGN §3, §6, §17, §18

## Context
- The GUI splits the library across Library, Review and Explore (Recipes, Compose, Join
  map, Tables, SQL). Reviewers and analysts both start from "what is in the library and
  what state is it in", so the split costs a page hop for every decision.
- GUI Compose asks a person to pick definitions and write a final SELECT without showing
  the definitions' SQL. Nobody would use it. The MCP path (`get_definition` returns the
  template + variables; `compile_and_run` takes several aliases + a final SELECT and, with
  `run=false`, returns the full compiled SQL) is the useful one, and it already exists.
- Recipe vs definition is not a property people can reliably pick: in testing, full
  queries were imported as definitions and were really "recipes". Any definition is a full
  SELECT, and any definition can be a CTE in a bigger query. What actually differs is how
  it was made: written as SQL, or assembled from other definitions.
- Join edges are already mined from every applied definition's SQL
  (`_library.refresh_join_map`). What mining cannot see is the relationship
  (one-to-many …), known traps, and joins no trusted SQL uses yet.

## Decision

### Library and Review are one page
- The Library lists definitions and the proposals touching them. A pending new definition
  is a row marked "Pending approval"; a pending revision or rename is a badge on the
  existing row linking to its diff.
- Filters, three independent groups:
  - **Trust**: verified · llm_annotated · draft (the definition's state).
  - **Approval**: pending · approved · rejected · withdrawn (latest proposal touching it).
    Rejected and withdrawn are **hidden by default**.
  - **Created by**: person · agent (who proposed it). Also "Verified by: person · agent"
    (ADR 0027 lets admin agents verify).
- A "Needs my review" quick filter and the pending count on the Library nav item replace
  the Review page. Proposal detail (`/review/:id`) stays as a page linked from the row.

### Composed definitions replace recipes
- Everything in the library is a definition. A definition's body is either
  - **SQL** (imported trusted SQL, as today), or
  - **a composition**: `uses` (alias → `{id, version?}`), a final `select` over the
    aliases, and `fixed` (alias → variable → value: variables this definition always sets).
- Parts are **live references**: a part without `version` floats to its latest usable
  version, so a fix to a part reaches everything built on it; a `version` pins it.
  Reviews of a part list the composed definitions it changes ("used by"), the way the
  rename review lists references (ADR 0024).
- A composed definition's variables are its parts' open variables, named `<alias>.<var>`
  (as `compile_and_run` exposes them today). Its final select's own literals can be
  annotated like any SQL.
- Trust: a composed definition is labeled `built_from_verified_parts` (or lower, per §8)
  until a person verifies the whole; verifying it verifies the final select and the
  choice of parts, not the parts themselves. A pinned part that is later superseded is
  shown as such; a floating part that moves to a non-usable state makes the composed
  definition unusable with an issue naming the part.
- Composition graph: cycles are refused; nesting composed definitions is allowed.
- `propose_definition` takes **either** `sql` **or** `composition` (never both); with a
  composition no `annotations` are needed for the parts. Checks: every part resolves, the
  final select is one read-only SELECT over the aliases, the whole compiles and EXPLAINs
  on the warehouse; the reviewer sees the full compiled SQL, the parts and which
  variables are fixed or open.
- Removed: `propose_recipe`, `list_recipes`, `compile_and_run(recipe_id=…)`, the Recipe
  model, proposal kind `new_recipe`. Existing recipes are migrated to composed definitions
  (same id when free) by a one-time store migration.

### Joins come from definitions; agents can annotate and add
- The join map stays mined from every applied definition (including composed ones: their
  compiled SQL is CTEs + a final select). Mined edges are **known** automatically.
- `list_joins` (read-only) returns per edge: tables, keys, the definitions it came from,
  `relationship`, `traps`, notes, and whether a person confirmed it.
- `propose_join` (write) takes `action`:
  - `annotate` — relationship (`one_to_one` · `one_to_many` · `many_to_one` ·
    `many_to_many`), traps, notes on an existing edge. cteforge checks a relationship
    claim against the warehouse (`COUNT(*)` vs `COUNT(DISTINCT key)` on each side) and
    stores the evidence with the proposal.
  - `add` — an edge no definition contains. An agent-added edge **never auto-applies**
    (even in open mode) and does not count as known until a person approves it, so an
    agent cannot raise trust labels by declaring joins.
- People see and edit the same details on the Join map page, also through proposals.
- Reading and writing stay separate tools so per-token tool access (ADR 0027) can allow
  reading the join map while denying changes.

### GUI navigation
**Library · Join map · Import · Audit · Settings.** The Explore page is removed: Recipes
(→ composed definitions in the Library), Compose and SQL (agents keep `compile_and_run` /
`check_and_run` through MCP; people see what agents ran, with full SQL, in Audit), Tables
(agents keep `describe_tables`). A definition page shows "joins to …" and, for a
composed definition, its parts and compiled SQL.

### MCP tools (10)
Use: `search` · `get_definition` · `compile_and_run` · `check_and_run` ·
`describe_tables` · `list_joins`. Build: `parse_query` · `propose_definition` (SQL or
composition) · `revise_definition` · `propose_join` (add | annotate). Review and admin
tools are added by ADR 0027 and are visible only to tokens allowed to use them.

## Consequences
- One concept for agents (definition) and one page for people (Library).
- Fixing a definition fixes everything composed from it; the review shows that blast radius.
- A store migration (recipes → composed definitions) and a codec change (definition body:
  sql | composition) with a `schema_version` bump.
- Join-map trust can no longer be raised by an agent alone.
