# 0025 — ClickHouse is a dialect and a connector

Status: accepted · 2026-11-17 · amends ADR 0011, 0020 · connector security review approved 2026-11-17

## Context
ClickHouse is widely used for AI workloads: agent platforms store traces there (traces →
turns → text and tool calls, hundreds of thousands of traces). Trusted SQL over that data
uses ClickHouse's own shapes: array columns (`has`, `hasAny`, lambdas, `ARRAY JOIN`),
`Map` / JSON attributes, `toStartOf*` bucketing, `FINAL` / `SAMPLE` / `PREWHERE` /
`LIMIT n BY` / `SETTINGS`. ClickHouse has its own query-parameter syntax `{name:Type}`,
which sqlglot (30.31.0) parses as the same `exp.Placeholder` cteforge templates use
(ADR 0011), and its own read-only model (the `readonly` setting, not transactions).

## Decision
- `Dialect.CLICKHOUSE` (sqlglot `clickhouse`): parsing, type mapping, literal rendering,
  grains, golden tests; a `clickhouse` connector (`warehouse/clickhouse.py`, extra
  `cteforge[clickhouse]`).
- **Placeholders.** Templates write `{name: Type}` (ClickHouse writes an untyped placeholder
  as `{name: }`, which does not parse back). The type is informational (text → String,
  number → Float64, date → Date, boolean → Bool); render replaces every placeholder with a
  literal and cteforge never sends query parameters. Trusted SQL containing a query
  parameter (`{x:String}`, `?`) is refused at analysis with a clear SqlParseError ("replace
  the parameter with a literal and mark it as a variable"): it would not run as written and
  would collide with template placeholders. A `{x:String}` inside a string literal is text.
- **Types.** `Nullable(T)`, `LowCardinality(T)`, `SimpleAggregateFunction(f, T)` → T.
  Date / Date32 / DateTime[('tz')] / DateTime64(p[, 'tz']) → date, date_range;
  String / FixedString / Enum8 / Enum16 / UUID → text, text_list; (U)Int* / Float* /
  Decimal* → number; Bool → boolean; Array / Map / Tuple / JSON / Object / Nested → none.
- **Literals.** ClickHouse reads `\'` as an escaped quote (sqlglot `STRING_ESCAPES`), so it
  joins the backslash-escape dialects; sqlglot's generator doubles the backslash and the
  quote, and a hostile value stays one literal (tested). Numbers keep their exact text
  (UInt64 max included). The scale / time zone of `toDateTime64(v, 3, 'UTC')` and other
  conversions are options, never values.
- **Filter shapes (ClickHouse only, so other dialects' stored literal IDs never shift):**
  `has(arr, 'x')` → operator `HAS`, compared with the array's element type;
  `hasAny(arr, [...])` / `hasAll` → `HAS ANY` / `HAS ALL`, **list shapes like `= ANY`**
  (each element its own literal, no IN group, so a text_list cannot replace the array — use
  `IN (...)` for a changeable list); `match(s, 're')` → `MATCH`; a lambda parameter
  (`arrayExists(x -> x = 'a', arr)`, arrayFilter / arrayAll / ...) and an `ARRAY JOIN` name
  are compared as elements of their array column; `map['k']` / `arr[i]` give the value /
  element type; `JSONExtractString` → String; `toDate` / `toDateTime*` / `toStartOf*` have
  their ClickHouse result types.
- **Grains.** toStartOfDay / Month / Quarter / Year / Hour / Minute and toMonday (sqlglot:
  TimestampTrunc), `date_trunc` / `dateTrunc` (DateTrunc), and — ClickHouse only —
  `toStartOfWeek(x[, mode[, tz]])` and `toStartOfInterval(x, INTERVAL n unit[, ...])` are grain
  candidates. Native week = Monday; `toStartOfWeek` with the default / an even mode is
  `week(sunday)` (not a grain value). A grain change is rebuilt in the SQL's own family
  (toStartOf* ↔ toMonday, toStartOfInterval keeps toStartOfInterval, date_trunc keeps
  date_trunc), keeping the time-zone argument.
- **Spelling kept.** sqlglot reads toStartOfMonth(x) as a TimestampTrunc and writes
  `dateTrunc('MONTH', x)`. `parsing.analyze.generate` (used for every SQL text cteforge
  writes) writes these back under the name the SQL used (sqlglot keeps it in node meta),
  so trusted SQL keeps its functions and return types (dateTrunc's return type changed
  across ClickHouse versions). `date_trunc` / `dateTrunc` keep the name written and get a
  lower-case unit: sqlglot writes `'MONTH'`, which ClickHouse reads only since 23.12.
- **Clauses** FINAL, SAMPLE, PREWHERE, ARRAY JOIN, LIMIT n BY, SETTINGS, scalar `WITH x AS
  expr`, GLOBAL IN round-trip exactly (tested). Names are `database.table`.
- **Connector** (HARDENED; details in `warehouse/clickhouse.py` and below).

## Consequences
- `INTERVAL 7 DAY` is written `INTERVAL '7' DAY` (sqlglot); ClickHouse documents both as
  identical. Its count is a string literal, so a number variable cannot replace it (same as
  INTERVAL strings in other dialects); use a date_range for periods.
- A scalar `WITH 'x' AS name` value is a named constant, not a filter literal.
- Transpiling from DuckDB to ClickHouse keeps sqlglot's defects visible in goldens:
  `strptime` becomes `STR_TO_TIME` (not a ClickHouse function); naive casts become
  `Nullable(DateTime)`. Rendering ClickHouse definitions elsewhere maps `DateTime[64]` with
  a time zone to TIMESTAMPTZ and without one to a naive timestamp.
- The connector is unit-tested against a fake driver; `tests/integration/test_clickhouse_live.py`
  runs against a real server only when `CTEFORGE_TEST_CLICKHOUSE_URL` is set.
- **Verified live 2026-11-17** against ClickHouse 26.10.5.57 (official build, local,
  bound to 127.0.0.1): all 22 live tests pass — column types, EXPLAIN, limits, week
  starts, refusal of writes / DDL / `SETTINGS readonly = 0` / `url()` / multi-statement,
  profile-capped timeouts, scan guards, scrubbed auth errors, and the trace definition's
  same-query-back and new-value / weekly-grain renders. Not in CI yet.

## Addendum (2026-11-17): connector — security review approved 2026-11-17
Driver: `clickhouse-connect` (ClickHouse Inc., Apache-2.0, HTTP/HTTPS), reviewed at 1.13.0
(`>=1.13,<2`). Options are an allowlist (host, port, database, user, secure, verify,
ca_cert, connect_timeout, send_receive_timeout, max_rows_to_read, max_bytes_to_read);
`dsn`, `interface` (chdb), `access_token`, client certificates and `settings` are refused;
the only secret is `password` (by env var); `user` is required.

Every request carries, as HTTP parameters (never SQL text): `readonly=2`,
`max_execution_time` + `timeout_overflow_mode=throw` (from `statement_timeout_s`),
`cancel_http_readonly_queries_on_client_close=1`, and the scan guards with
`read_overflow_mode=throw` when set. `readonly=2` rather than `1`: readonly=1 refuses every
settings change in the request, including our own max_execution_time and a definition's
`SETTINGS` clause; readonly=2 still refuses INSERT / DDL / OPTIMIZE / SYSTEM / KILL /
grants and any change of `readonly` itself (ClickHouse Settings.cpp, ContextAccess.cpp,
SettingsConstraints.cpp). It does allow a query to raise other limits (e.g. its own
max_execution_time) — only the user's settings-profile constraints stop that, so the README
requires a profile (`readonly = 2 CONST`, `max_execution_time = 60 MAX 300`, ...). CREATE
TEMPORARY TABLE and table functions (url, s3, remote, ...) are governed by grants: grant
SELECT on the needed databases only.

Single statement: the HTTP handler parses with multi-statements disabled ("Multi-statements
are not allowed"); clickhouse-connect sends the SQL unchanged (no client-side binding
without parameters). Rows: `run` streams blocks and stops after limit + 1 rows (closing the
response; the server cancels the read query); not `max_result_rows` +
`result_overflow_mode=break`, which ClickHouse also applies to subqueries. Timeouts: error
code 159 TIMEOUT_EXCEEDED, or the HTTP read timeout (default statement timeout + 30 s),
raise `query timed out after Ns`. Column types: `system.columns` with server-bound
parameters. `explain` = `EXPLAIN PLAN`. Errors: `show_clickhouse_errors='scrub'`, then only
the last `DB::Exception:` message (no `Code:`, `Received from <host>`, version, stack trace),
then the usual scrub. No HTTP session; the driver's logger is set to ERROR unless
configured; the connector refuses to start if clickhouse-connect's
`invalid_setting_action` is `drop` (it would silently drop our settings).
