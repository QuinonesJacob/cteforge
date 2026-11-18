# 0026 — One library: review in the Library, composed definitions replace recipes, joins from definitions

Status: accepted · 2026-11-17 · implemented 2026-11-18 (see "as built" below) · amends ADR
0019 (GUI pages), ADR 0024 (references), ADR 0027 (tool catalog), DESIGN §3, §6, §8, §15, §17,
§18

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

## Addendum (2026-11-18): as built
Choices made while building it (the decision above is unchanged):

**Model and codec.** `Definition.composition` (`Composition{uses: alias → DefinitionRef,
fixed: alias → var → value}`) marks a composed definition; its final SELECT is the
definition's `template` (so its own literals are annotated exactly like SQL definitions),
and an empty template means exactly one part used as is (what a recipe without a SELECT
was). The parts' open variables are not copied into `variables`: they are computed from
the parts as they are now (`<alias>.<var>`, nested `<alias>.<sub>.<var>`), so a floating
part's new variables appear without a new version. Definition `schema_version` is 2;
version-1 files load unchanged. A variable may not be named like a part alias.
`codec.definition_from_recipe` turns old recipe data (files and stored rows) into a
composed definition (values → `fixed`, description → meaning).

**Values.** `compile_and_run` takes a composed definition's values as
`values[alias]["<part alias>.<var>"]` or `values[alias]["<part alias>"] = {var: value}`.
Giving a value for a variable the composition fixes is an override: `fixed_override`
warning, one step down (like overriding a fixed variable). A composed definition's
`values_used` and audit values use the dotted names.

**Trust (trust/labels.py `composed_states`).** A composed definition nobody verified caps
the label at `built_from_verified_parts` and its final SELECT's joins are judged like an
agent's (unknown → exploratory; raw tables → new logic). A VERIFIED composed definition's
final SELECT and choice of parts are trusted as reviewed (its own alias joins are not
re-judged); its parts' states still cap the label. A floating part with no usable version
in the team mode → `part_not_usable` (error at run time: the run is refused; a warning in
proposal checks: approvable, never verifiable). A pinned part with newer versions →
`part_superseded` (info). Cycles (also through nesting) → `composition_cycle`, refused at
proposal time, again when applying (two pending proposals could close one) and at run
time; nesting deeper than 10 levels is refused.

**Checks of a composition** (`services/_compose.checked_composition`): the select must be
one read-only SELECT (PolicyError `not_read_only`, first); parts resolve (`not_found`);
`fixed` names open variables of its part (`composition_invalid`); no cycle; the whole
compiles (render + compose; compose's issues kept) and passes EXPLAIN on the profile's
warehouse. ADR 0027's raw-table gate holds throughout: a final SELECT that reads raw tables
(or table functions) is exploratory SQL and needs `check_and_run` — when a composed
definition using one is run (verified or not, nested too), when it is proposed or
re-checked (the checks run it; refused before the warehouse), and in revalidation
(without the tool it is reported as not checked). With annotations on the final select, the compiled query with the plain select
and with the template are run and compared. The final SELECT reads its parts, not
warehouse tables, so a variable's type is not checked against a column type
(`type_unknown` at INFO severity; the results comparison covers the original values). The
definition's `tables` are its parts' tables (+ raw tables of the select); `outputs` the
select's.

**Joins.** `JoinEdge` lost `state`; it gained `notes`, `sources` (definition ids it was
mined from; recomputed with `usage_count` after every apply, composed definitions mined
from their compiled SQL), `added_by` / `added_by_kind`, `confirmed_by` (a PERSON who
approved a proposal on it; auto-apply and admin agents never confirm) and `evidence` (the
last relationship check). Known (counts for trust) = mined, added by a person, or
confirmed by a person. `list_joins` returns `JoinCard` (edge + `known`, `confirmed` — a
person approved it or a source definition is verified — and `pending_proposals` for
callers holding list_proposals) and filters `table`, `known_only`, `definition`. A
confirmed edge is treated like a verified definition: changes to it wait for a person or
an admin agent. Join proposals are approved, never verified. The relationship check runs
`compose.relationship.relationship_query` on each side (rows with every key set vs
distinct key tuples; identifiers via sqlglot, no values); a side claimed "one" with
duplicate keys is a `relationship_contradicted` ERROR (data may be stricter than a claim,
never looser); a failed count is `checks_incomplete` with the error class only. The
default `action` is `annotate`; `annotate` never changes the join type (only a library
import does, from a file).

**Store migration (schema 3)** in `Store.init_schema`, one transaction: each recipe's
latest version → a composed definition v1 (same id when free and not a former id, else
`<id>_recipe`, `_recipe_2`, …), llm_annotated, or verified with `verified_by` = the
approver of the proposal that produced that verified recipe version (no such record →
llm_annotated), authored by that proposal's author (else `cteforge`); pending `new_recipe`
proposals → withdrawn by `cteforge` with a comment; `list_recipes` / `propose_recipe`
stripped from every role's allowed/defaults and every token's include/exclude; join edges
lose `state`, and `added_by` / `confirmed_by` come from their approved join proposals. The
recipe tables are kept (read only by the migration). The store cannot write the audit
log (another adapter), so it leaves a note in `settings`; `services/_upgrade.py` writes
it as one `admin` event (`op=migrate_adr_0026`, actor `cteforge`) when a context opens,
fail closed, then clears it, and re-mines the join map once per database. ProposalKind
`new_recipe` and IssueCode `recipe_invalid` stay as values so stored history loads;
nothing creates them.

**Library page and API.** `GET /api/v1/library` (`services/library.list_library`, tool
`search`; proposal rows, badges and approval statuses only for callers who also hold
`list_proposals`; `needs_my_review` needs `review_proposal` and uses the four-eyes rule).
A definition in the library always matches the "approved" filter (its current version
was approved) as well as its latest proposal's status, so a rejected revision never hides
it; rejected/withdrawn new-definition proposals are hidden by default. "Created by" is
the author of version 1 (kind from the users table) or of the proposal.

**Other.** `DefinitionCard` gained `composed`, `composition` (parts with what they
resolve to now, fixed and open variables, the full compiled SQL), `used_by` and `joins`
("joins to": edges touching its tables). `DefinitionReferences.recipes` became `used_by`.
`check_and_run`'s `matches_definition` hint skips composed definitions. The demo
(`cteforge demo`) adds one composed definition proposed by the agent. MCP has 15 tools
(ADR 0027's review and admin tools included).
