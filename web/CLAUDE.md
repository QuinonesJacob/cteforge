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
  variable form values ↔ JSON, import variable-building rules (type suggestion, ADR 0015
  description rule), revise helpers (`revise.ts`: card → drafts/metadata, changed keys
  only), composition/recipe run requests (`variableForm.ts`), diff line classification,
  trust/state badge mapping, display-only SQL tokenizer, formatting helpers, permission
  checks (`roles.ts`, on the token's tools), the tool catalog and token/role form rules
  (`tools.ts`, ADR 0027), rename-form rules (`rename.ts`, ADR 0024).
- `src/state/` — React context providers and hooks (auth, toasts, theme, async loading).
- `src/pages/` — one file per route: SignIn, Library, Definition (Overview, Run, History,
  Edit), Import (also revise mode: `/import?revise=<id>`), Explore (Recipes, Compose, Join
  map, Tables, SQL), Review (list + detail), Audit, Settings, NotFound.
- `src/components/` — shared UI pieces, one component per file (SQL viewer, variable form,
  issue list, trust badge, diff viewer, literal table, …).
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
- Values sent to /run follow the variable input formats exactly (DESIGN §4):
  date_range → `{start, end}` ISO; date → ISO; text_list → array; number → JSON number;
  boolean → true/false; grain → day|week|month|quarter|year. Built only by `lib/variableForm.ts`.
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
- Search (ADR 0023): `SearchHit.match` is keyword | meaning | both (null for an empty
  query); `SearchHitCard` shows a "matched by meaning" badge for meaning-only hits; the
  Library shows a hit's `note` (meaning search unavailable → keyword-only results) once.
  The Playwright setup runs the server with `CTEFORGE_SEARCH_EMBEDDINGS=off` (tests never
  download the model).
- `DefinitionCard.history` may be null; `GET /audit` returns `AuditPage {events, limit, has_more}`;
  approve returns `ProposalOutcome`, reject/withdraw return `ProposalView` (the GUI reloads after
  any decision). `POST /users` → 201 `{user, token | null}` is normalized in `endpoints.ts`
  (`toCreatedUser`; token null when the caller is an agent).
- `/run` body: `uses: {<id>: {id, version}}`, `values: {<id>: {...}}`, `run`, `limit`.
- `/run` also takes `profile` and `question`; `GET /definitions` takes `limit` (the GUI sends
  100; the API default is 10). `RunOutcome.used` lists each definition version with its
  state and verifier + date; the run result shows it.
- Revise: `GET /definitions/{id}?include_source=true` adds `source_sql` (template rendered
  with its original values) and `annotations` (literal IDs addressing that SQL). The Edit
  tab sends `POST /proposals/definitions/{id}/revise {base_version, metadata: <changed keys
  only>, summary}`; revise mode in Import sends `{base_version, sql, dialect, metadata
  (changed keys, + profile if changed), annotations, summary}`.
- IDs (ADR 0024): on Import the ID follows the title and is read-only until "Edit ID"
  (`MetadataForm`; "Use title" locks it again); revise mode and the Edit tab keep it
  read-only. Renames: the Edit tab's "Rename ID" (`RenameIdPanel`) loads
  `GET /definitions/{id}/references` (`DefinitionReferences`: recipes, pending proposals,
  former_ids, outside_warning; shown by `ReferencesList`), and enables submit only when the
  new ID is valid, different and typed again exactly (`lib/rename.ts` `checkRename`); it
  sends `POST /proposals/definitions/{id}/rename {new_id, base_version, summary}`. Applied
  at once → toast + navigate to the new ID; else "Rename proposal submitted". The Review
  page shows `rename_definition` proposals as from → to plus `payload.references` (no diff,
  no "also verify"). `DefinitionCard.former_ids` shows as "formerly …" in the header.
- Permissions (ADR 0027): the unit is the tool. `GET /me` returns the signed-in token's
  effective `tools`, `review_all`, and its role's `role_allowed` / `role_defaults`;
  `lib/roles.ts` decides what to offer from them (`can(user, tool)`; a user object without
  `tools` falls back to the built-in role's, `lib/tools.ts` `BUILTIN_ROLE_TOOLS`, mirroring
  domain/access.py; agents never verify outside the admin role). Nav: Import needs
  parse_query + propose_definition, Review list_proposals (also the pending badge), Audit
  view_audit, Settings everyone. Edit tab / revise mode: revise_definition (+ parse_query);
  save recipe: propose_recipe. Approve/reject: review_proposal with review_all, or the
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
- Explore: `GET /recipes?q=&include_drafts=`, `GET /joins?table=&include_drafts=`,
  `GET /tables?name=&name=&profile=` (array query values repeat the key), `POST /check
  {sql, profile?, run, limit}`, `POST /run {recipe_id, values: {}, run, limit, profile?}`,
  compose `POST /run {uses: {<alias>: {id, version?}}, values, select?, ...}`,
  `POST /proposals/recipes {recipe: {id, title, description, uses, values, select?, dialect},
  summary}`. Recipe/join proposals carry `payload.recipe` / `payload.join`.
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
  GET /proposals builds diffs and writes an audit event per call: the Review page only.
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
  (`E2E_KEEP_TMP=1` keeps it, with `serve.log`). Specs: sign-in, library search, run form
  (+ validation), import → auto-apply → run, admin sets the default connection → Import starts on it, strict mode + review + four-eyes, audit
  filters, theme toggle and 375px layout, Edit-tab and SQL revisions, Explore (compose →
  save recipe → run recipe, join map, tables, exploratory SQL), rename ID (references →
  typed confirmation → applied → old ID's "renamed to" error; recipe still runs; a pending
  rename on the Review page), access (ADR 0027: admin creates a custom role in Settings →
  Roles; a person generates a narrow token in My tokens — shown once, copy — signs in with
  it, sees the nav follow its tools, revokes it and is signed out; admin creates an agent
  with the custom role, generates a token for it in Settings → Users, revokes it → 401).
  Screenshots of each page land in
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
