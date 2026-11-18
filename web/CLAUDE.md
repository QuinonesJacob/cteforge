# web/

React 18 + TypeScript (strict) + Vite GUI (ADR 0019). Talks only to the HTTP API
(`/api/v1`, see src/cteforge/api/CLAUDE.md). Builds to `web/dist` (base `/`), served by
`cteforge serve`. Project root: ../CLAUDE.md

## What belongs here (authoritative)
- `package.json`, `package-lock.json`, `vite.config.ts` (also holds the vitest config),
  `tsconfig*.json`, `index.html`, `.gitignore`.
- `src/main.tsx` (mount + providers), `src/App.tsx` (routes only).
- `src/api/` — typed API client: `types.ts` (mirror services/cards.py + domain models; keep
  field names identical), `client.ts` (fetch wrapper, `ApiError`), `token.ts` (sessionStorage),
  `endpoints.ts` (one function per route).
- `src/lib/` — pure, framework-free logic (no React, no fetch, no DOM) so it is unit-testable:
  import variable-building rules (type suggestion, ADR 0015 description rule, the grain
  list; `importBuilder.ts`), revise helpers (`revise.ts`: card → drafts/metadata, changed
  keys only), Library filters ⇄ URL ⇄ `GET /library` query, filter chips, the review-queue
  link (`library.ts`, ADR 0026, 0029), join-map annotate requests and list-view columns —
  many/one side, join-type variants, trust mix, location labels, search, sort (`joins.ts`),
  diff parsing and views — changes only / full, unified / side by side (`diff.ts`), Log
  rows — call name, outcome, summary, values, token prefix (`audit.ts`), profile form
  (`profile.ts`), described-field changes and query-change labels (`metadata.ts`), trust/state badge and trust-icon mapping (`trust.ts`), display-only SQL
  tokenizer and Import's value marking (`sqlTokens.ts`), formatting helpers (ages,
  initials, bullets; `format.ts`), permission checks and Settings sections (`roles.ts`, on
  the token's tools), the tool catalog and token/role form rules (`tools.ts`, ADR 0027),
  rename-form rules (`rename.ts`, ADR 0024).
- `src/state/` — React context providers and hooks (auth, toasts, theme, async loading).
- `src/pages/` — one file per route: SignIn, Library (`/`: definitions + the proposals
  touching them, ADR 0026), Definition (Overview, History, Edit — no Run, ADR 0029), Import
  (also revise mode: `/import?revise=<id>`), Join map (`/joins`, list view), Proposal
  (`/review/:id`), Log (`/log`; the audit log), Settings (`/settings/:section`), NotFound.
  `/review` redirects to `/?review=mine`, `/explore` to `/`, `/audit` to `/log`.
- `src/components/` — shared UI pieces, one component per file: the shell (`AppLayout`,
  `SideNav`, `AppHeader`, `UserMenu`, `ThemeToggle`), `Icon` (inline SVG, ADR 0029),
  `TrustIcon`, SQL viewer, diff viewer, issue list, literal table, …
- `src/styles/` — plain CSS: `tokens.css` (light/dark custom properties), `base.css`,
  `components.css`, `pages.css`.
- `src/test/` — vitest setup and test helpers (`fetchMock.ts`). Tests sit next to the code
  as `*.test.ts(x)`.
- `playwright.config.ts` and `e2e/` — real-browser end-to-end tests (Playwright on the
  installed Google Chrome): `global-setup.ts` (build, `cteforge demo`, `cteforge serve`,
  second approver; returns the teardown), `helpers.ts` (fixtures, sign-in, API calls,
  screenshots), `NN-<area>.spec.ts` (run in file order; they share one server's state).
  `e2e/screenshots/`, `test-results/` are output (gitignored).

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Rules
- No SQL logic in the browser: the GUI shows what the API returns and sends values. The SQL
  tokenizer in `src/lib/sqlTokens.ts` is for colouring text only — never for deciding anything.
- Token kept in sessionStorage; never logged. Dev server proxies /api and /mcp to localhost:8765.
- Every API error is an `ApiError` mirroring `{"error": {type, message, issues}}`; show its
  issues with `IssueList`, its message in a toast.
- The GUI does not run queries (ADR 0029): no calls to `POST /run` or `POST /check`. Agents
  run through MCP; scheduled jobs use the API or CLI.
- Look (ADR 0029): colours, radii and type only through the tokens in `tokens.css`
  (warm neutral background, dark green primary; code panels dark in both themes). Fonts:
  `Inter` / `JetBrains Mono` when installed, else system fonts — never downloaded or
  bundled.
- Theme: tokens on `:root`; dark via `prefers-color-scheme` unless `html[data-theme]` is set by
  the toggle. Accessible: every control labelled, visible focus ring, keyboard reachable.
  Layout works down to 375px.
- No component libraries. Keep files small: one component per file.
- Dependencies: react, react-dom, react-router-dom, vite, typescript, @vitejs/plugin-react,
  vitest. Test-only: jsdom, @testing-library/react, @testing-library/jest-dom,
  @testing-library/user-event (DOM tests), @types/* (typings), @playwright/test (e2e/ only:
  drives the real GUI in Chrome against a real `cteforge serve`; browsers are never
  downloaded, `channel: "chrome"` uses the installed Google Chrome). Exact versions pinned.
  Nothing else without a reason in this file.

## Contract notes (from services/cards.py)
- Library (ADR 0026): `GET /library?q=&trust=&approval=&created_by=&verified_by=
  &needs_my_review=` (list params repeat) → `LibraryPage {rows, total, pending_count,
  proposals_visible}`; filtering is server-side. `LibraryRow.version` null = a
  new-definition proposal never applied ("Pending approval", links to `/review/:id`);
  `pending` (ProposalBrief) become "Revision pending" / "Rename pending" badges linking to
  the proposal; `composed` rows get a "composed" tag. Filters live in the URL
  (`lib/library.ts`: `trust`, `approval` — absent = pending + approved, rejected and
  withdrawn hidden —, `created`, `verified`, `review=mine`); approval filters only with
  list_proposals, "Needs my review" only with review_proposal. The pending count badge sits
  on the Library nav item. The Playwright setup runs the server with
  `CTEFORGE_SEARCH_EMBEDDINGS=off`. ADR 0029: `sort=oldest` (URL `sort=oldest`) orders rows
  by their oldest proposal the caller may decide; `LibraryPage.waiting` (WaitingProposal:
  brief + `target_id`, `title`) feeds "Waiting on you", oldest first, whatever the filters;
  its link is `/?review=mine&sort=oldest` (`REVIEW_QUEUE_HREF`). Pending briefs carry
  `lines_added` / `lines_removed` / `needs_my_review`. Filters sit in a popover; active ones
  are removable chips (`filterPills`) with "Clear all".
- Composed definitions (ADR 0026): `DefinitionCard.composed`, `composition`
  (`CompositionCard`: parts alias → id, `pinned_version` or latest, `superseded`, `usable`;
  `fixed`; `open_variables`; `select`; `compiled_sql`), `used_by` (UsedByReference) and
  `joins` ("joins to": JoinCard). `CompositionView` shows them on the Definition overview and
  on a proposal (`ProposalView.composition`). A composed definition's `variables` are its
  open ones named `<alias>.<var>`. Composed definitions are not created or re-composed in the GUI (agents
  do that through MCP); the Edit tab offers metadata only, and Import's revise mode refuses
  them. `ProposalView.impact` = ids of composed definitions using the target ("Used by").
- Join map (ADR 0026, 0029 list view): `GET /joins?definition=` → JoinCard (the GUI offers no
  "known only" filter: the Known / Confirmed flags say it per row; `known_only` stays an API
  parameter): edge + `known`, `confirmed`, `pending_proposals`; `sources`, `trusted_sources` — the
  sources a person stands behind, the only ones that make a mined edge known —,
  `relationship`, `traps`, `notes`, `evidence`; ADR 0029: `source_joins` — JoinSource
  `{definition, join_type, location: main|cte|subquery, scope}` —, `source_states`,
  `source_titles`). Columns: many side / one side from the relationship (else left / right),
  keys, join-type variants per source definition, used by, a trust bar of the sources'
  states, flags (variants, known, confirmed, change pending). A row opens its sources (join
  type + location), evidence, traps, notes and, with propose_join, the annotate form:
  `POST /proposals/joins {action: "annotate", id, relationship?, traps?, notes?, summary}`
  (only changed fields). The GUI adds no joins (agents may `propose_join` add via MCP).
  Pending additions are proposals, not edges: faded "Proposed" rows from
  `GET /proposals?status=pending` (callers with list_proposals). Join proposals show
  `payload.action` and `payload.evidence`; they are approved, never verified.
- `DefinitionCard.history` may be null; `GET /audit` returns `AuditPage {events, limit,
  has_more, actors}` (ADR 0029: `actors` = display name / kind of each actor on the page,
  email only for list_users holders; events of token callers carry `extra.token_prefix`;
  the Log shows kind + `extra.op` as the call name and loads more by raising `limit`);
  approve returns `ProposalOutcome`, reject/withdraw return `ProposalView` (the GUI reloads after
  any decision). `POST /users` → 201 `{user, token | null}` is normalized in `endpoints.ts`
  (`toCreatedUser`; token null when the caller is an agent).
- The GUI calls neither `/run` nor `/check` (ADR 0029) nor the search route
  (`GET /definitions?q=`); the Library uses `GET /library`.
- Definition page (ADR 0029): no banner; the state line (verified by / when, or not
  verified), "Propose a change" (→ Edit tab). Overview: Use when / Don't use when as
  bullets, "Variables agents can fill" (Name · Type · Original value · Allowed; fixed ones
  tagged), the trusted SQL in a collapsed `<details>` (or a composed definition's parts and
  compiled SQL), a side card (grain, outputs, joins → `/joins?definition=<id>`, used by,
  keywords, owner).
- Proposal page (ADR 0029): `GET /proposals/{id}?full_diff=true`; `ProposalView.title` is the
  page title, `summary` the proposer's note. It shows the QUERY change, never the stored
  YAML: `sql_diff` (the source SQL as written, both sides formatted alike; a composed
  definition's parts list and final SELECT; "" = the query does not change → "metadata
  only"; null for renames / joins) in the diff viewer, which toggles unified / side by side
  and changes only / full (`lib/diff.ts`), and `metadata_changes` (MetadataChange `{field,
  before, after}`; lists as added / removed, `lib/metadata.ts`) in "Description changes".
  `ProposalView.diff` (YAML) is for the CLI / MCP only. Pending briefs' `lines_added` /
  `lines_removed` count the query diff (0/0 → "metadata only" in Waiting on you; null for
  renames and joins); "Your decision" (approve; "Keep it verified after
  this change" / "Also verify" = approve with verify; reject with a note; withdraw for the
  author); Activity: proposed, auto-applied, decided only.
- Users (ADR 0029): `/me` and `UserView` carry `display_name`, `email`; `PUT /me/profile
  {display_name?, email?}` (Settings → My tokens → Your profile) and `PATCH /users/{name}`
  with the same fields (Users & roles → Profile, manage_user); only changed fields are
  sent, "" clears (`lib/profile.ts`); the server validates. Shown in the user menu, Users &
  roles, token lists (owner) and the Log's actor; never on definitions or in search.
- Revise: `GET /definitions/{id}?include_source=true` adds `source_sql` (template rendered
  with its original values) and `annotations` (literal IDs addressing that SQL). The Edit
  tab sends `POST /proposals/definitions/{id}/revise {base_version, metadata: <changed keys
  only>, summary}`; revise mode in Import sends `{base_version, sql, dialect, metadata
  (changed keys, + profile if changed), annotations, summary}`.
- IDs (ADR 0024): on Import the ID follows the title and is read-only until "Edit ID"
  (`MetadataForm`; "Use title" locks it again); revise mode and the Edit tab keep it
  read-only. Renames: the Edit tab's "Rename ID" (`RenameIdPanel`) loads
  `GET /definitions/{id}/references` (`DefinitionReferences`: used_by, pending proposals,
  former_ids, outside_warning; shown by `ReferencesList`), and enables submit only when the
  new ID is valid, different and typed again exactly (`lib/rename.ts` `checkRename`); it
  sends `POST /proposals/definitions/{id}/rename {new_id, base_version, summary}`. Applied
  at once → toast + navigate to the new ID; else "Rename proposal submitted". The proposal
  page shows `rename_definition` proposals as from → to plus `payload.references` (no diff,
  no "also verify"). `DefinitionCard.former_ids` shows as "formerly …" in the header.
- Permissions (ADR 0027): the unit is the tool. `GET /me` returns the signed-in token's
  effective `tools`, `review_all`, and its role's `role_allowed` / `role_defaults`;
  `lib/roles.ts` decides what to offer from them (`can(user, tool)`; a user object without
  `tools` falls back to the built-in role's, `lib/tools.ts` `BUILTIN_ROLE_TOOLS`, mirroring
  domain/access.py; agents never verify outside the admin role). Sidebar (ADR 0029):
  Library · Join map (list_joins) · Import (parse_query + propose_definition) · Log
  (view_audit), then Settings: Connections and Team mode (manage_settings), Users & roles
  and Roles (list_users), My tokens (everyone) — `lib/roles.ts` `settingsSections`; the
  Library's pending badge needs list_proposals. Header: the default connection (GET
  /profiles `default`) and the user menu. Edit tab / revise
  mode: revise_definition (+ parse_query); join forms: propose_join. Approve/reject: review_proposal with review_all, or the
  target definition's owner holding review_proposal; changes to verified work: a person or
  an admin agent. Verify: the verify tool. Never a role-name check except the admin-role
  rules for agents and the four-eyes "human admin" exemption.
- Settings (ADR 0027): "My tokens" for everyone (`MyTokensCard`: `GET /me/tokens`,
  `POST /me/tokens {name, expires_at, tool_mode, include, exclude}` → 201 `{token, info}`
  shown once by `TokenReveal` with copy, `DELETE /me/tokens/{id}`; the token this tab signed
  in with is tagged "this session" by matching its 12-char prefix; agents get no generate
  button). `TokenForm`: name, optional expiry date → `YYYY-MM-DDT23:59:59Z` (works through
  the end of that day, UTC; `lib/tools.ts` `expiryFromDate`), mode follow_role | only, a
  `ToolChecklist` limited to the role's Allowed and starting at its defaults
  (`tokenToolsRequest`: follow_role sends include = checked − defaults, exclude = defaults
  − checked; only sends include = checked). Signed in with a token (`/me` `token_id`,
  `token_expires_at`, `token_mode`), My tokens offers only tools that token holds
  (`ceiling` = the role's Allowed, so unoffered defaults are excluded), caps the expiry at
  the token's own (`expiryWithin`; the date input's `max`), disables "follow the role" for
  an `only` session, and says the new token is tied to the session's (the server links it
  as `parent_token_id` and revokes it with its parent; `TokenList` shows "created by token
  <prefix>"). Generating for another user (Users) shows that `manage_user` is equivalent to
  full admin access. Pickers use `GET /tools` (`ToolInfo {name,
  group, admin_only}`) grouped use/build/review/admin. Admin sections are mounted only when
  the token holds their tool, so others never call those routes: team mode and default
  connection with manage_settings; Users (`GET /users` → UserView with tokens' metadata —
  never values) and Roles (`GET /roles` → RoleCard) with list_users. `UsersTable`: role
  select from /roles and active checkbox (`PATCH /users/{name} {role} | {active}`, with
  manage_user), Tools column = the role's Allowed per group (`toolSummary`), each user's
  tokens (name, prefix or "legacy", tools, last used, expiry, state, revoke via `DELETE
  /users/{name}/tokens/{id}`), "Generate token for X" (`POST /users/{name}/tokens`; a human
  with manage_user only). `CreateUserForm` takes the roles and shows the returned "default"
  token once. `RolesCard` / `RoleEditor` (manage_role to edit; the admin role only by a
  human): Allowed and On-by-default checklists; the admin role's Allowed is locked (every
  tool) and its review_all fixed (the editor sends neither); admin-only tools are disabled
  outside the admin role; defaults only among allowed; `POST /roles`, `PUT /roles/{name}`,
  `DELETE /roles/{name}` → 204 (custom roles nobody holds). `DefinitionCard.verified_by_kind`
  / `ProposalView.decided_by_kind` show "(agent)" after the name.
- Default connection (ADR 0021): Settings (manage_settings) has a "Default connection" select (`None` + GET /profiles `name
  (dialect)`) that sends `PUT /settings {default_connection: name | null}` and shows
  TeamSettings `issues` (e.g. `default_connection_missing`). `PUT /settings {mode}` for
  the team mode. Import starts on the effective default connection (`default: true` from
  GET /profiles, `lib/importBuilder.ts` `importConnection`); the dialect follows the
  selected profile (`defaultImportDialect`) until the person picks one; the hint
  (`connectionDialectHint`) says "From connection X." or that a different dialect is
  checked translated, lower trust. Revise mode keeps the card's source_dialect and profile.
- The GUI's request bodies and the fields it reads are pinned by
  `tests/e2e/test_gui_contract.py` against the real API: change both together.
- Parse report: `literals[].id` is `<scope>.L<n>` and `grains[].id` is `<scope>.G<n>`; both are
  positional. A grain annotation's `literals` holds grain IDs only. The import page builds the
  proposal from the exact SQL/dialect/profile that was parsed, and drops drafts whose IDs no
  longer name the same literal after a re-parse.
- Approve/reject is offered to reviewers of everything (review_proposal + review_all) and,
  for definition proposals, to the target definition's owner (holding review_proposal);
  verify to holders of the verify tool (agents: admin role only).
- The pending badge shares one poller per tab (`state/usePendingCount.ts`) on
  `GET /proposals/count` (no diffs, not audited); still poll rarely and only while visible.
  GET /proposals builds diffs: only the Join map page (pending additions) calls it.
- Audit (ADR 0008 addendum, ADR 0029): the GUI's successful reads are not logged (the API
  surface); runs, changes and refused calls are. The Log therefore shows agent (MCP)
  calls, runs and changes.
- Verify is offered only on the latest version of an `llm_annotated` definition; approval is
  disabled while checks have errors; "also verify" is disabled on `checks_incomplete`.
- Four-eyes (ADR 0018 addendum, ADR 0027: no exemption for agents; `lib/roles.ts` `isOwnWork`, `versionAuthors` mirror
  trust/approval.py and services/review.py): the author of a proposal is not offered
  Approve (only Reject/Withdraw), and Verify is hidden from anyone who wrote any part of
  the version, unless they are a human admin. The server still enforces it (403).

## Commands (run in web/)
- `npm install` — install
- `npm run dev` — Vite dev server (proxies to `cteforge serve` on :8765)
- `npm run typecheck` — `tsc -b`
- `npm test` — vitest (jsdom)
- `npm run build` — typecheck + build to `web/dist`
- `npm run e2e` — Playwright end-to-end suite in the installed Google Chrome (needs `uv`
  and Chrome at /Applications/Google Chrome.app or the platform default; never run
  `playwright install`). globalSetup runs `npm run build`, `cteforge demo <tmp>` (captures
  the printed admin/agent tokens), starts `cteforge serve --port <free>` with the demo's
  env.sh values and `CTEFORGE_AUTH=on`, waits for `GET /api/v1/me` → 401, and creates the
  human approver `ben`; teardown stops the server (SIGINT) and deletes the temp dir
  (`E2E_KEEP_TMP=1` keeps it, with `serve.log`). Specs: sign-in, sidebar and user menu,
  library search, filters (popover, chips, needs my review, created by, rejected hidden by
  default, `/review` redirect) and "Waiting on you" (oldest first, +/− lines, the queue
  sorted oldest first), the definition overview (no Run tab; variables with original
  values; trusted SQL; facts; "Propose a change"), import → auto-apply → overview, admin
  sets the default connection → Import starts on it, strict mode + Library "needs my
  review" + a description-only change ("the query does not change", field changes) + a
  query change (the SQL diff unified / side by side, changes only / full) + four-eyes,
  the Log (agent runs through the API; call names, trust, outcome; a row's token prefix
  and connection; filters), theme (user menu) and 375px layout, Edit-tab and SQL
  revisions, the demo's composed definition (parts, variables, compiled SQL, used by),
  rename ID (references → typed confirmation → applied → old ID's "renamed to" error; the
  composed definition using it still runs over the API; a pending rename on its proposal
  page), access (ADR 0027: custom role, narrow token shown once, sign in with it, revoke;
  an agent's token from Users & roles; ADR 0029: display name and email set by yourself and
  by an admin, shown in the menu, users, token owner and the Log), join map (list columns,
  variants, sources and locations; a person annotates a mined edge — evidence; an agent's
  added edge waits as a "Proposed" row, a person approves it → known + confirmed; a
  read-only token sees no forms), and `12-screenshots` (every page, light and dark, at
  1440px: `e2e/screenshots/12-<page>-<light|dark>.png`). Screenshots land in
  `e2e/screenshots/`. One spec: `npx playwright test 04`; debug: `--headed` or
  `npx playwright show-trace test-results/<test>/trace.zip` (kept on failure).

## Common misplacements
- Any SQL logic — the backend owns it; the GUI calls the API.
- Value validation beyond "is the form filled in" — domain/variables.py owns it; show the
  API's issues instead of re-implementing rules.
- Logic inside components that could be a pure function — put it in `src/lib/` and test it.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
