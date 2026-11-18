# 0029 — GUI redesign: true to the product, no GUI Run

Status: accepted · 2026-11-17 · implemented 2026-11-18 (see "as built" below) · amends ADR 0019, 0026, 0027

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

## Addendum (2026-11-18): as built
The decision above is unchanged; these are the choices made while building it.

**Look.** Colours, radii and type sizes come from the boards and live as tokens in
`web/src/styles/tokens.css` (light and dark; the dark values are the boards' dark
variants). Code panels (trusted SQL, diffs, Import's SQL) are dark in both themes, as on
the boards. Fonts: `Inter` and `JetBrains Mono` are named first and used when installed;
otherwise the system fonts. Nothing is downloaded or bundled (web/CLAUDE.md: no new
runtime dependency; a self-hosted server should not call out to a font CDN). Icons are
inline SVG drawn after the boards (`components/Icon.tsx`, `TrustIcon.tsx`).

**Layout.** Settings is one sidebar group with one route per section
(`/settings/connections | team | users | roles | tokens`; `/settings` opens the first one
the token may use). "Users & roles" holds the users table (role select, active, profile,
tokens); "Roles" the role editor. Each admin section is mounted only with its tool, as
before. The display-name/email form for yourself is the "Your profile" card in My tokens
(there is no profile page). The user menu's theme control has three settings — system,
light, dark — since following the system was already supported. `/audit` redirects to
`/log`; old `?tab=run` links open the Overview.

**Library.** `GET /library` gained `sort=relevance|oldest` (default relevance: words found
in the id or title weigh more than in the meaning or keywords, then trust, then title;
oldest: rows by their oldest proposal the caller may decide). `LibraryPage.waiting` lists
every pending proposal the caller may decide (list_proposals + review_proposal, four-eyes
included), oldest first, regardless of the search and filters, so the panel comes with the
page in one call. Pending proposal briefs carry `lines_added` / `lines_removed` (counted on
the query diff, see "Proposal page") and `needs_my_review`. Row metadata shows state, creator, verifier and owner; the
boards' grain / variable count / "updated" are not in `LibraryRow` and were not added.

**Definition page.** The state line reads "Verified by X (agent) on <date>" or
"LLM annotated: not verified by a person yet", with the version, id, dialect and profile.
Use when / Don't use when are shown as bullets (one per line of the text). The variables
table is Name · Type · Original value · Allowed; fixed variables are listed and tagged
"fixed". The owner is shown as stored (a user name); the display name is added only when
the owner is the signed-in user, because profiles are not part of definition cards. The
Verify button (llm_annotated, latest version, not one's own work) stays beside "Propose a
change". The definition card has no pending-proposal list, so "No changes pending" from the
board is not shown (the Library marks pending changes).

**Proposal page.** `ProposalView.title` (the proposed definition's title; null for joins)
is the page title; the note is `summary`. The page shows the **query** change, not the
stored YAML: `ProposalView.sql_diff` is the unified diff of the trusted SQL as written (the
template rendered with its original values, `_library.original_sql`, so both sides are
formatted alike; for a composed definition its parts list — alias, part, pinned version or
latest, fixed values — and final SELECT; "" when the query does not change, shown as
"The query does not change"; null for renames and joins), and `metadata_changes` lists the
described fields that change (title, meaning, keywords, use when, don't use when, grain,
join keys, owner, connection, dialect, variables; field → before / after; never version,
state, verifier or schema version). `diff` (the stored YAML) stays for the CLI and MCP.
`lines_added` / `lines_removed` count the query diff only (0 / 0 = "metadata only" in the
waiting panel; None for renames and joins). The page asks for
`GET /proposals/{id}?full_diff=true` (the whole query as context; MCP and `list_proposals`
keep 3 lines) and builds "changes only" (3 lines around each change, gaps collapsed),
"full", unified and side by side in `lib/diff.ts`. "Keep it verified after this change" is the approve-with-verify checkbox
when the target is verified ("Also verify the definition" otherwise); the target is
fetched for reviewers of a pending change to an existing definition. Activity lists the
proposal, auto-apply and the decision (by, when, note) only. Checks and Used by stay on
the page.

**Join map.** `JoinEdge.source_joins` (`JoinSource {definition, join_type, location:
main | cte | subquery, scope}`) is recomputed with `sources` by every mining; edges are
stored as JSON, so no schema change, and `join_map_version` 4 re-mines existing databases
once. The location is reliable: parsing's `JoinInfo.in_subquery` marks a join inside a
derived table or nested query below its scope; `scope` is the CTE (for a subquery, the
enclosing CTE or none). The join type is oriented like the stored edge. `JoinCard` adds
`source_states` and `source_titles` (latest version of each source). Many side / one side
come from the relationship (many_to_one: left is many; one_to_many: right); otherwise the
columns show the left and right tables marked "left" / "right". Variants count source
definitions per join type ("LEFT 3 · INNER 2"; a definition joining both ways counts in
both). Search, sort and "Variants first" are client-side. There is no "known only" filter
(the user ruled it out; the Known / Confirmed flags say it per row; the API keeps
`known_only`); a `?definition=` filter comes from the definition page. A pending
addition shows as a faded "Proposed" row linking to its proposal. No connection selector:
the join map is not per connection.

**Log.** Events from a token-authenticated caller carry `extra.token_id` and
`extra.token_prefix` (12 characters). Token administration events that name another token
now use `extra.subject_token_id` / `subject_prefix` (they used `token_id` / `prefix`).
`AuditPage.actors` maps each actor on the page to its display name and kind, and email only
for callers holding `list_users`. The call name is the kind plus `extra.op` when they
differ ("propose · revise_definition"). The summary (agents, people, errors, denied)
counts only the events returned; "Load more" raises the limit by 100 (up to 1000). Free
text search over compile IDs (the board's search box) is not an API filter, so the Log
keeps the actor / kind / definition filters.

**What the Log contains** (decided while building; ADR 0008 addendum). A person's
successful reads through the GUI, the HTTP API or the CLI are not logged; every MCP call
(reads included), every run, every change and every refused or failed call is. So the Log
shows what agents looked up and ran and who changed what, not every page a person opened.
The surface is set by the adapter (`_audit.surface`) and the read / run / change
classification lives in `services/_audit.py` (tested for completeness).

**Users.** Display name ≤ 80 characters without control characters; email ≤ 254, one `@`,
a dot in the domain, no spaces; bad input is a PolicyError (409) with a message, like other
admin input. `PUT /me/profile` needs no tool; another user's profile needs `manage_user`
(+ `can_manage_user`). CLI: `cteforge user profile NAME --display-name --email`.

**Removed.** The Run tab, RunPanel / RunResult / RunVariableField / ResultsTable /
VariableInput and `lib/variableForm.ts` (the grain list moved to `lib/importBuilder.ts`),
the GUI "Add a join" form (`JoinAddForm`, `addRequest`), the old JoinTable. `POST /run`,
`POST /check`, their API tests and the CLI are unchanged; the GUI contract test no longer
calls them.

