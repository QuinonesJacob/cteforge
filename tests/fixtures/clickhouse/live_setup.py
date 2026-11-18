"""Prepare a ClickHouse server for tests/integration/test_clickhouse_live.py (ADR 0025).

Run once per server, as an administrator (NOT as the read-only user):

    export CTEFORGE_TEST_CLICKHOUSE_URL=http://localhost:8123      # or https://host:8443
    export CTEFORGE_TEST_CLICKHOUSE_ADMIN_USER=default              # an admin user
    export CTEFORGE_TEST_CLICKHOUSE_ADMIN_PASSWORD=...              # its password ('' if none)
    export CTEFORGE_TEST_CLICKHOUSE_PASSWORD=...                    # chosen for cteforge_ro
    uv sync --extra clickhouse
    uv run python tests/fixtures/clickhouse/live_setup.py

It (re)creates:
- database `agent_traces` with a small deterministic, trace-shaped dataset: `traces`
  (3,000 rows; model, tags, attrs), `turns` (4 per trace: user / assistant / tool /
  assistant) and `tool_calls` (2 per assistant turn; ~1 in 7 has status 'error');
- settings profile `cteforge_ro_profile`: readonly = 2 CONST (reads only; the query cannot
  change readonly), allow_ddl = 0 CONST, max_execution_time = 60 MAX 300 (a query's own
  SETTINGS clause cannot raise its timeout past 300 s), max_memory_usage MAX 10 GB;
- user `cteforge_ro` with that profile and only SELECT on agent_traces.* — the setup the
  README recommends for real deployments.

Synthetic data only. The password is passed to the server as a bound parameter.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

DATABASE = "agent_traces"
USER = "cteforge_ro"
PROFILE = "cteforge_ro_profile"

SCHEMA = [
    f"CREATE DATABASE IF NOT EXISTS {DATABASE}",
    f"DROP TABLE IF EXISTS {DATABASE}.tool_calls",
    f"DROP TABLE IF EXISTS {DATABASE}.turns",
    f"DROP TABLE IF EXISTS {DATABASE}.traces",
    f"""CREATE TABLE {DATABASE}.traces (
        trace_id UInt64,
        started_at DateTime64(3, 'UTC'),
        model LowCardinality(String),
        tags Array(LowCardinality(String)),
        attrs Map(String, String)
    ) ENGINE = MergeTree ORDER BY (started_at, trace_id)""",
    f"""CREATE TABLE {DATABASE}.turns (
        turn_id UInt64,
        trace_id UInt64,
        idx UInt8,
        role Enum8('user' = 1, 'assistant' = 2, 'tool' = 3),
        text String,
        started_at DateTime64(3, 'UTC')
    ) ENGINE = MergeTree ORDER BY (trace_id, idx)""",
    f"""CREATE TABLE {DATABASE}.tool_calls (
        call_id UInt64,
        turn_id UInt64,
        tool_name LowCardinality(String),
        status Enum8('ok' = 1, 'error' = 2),
        latency_ms UInt32,
        called_at DateTime64(3, 'UTC'),
        args String
    ) ENGINE = MergeTree ORDER BY (called_at, call_id)""",
    f"""INSERT INTO {DATABASE}.traces
    SELECT
        number,
        toDateTime64('2026-01-01 00:00:00', 3, 'UTC')
            + toIntervalSecond((number * 7919) % (59 * 86400)),
        ['claude-sonnet-4', 'claude-opus-4', 'claude-haiku-4'][number % 3 + 1],
        if(number % 5 = 0, ['eval'], ['production', if(number % 2 = 0, 'web', 'api')]),
        map('env', if(number % 5 = 0, 'staging', 'prod'), 'region', ['eu', 'us'][number % 2 + 1])
    FROM numbers(3000)""",
    f"""INSERT INTO {DATABASE}.turns
    SELECT
        t.trace_id * 4 + n.number,
        t.trace_id,
        n.number,
        ['user', 'assistant', 'tool', 'assistant'][n.number + 1],
        concat('turn ', toString(n.number), ' of trace ', toString(t.trace_id)),
        t.started_at + toIntervalSecond(n.number * 5)
    FROM {DATABASE}.traces AS t CROSS JOIN numbers(4) AS n""",
    f"""INSERT INTO {DATABASE}.tool_calls
    SELECT
        u.turn_id * 2 + n.number,
        u.turn_id,
        ['web_search', 'code_exec', 'file_read', 'sql_query'][(u.turn_id + n.number) % 4 + 1],
        if((u.turn_id * 31 + n.number) % 7 = 0, 'error', 'ok'),
        (u.turn_id * 13) % 2000 + 20,
        u.started_at + toIntervalMillisecond(100 + n.number * 50),
        '{{}}'
    FROM {DATABASE}.turns AS u CROSS JOIN numbers(2) AS n
    WHERE u.role = 'assistant'""",
]

ACCESS = [
    f"DROP USER IF EXISTS {USER}",
    f"""CREATE SETTINGS PROFILE OR REPLACE {PROFILE} SETTINGS
        readonly = 2 CONST,
        allow_ddl = 0 CONST,
        max_execution_time = 60 MAX 300,
        max_memory_usage = 10000000000 MAX 10000000000""",
    # The password is a client-side bound, escaped parameter (clickhouse-connect %(pw)s).
    f"CREATE USER {USER} IDENTIFIED WITH sha256_password BY %(pw)s SETTINGS PROFILE {PROFILE}",
    f"GRANT SELECT ON {DATABASE}.* TO {USER}",
]


def main() -> int:
    url = os.environ.get("CTEFORGE_TEST_CLICKHOUSE_URL")
    password = os.environ.get("CTEFORGE_TEST_CLICKHOUSE_PASSWORD")
    if not url or not password:
        print(
            "Set CTEFORGE_TEST_CLICKHOUSE_URL and CTEFORGE_TEST_CLICKHOUSE_PASSWORD (and the "
            "ADMIN_USER / ADMIN_PASSWORD variables); see this file's docstring.",
            file=sys.stderr,
        )
        return 2
    import clickhouse_connect

    parsed = urlparse(url)
    admin = clickhouse_connect.create_client(
        host=parsed.hostname,
        port=parsed.port,
        secure=parsed.scheme == "https",
        username=os.environ.get("CTEFORGE_TEST_CLICKHOUSE_ADMIN_USER", "default"),
        password=os.environ.get("CTEFORGE_TEST_CLICKHOUSE_ADMIN_PASSWORD", ""),
        autogenerate_session_id=False,
    )
    try:
        for statement in SCHEMA:
            admin.command(statement)
        for statement in ACCESS:
            params = {"pw": password} if "%(pw)s" in statement else None
            admin.command(statement, parameters=params)
        counts = {
            table: admin.command(f"SELECT count() FROM {DATABASE}.{table}")
            for table in ("traces", "turns", "tool_calls")
        }
    finally:
        admin.close()
    print(f"ClickHouse ready: {counts}; read-only user {USER} with profile {PROFILE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
