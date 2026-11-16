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
  trust/state badge mapping, display-only SQL tokenizer, formatting helpers, role checks.
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
- `DefinitionCard.history` may be null; `GET /audit` returns `AuditPage {events, limit, has_more}`;
  approve returns `ProposalOutcome`, reject/withdraw return `ProposalView` (the GUI reloads after
  any decision). `POST /users` / `POST /users/{name}/token` shapes are normalized in
  `endpoints.ts` (`{user, token}`, flat, or a bare token string).
- `/run` body: `uses: {<id>: {id, version}}`, `values: {<id>: {...}}`, `run`, `limit`.
- `/run` also takes `profile` and `question`; `GET /definitions` takes `limit` (the GUI sends
  100; the API default is 10). `RunOutcome.used` lists each definition version with its
  state and verifier + date; the run result shows it.
- Revise: `GET /definitions/{id}?include_source=true` adds `source_sql` (template rendered
  with its original values) and `annotations` (literal IDs addressing that SQL). The Edit
  tab sends `POST /proposals/definitions/{id}/revise {base_version, metadata: <changed keys
  only>, summary}`; revise mode in Import sends `{base_version, sql, dialect, metadata
  (changed keys, + profile if changed), annotations, summary}`.
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
- Approve/reject is offered to approvers and, for definition proposals, to the target
  definition's owner (contributor or above); verify only to human approvers.
- The pending badge shares one poller per tab (`state/usePendingCount.ts`) on
  `GET /proposals/count` (no diffs, not audited); still poll rarely and only while visible.
  GET /proposals builds diffs and writes an audit event per call: the Review page only.
- Verify is offered only on the latest version of an `llm_annotated` definition; approval is
  disabled while checks have errors; "also verify" is disabled on `checks_incomplete`.
- Four-eyes (ADR 0018 addendum; `lib/roles.ts` `isOwnWork`, `versionAuthors` mirror
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
  (+ validation), import → auto-apply → run, strict mode + review + four-eyes, audit
  filters, theme toggle and 375px layout, Edit-tab and SQL revisions, Explore (compose →
  save recipe → run recipe, join map, tables, exploratory SQL). Screenshots of each page land in
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
