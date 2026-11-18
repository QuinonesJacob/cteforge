"""Prepare a Postgres server for tests/integration/test_postgres_live.py.

Run once per server, as an administrator (NOT as the read-only role):

    export CTEFORGE_TEST_POSTGRES_URL=postgresql://localhost:5432   # host/port only
    export CTEFORGE_TEST_POSTGRES_ADMIN_USER=postgres
    export CTEFORGE_TEST_POSTGRES_ADMIN_PASSWORD=...
    export CTEFORGE_TEST_POSTGRES_PASSWORD=...                      # chosen for cteforge_ro
    uv sync --extra postgres
    uv run python examples/seed_warehouse.py                        # examples/warehouse.duckdb
    uv run python tests/fixtures/postgres/live_setup.py

It (re)creates database `cteforge_test` holding a copy of the synthetic example warehouse
(`accounts`, `deals`, `invoices` from examples/warehouse.duckdb, same types) and a role
`cteforge_ro` with LOGIN, CONNECT on the database, USAGE on schema public and SELECT on its
tables only — the setup the README recommends for real deployments.

Synthetic data only. Values are sent as bound parameters; identifiers and the password use
psycopg.sql composition (quoted identifiers / literals), never string formatting.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

DATABASE = "cteforge_test"
ROLE = "cteforge_ro"
WAREHOUSE = Path(__file__).resolve().parents[3] / "examples" / "warehouse.duckdb"
TABLES = {
    "accounts": "account_id INTEGER, name VARCHAR, region VARCHAR",
    "deals": "deal_id INTEGER, account_id INTEGER, closed_at TIMESTAMP, status VARCHAR, "
    "amount NUMERIC(12, 2)",
    "invoices": "invoice_id INTEGER, deal_id INTEGER, invoiced_at TIMESTAMP, status VARCHAR, "
    "paid_amount NUMERIC(12, 2)",
}


def _env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        sys.exit(f"Set {name} (see this file's docstring).")
    return value


def main() -> None:
    import duckdb
    import psycopg
    from psycopg import sql

    url = urlparse(_env("CTEFORGE_TEST_POSTGRES_URL"))
    admin = {
        "host": url.hostname or "localhost",
        "port": url.port or 5432,
        "user": _env("CTEFORGE_TEST_POSTGRES_ADMIN_USER"),
        "password": _env("CTEFORGE_TEST_POSTGRES_ADMIN_PASSWORD"),
    }
    ro_password = _env("CTEFORGE_TEST_POSTGRES_PASSWORD")
    if not WAREHOUSE.exists():
        sys.exit("Run `uv run python examples/seed_warehouse.py` first.")

    with psycopg.connect(dbname="postgres", autocommit=True, **admin) as con:
        con.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(DATABASE)))
        con.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
        exists = con.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [ROLE]).fetchone()
        verb = "ALTER" if exists else "CREATE"
        con.execute(
            sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(ROLE), sql.Literal(ro_password)
            )
        )

    source = duckdb.connect(str(WAREHOUSE), read_only=True)
    with psycopg.connect(dbname=DATABASE, **admin) as con:
        for table, columns in TABLES.items():
            con.execute(sql.SQL("CREATE TABLE {} (" + columns + ")").format(sql.Identifier(table)))
            rows = source.execute(f"SELECT * FROM {table}").fetchall()  # constant table names
            width = len(rows[0])  # not columns.split(","): NUMERIC(12, 2) has a comma
            insert = sql.SQL("INSERT INTO {} VALUES ({})").format(
                sql.Identifier(table), sql.SQL(", ").join(sql.Placeholder() * width)
            )
            with con.cursor() as cur:
                cur.executemany(insert, rows)
        con.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
        con.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(DATABASE), sql.Identifier(ROLE)
            )
        )
        con.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(ROLE)))
        con.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                sql.Identifier(ROLE)
            )
        )
        counts = {
            t: con.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[
                0
            ]
            for t in TABLES
        }
    source.close()
    print(f"Postgres ready: {counts}; read-only role {ROLE} on database {DATABASE}.")


if __name__ == "__main__":
    main()
