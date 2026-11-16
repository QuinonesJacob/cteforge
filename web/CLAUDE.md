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
  description rule), diff line classification, trust/state badge mapping, display-only SQL
  tokenizer, formatting helpers, role checks.
- `src/state/` — React context providers and hooks (auth, toasts, theme, async loading).
- `src/pages/` — one file per route: SignIn, Library, Definition, Import, Review (list +
  detail), Audit, Settings, NotFound.
- `src/components/` — shared UI pieces, one component per file (SQL viewer, variable form,
  issue list, trust badge, diff viewer, literal table, …).
- `src/styles/` — plain CSS: `tokens.css` (light/dark custom properties), `base.css`,
  `components.css`, `pages.css`.
- `src/test/` — vitest setup and test helpers (`fetchMock.ts`). Tests sit next to the code
  as `*.test.ts(x)`.

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
  @testing-library/user-event (DOM tests), @types/* (typings). Exact versions pinned.
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

## Commands (run in web/)
- `npm install` — install
- `npm run dev` — Vite dev server (proxies to `cteforge serve` on :8765)
- `npm run typecheck` — `tsc -b`
- `npm test` — vitest (jsdom)
- `npm run build` — typecheck + build to `web/dist`

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
