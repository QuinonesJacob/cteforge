# tests/

pytest suite. Project root: ../CLAUDE.md

## What belongs here (authoritative)
- `unit/<module>/test_*.py` — mirrors src/cteforge/<module>/; pure, no warehouse.
- `integration/` — tests using a DuckDB warehouse (built in a tmp dir by fixtures), and
  `test_clickhouse_live.py`: the ClickHouse connector against a real server, skipped unless
  `CTEFORGE_TEST_CLICKHOUSE_URL` is set (how to run: its docstring; ADR 0025), and
  `test_postgres_live.py` (the Postgres connector plus the example definition end to end
  through the services; skipped unless `CTEFORGE_TEST_POSTGRES_URL` is set), and
  `test_mysql_live.py` (the MySQL connector against a real MySQL 8; skipped unless
  `CTEFORGE_TEST_MYSQL_URL` is set; ADR 0028). Tests never start or download a warehouse
  server; CI (`.github/workflows/ci.yml`, job `live-databases`) runs them against service
  containers and fails if any is skipped.
- `fixtures/clickhouse/` — the trace-shaped ClickHouse definition (`trace_tool_errors.sql`
  + `.annotations.yml`: traces → turns → tool_calls) shared by unit and live tests, and
  `live_setup.py`, run by hand as an admin to create the read-only user, settings profile
  and synthetic dataset on a test server.
- `fixtures/postgres/live_setup.py` — run as an admin to copy the example warehouse into a
  `cteforge_test` database and create the SELECT-only role `cteforge_ro`.
- `fixtures/mysql/` — the shop-shaped MySQL definition (`paid_revenue.sql` +
  `.annotations.yml`: customers → orders, DATE_FORMAT month grain), `hostile_strings.json`
  (text values that must render as one exact literal; unit tests and the live server's
  `shop.strings`), and `live_setup.py`, run as an admin to create the read-only user, the
  write probe user and the synthetic dataset on a test server.
- `e2e/` — the product wired together: `test_gui_contract.py` (every API call the GUI in
  `web/src/api/endpoints.ts` makes, with the GUI's body shapes, asserting the fields the GUI
  reads) and `test_server_e2e.py` (marked `e2e`: real `cteforge demo` + `cteforge serve`
  subprocess, built GUI, /api/v1, MCP over streamable HTTP and stdio; skipped without
  `web/dist`). Run only these: `uv run pytest tests/e2e`; skip the slow ones: `-m "not e2e"`.
- `golden/` — per-dialect expected SQL snapshots (ADR 0012); `trace_tool_errors*.clickhouse.sql`
  from the ClickHouse fixture, `paid_revenue*.mysql.sql` from the MySQL fixture.
- `conftest.py` — shared fixtures, and the embedding guard (ADR 0023): tests NEVER
  download or load the real embedding model. An autouse fixture gives every context no
  embedder (keyword search) and refuses `FastEmbedEmbedder._open`; the process env gets
  `CTEFORGE_SEARCH_EMBEDDINGS=off` and `HF_HUB_OFFLINE=1`; e2e servers are started with
  meaning search off. Tests that need meaning search pass `FakeEmbedder` (fixture
  `fake_embedder_class`: deterministic topic vectors) to `open_context(..., embedder=)` or
  patch `services.context.default_embedder`.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Common misplacements
- Sample data meant for demos — examples/.

## Conventions
- Assert on returned values, never on source text. Every assertion must be able to fail.
- Golden files update only deliberately (UPDATE_GOLDEN=1) and are reviewed as diffs.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
