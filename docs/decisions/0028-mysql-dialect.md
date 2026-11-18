# 0028 — MySQL is a dialect and a connector

Status: accepted · 2026-11-17 · amends ADR 0020 · **connector security review with the user
pending: not to be committed before it is approved**

## Context
Many teams keep operational and reporting data in MySQL, and their trusted SQL is written in
MySQL's own idioms: no DATE_TRUNC (periods are `DATE_FORMAT(ts, '%Y-%m-01')`,
`DATE(ts)`, `ts - INTERVAL WEEKDAY(ts) DAY`), backslash escapes in string literals,
`"..."` strings, `TIMESTAMP '...'` literals, TINYINT(1) booleans, UNSIGNED integers, ENUM / SET
columns. MySQL's read-only and statement-limit model also differs: `START TRANSACTION
READ ONLY` does not stop `SELECT ... INTO OUTFILE` or `LOAD_FILE()`, executable comments
`/*! ... */` run SQL that every other reader treats as a comment, and optimizer hints can
change a statement's own `max_execution_time`.

## Decision
- `Dialect.MYSQL` (sqlglot `mysql`, pinned 30.31.0): parsing, type mapping, literal rendering,
  grains, golden tests; a `mysql` connector (`warehouse/mysql.py`, extra `cteforge[mysql]`,
  PyMySQL). MySQL 8 is the target; **MariaDB is untested** (best effort, not supported: it has
  `max_statement_time`, not `max_execution_time`, so the connector's timeout fails there).
- **Types** (`information_schema.columns.column_type`): TINYINT(1) (what BOOL / BOOLEAN are
  stored as) → number and boolean; other integers incl. UNSIGNED, DECIMAL, FLOAT / DOUBLE,
  BIT, YEAR → number; DATE / DATETIME / TIMESTAMP → date, date_range (sqlglot reads
  TIMESTAMP as zone-aware — MySQL converts it with the session time zone — and DATETIME as
  naive); CHAR / VARCHAR / *TEXT / ENUM / SET → text, text_list; JSON / BLOB / BINARY /
  VARBINARY / GEOMETRY and spatial types → no variable type.
- **Literals.** MySQL reads `\` as an escape in strings unless the server's sql_mode has
  NO_BACKSLASH_ESCAPES, so MySQL joins the backslash-escape dialects. sqlglot's generator
  doubles `'` and `\` and writes NUL, newline, CR, tab, backspace and Ctrl-Z as `\0 \n \r \t
  \b \Z`; `%` and `_` stay as they are (they are only LIKE wildcards, which is what a text
  value means there). Checked offline against an independent reader of MySQL string
  literals for quotes, backslashes, `\'`, NUL, control characters, `%` / `_` / `\%` / `\_`,
  Unicode quotes and comment markers (each stays one literal with the exact value), and
  live against a utf8mb4_bin column. The connection charset is always utf8mb4, so no
  multi-byte character can hide a `'` or `\` byte (the GBK / SJIS escaping attack).
  Placeholders in stored templates are `:name`. A `TIMESTAMP '...'` literal is written back
  as a literal (sqlglot would write the function `TIMESTAMP('...')`, a different tree).
- **sql_mode.** cteforge reads and writes MySQL as sqlglot does under the default sql_mode
  (`\` escapes, `"..."` is a string, `||` is OR). The connector refuses a session whose
  sql_mode has NO_BACKSLASH_ESCAPES, ANSI_QUOTES or PIPES_AS_CONCAT (also via ANSI): rendered
  values would change and trusted SQL would mean something else.
- **Grains.** MySQL has no DATE_TRUNC. Grain candidates (MySQL only, so other dialects' grain
  IDs never shift) are `DATE_FORMAT(x, f)` with a period-start format: `'%Y-%m-%d'` day,
  `'%Y-%m-01'` month, `'%Y-01-01'` year, each optionally followed by `' 00:00:00'`; and the day
  format over `x - INTERVAL WEEKDAY(x) DAY` / `DATE_SUB(x, INTERVAL WEEKDAY(x) DAY)` (week)
  or `MAKEDATE(YEAR(x), 1) + INTERVAL QUARTER(x) - 1 QUARTER` (quarter). A value already
  aligned to another period (computed with YEAR / MONTH / WEEKDAY / DAYOFWEEK / MAKEDATE /
  LAST_DAY / ..., e.g. a Sunday week start) is not a candidate: another grain would
  truncate that start, not the timestamp. A grain change is rebuilt in this family, so the result
  keeps its type ('YYYY-MM-DD' text, time suffix kept) and every unit is runnable MySQL:
  day / month / year change the format; week is `DATE_FORMAT(x - INTERVAL WEEKDAY(x) DAY,
  '%Y-%m-%d')` (**Monday**, ISO; WEEKDAY() is 0 on Monday); quarter is
  `DATE_FORMAT(MAKEDATE(YEAR(x), 1) + INTERVAL QUARTER(x) - 1 QUARTER, '%Y-%m-%d')`. Week and
  quarter name `x` twice (the second a copy), so render now rebuilds nested truncations first
  (all dialects; other rebuilds move their arguments, so the order does not matter there).
  A DATE result is the analyst's wrapper: `CAST(DATE_FORMAT(x, '%Y-%m-01') AS DATE)`,
  `DATE(...)`, `STR_TO_DATE(...)` keep working, since only the DATE_FORMAT node is rebuilt.
  Not candidates: `DATE(x)` (a cast, like `CAST(x AS DATE)` elsewhere — it is also the usual
  date filter, which a grain change must not touch), label formats (`'%Y-%m'`, `'%x-%v'`),
  `LAST_DAY`, `YEARWEEK`, `WEEK`.
- **Same query back** for MySQL-written SQL: `INTERVAL (expr) unit` and `INTERVAL expr unit`
  read the same (sqlglot writes computed counts in parentheses), TIMESTAMP literals as above.
  Checked for DATE_FORMAT / DATE() / STR_TO_DATE / DATE_SUB / CURDATE / IFNULL / REGEXP /
  `<=>` / GROUP_CONCAT / backtick names / `"..."` strings / `LIMIT o, n`. Known difference
  (reported, never silent): adjacent string literals `'a' 'b'` become `CONCAT('a', 'b')`.
- **SQL the server would run differently** is refused at parse time (MySQL only):
  executable comments `/*! ... */` (and MariaDB's `/*M! ... */`) — MySQL runs their content,
  every checker sees a comment, so `SELECT 1 /*! INTO OUTFILE '...' */` would pass any
  read-only check; and optimizer hints naming `MAX_EXECUTION_TIME` or `SET_VAR`, which would
  lift the statement timeout or change session settings for the statement; and a comment
  that starts with non-ASCII whitespace (`--` + U+00A0, U+3000, ...): sqlglot starts a `--`
  comment before any Unicode space, MySQL only before an ASCII space or control byte, so
  MySQL would run the rest of the line as SQL (found in the security review, 2026-11-17).
  Checked on sqlglot's MySQL tokens, so text in string literals is not affected.
  sqlglot writes line comments back as `/* */` with `*/` and `/*` broken up.
- **Read-only guard** (all dialects, compose.checks.ensure_query — the real guard, before any
  SQL reaches a connector): SELECT ... INTO (`exp.Into`) and row locks were already refused;
  sqlglot cannot parse `INTO OUTFILE` / `INTO DUMPFILE` / multi-variable `INTO @a, @b` at all,
  so those fail closed as SqlParseError. MySQL side-effect functions join the denylist:
  LOAD_FILE, SLEEP, BENCHMARK, GET_LOCK / RELEASE_LOCK / RELEASE_ALL_LOCKS, MASTER_POS_WAIT /
  SOURCE_POS_WAIT / WAIT_FOR_EXECUTED_GTID_SET / WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS, and the
  common shell UDFs sys_exec / sys_eval. Not exhaustive: the guarantee is the grants.

## Connector (HARDENED; details in `warehouse/mysql.py`)
Driver: PyMySQL (MIT, pure Python), reviewed at 1.2.4 (`>=1.1,<2`). Options are an allowlist
(host, port, database, user, ssl_ca, ssl_verify_cert, ssl_verify_identity, ssl_disabled,
connect_timeout, read_timeout); everything else is refused by name — in particular
local_infile, client_flag, init_command, sql_mode, autocommit, read_default_file /
read_default_group, unix_socket, charset, ssl, client certificates. The only secret is
`password` (by env var); `user` is required (PyMySQL would use the OS user).

- TLS verified by default: the connector passes its own `ssl.SSLContext` (system trust store
  or `ssl_ca`, host name checked, TLS 1.2+), which PyMySQL uses as given and then requires;
  PyMySQL's own default (PREFERRED) would be unverified and fall back to plaintext.
- Single statement: PyMySQL's capability flags have no CLIENT.MULTI_STATEMENTS
  (constants/CLIENT.py) and `client_flag` is refused, so the server refuses several
  statements (1064). `execute(sql)` without arguments sends the SQL unchanged (no `%`
  formatting).
- Each call: `START TRANSACTION READ ONLY` → `SET SESSION max_execution_time = %s` (an int
  bound parameter, ms) → statement → ROLLBACK, autocommit off. READ ONLY is defence in
  depth; it does not stop INTO OUTFILE or LOAD_FILE (only the FILE privilege does). The
  guarantee comes from grants: a user with SELECT on the needed schemas only — no FILE,
  PROCESS, SUPER, CREATE TEMPORARY TABLES or write privileges (README).
- Timeout: ER_QUERY_TIMEOUT 3024 (and MariaDB's 1969, untested) or the socket read timeout
  (default statement timeout + 30 s; error 2013 "timed out") → `query timed out after Ns`. A
  dropped connection is reopened, and its sql_mode checked again, on the next call.
- Rows: an unbuffered cursor (SSCursor) fetches at most limit + 1 rows; closing it reads
  and discards the rest of the stream (MySQL cannot stop sending a result), bounded by the
  statement timeout.
- Column types: `information_schema.columns` (`column_type`) with bound parameters; schema =
  database; unqualified names resolve in `DATABASE()`; an exact-case table name wins,
  ambiguous ones are unknown. `explain` = `EXPLAIN <sql>`.
- Errors: PyMySQL raises `(code, message)`; only the message is kept, `'user'@'host'`
  accounts are removed, then the usual scrub.

## Consequences
- Transpiling into MySQL keeps sqlglot's behavior visible in goldens: DATE_TRUNC from other
  dialects becomes `DATE_ADD('0000-01-01 00:00:00', INTERVAL (TIMESTAMPDIFF(unit,
  '0000-01-01 00:00:00', x)) unit)`; for weeks that counts from 0000-01-01, which MySQL's
  calendar (calc_daynr / calc_weekday) makes a **Sunday**, so transpiled weeks start on
  Sunday (the live test checks it). The result is DATETIME-shaped text, not DATE.
  Transpiling MySQL definitions elsewhere renders DATE_FORMAT as the target's formatting
  function and WEEKDAY / MAKEDATE as written (not functions in most targets): labeled
  `transpiled`, lower trust (ADR 0013).
- Definitions written against a server with NO_BACKSLASH_ESCAPES / ANSI_QUOTES /
  PIPES_AS_CONCAT cannot be connected until the cteforge user's sql_mode is the default.
- The connector is unit-tested against a fake driver; `tests/integration/test_mysql_live.py`
  runs against a real server only when `CTEFORGE_TEST_MYSQL_URL` is set
  (`tests/fixtures/mysql/live_setup.py` prepares it). **Not yet run against a real MySQL**:
  everything that needs a server (READ ONLY refusals, the multi-statement refusal,
  max_execution_time inside a READ ONLY transaction, error texts, the transpiled week start,
  the hostile literals on a server) is unverified until those tests run in CI.
- The GUI's dialect list (`web/src/api/types.ts` DIALECTS) does not include `mysql` yet.
