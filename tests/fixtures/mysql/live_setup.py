"""Prepare a MySQL 8 server for tests/integration/test_mysql_live.py (ADR 0028).

Run once per server, as an administrator (NOT as the read-only user):

    export CTEFORGE_TEST_MYSQL_URL=mysql://127.0.0.1:3306
    export CTEFORGE_TEST_MYSQL_ADMIN_USER=root                # an admin user
    export CTEFORGE_TEST_MYSQL_ADMIN_PASSWORD=...             # its password
    export CTEFORGE_TEST_MYSQL_PASSWORD=...                   # chosen for cteforge_ro
    uv sync --extra mysql
    uv run python tests/fixtures/mysql/live_setup.py

Optional: CTEFORGE_TEST_MYSQL_SSL_DISABLED=1 for a server without TLS (the admin connection
otherwise uses PyMySQL's default: TLS when the server offers it, not verified — this is
test tooling, never the connector).

It (re)creates:
- database `shop` with a small deterministic dataset: `customers` (200 rows; country DE /
  FR / US / GB), `orders` (5,000 rows over 2026-01-01 .. 2026-03-31; channel, status,
  is_test TINYINT(1), amount DECIMAL(10,2)), `strings` (the hostile text values of
  hostile_strings.json, utf8mb4_bin, inserted with bound parameters) and `probe`
  (an empty table only cteforge_probe may write to);
- user `cteforge_ro` with only SELECT on shop.* — the setup the README recommends (no
  FILE, PROCESS, SUPER, CREATE TEMPORARY TABLES or any write privilege);
- user `cteforge_probe` with SELECT on shop.* and INSERT on shop.probe, which only exists
  to show that the connector's READ ONLY transaction refuses a write a grant would allow.

Synthetic data only. Passwords and values are passed as bound parameters.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

DATABASE = "shop"
USERS = ("cteforge_ro", "cteforge_probe")
ORDERS = 5000

SCHEMA = [
    f"CREATE DATABASE IF NOT EXISTS {DATABASE} CHARACTER SET utf8mb4",
    f"DROP TABLE IF EXISTS {DATABASE}.orders",
    f"DROP TABLE IF EXISTS {DATABASE}.customers",
    f"DROP TABLE IF EXISTS {DATABASE}.strings",
    f"DROP TABLE IF EXISTS {DATABASE}.probe",
    f"""CREATE TABLE {DATABASE}.customers (
        customer_id BIGINT UNSIGNED PRIMARY KEY,
        country CHAR(2) NOT NULL
    )""",
    f"""CREATE TABLE {DATABASE}.orders (
        order_id BIGINT UNSIGNED PRIMARY KEY,
        customer_id BIGINT UNSIGNED NOT NULL,
        created_at DATETIME NOT NULL,
        channel ENUM('web', 'app', 'store') NOT NULL,
        status ENUM('new', 'paid', 'shipped', 'refunded') NOT NULL,
        is_test TINYINT(1) NOT NULL,
        amount DECIMAL(10, 2) NOT NULL,
        note VARCHAR(200) NOT NULL
    )""",
    f"""CREATE TABLE {DATABASE}.strings (
        id INT PRIMARY KEY,
        s VARCHAR(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
    )""",
    f"CREATE TABLE {DATABASE}.probe (id INT PRIMARY KEY)",
    "SET SESSION cte_max_recursion_depth = 10000",
    f"""INSERT INTO {DATABASE}.customers (customer_id, country)
    WITH RECURSIVE seq (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 200)
    SELECT n, ELT(n % 4 + 1, 'DE', 'FR', 'US', 'GB') FROM seq""",
    f"""INSERT INTO {DATABASE}.orders
    WITH RECURSIVE seq (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < {ORDERS})
    SELECT
        n,
        (n * 7) % 200 + 1,
        TIMESTAMP '2026-01-01 00:00:00' + INTERVAL ((n * 7919) % (90 * 86400)) SECOND,
        ELT(n % 3 + 1, 'web', 'app', 'store'),
        -- n DIV 4, not n % 4: the customer's country already follows n % 4 (7n mod 200),
        -- so n % 4 here would give every country a single status.
        ELT((n DIV 4) % 4 + 1, 'new', 'paid', 'shipped', 'refunded'),
        n % 50 = 0,
        ((n * 37) % 50000) / 100 + 5,
        CONCAT('order ', n)
    FROM seq""",
]


def hostile_values() -> list[str]:
    """The values tests/unit/templating/test_mysql_templates.py checks offline."""
    path = Path(__file__).resolve().parent / "hostile_strings.json"
    return list(json.loads(path.read_text(encoding="ascii")))


def main() -> int:
    url = os.environ.get("CTEFORGE_TEST_MYSQL_URL")
    password = os.environ.get("CTEFORGE_TEST_MYSQL_PASSWORD")
    if not url or not password:
        print(
            "Set CTEFORGE_TEST_MYSQL_URL and CTEFORGE_TEST_MYSQL_PASSWORD (and the ADMIN_USER / "
            "ADMIN_PASSWORD variables); see this file's docstring.",
            file=sys.stderr,
        )
        return 2
    import pymysql

    parsed = urlparse(url)
    tls = (
        {"ssl_disabled": True} if os.environ.get("CTEFORGE_TEST_MYSQL_SSL_DISABLED") == "1" else {}
    )
    admin = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=os.environ.get("CTEFORGE_TEST_MYSQL_ADMIN_USER", "root"),
        password=os.environ.get("CTEFORGE_TEST_MYSQL_ADMIN_PASSWORD", ""),
        charset="utf8mb4",
        autocommit=True,
        **tls,
    )
    try:
        with admin.cursor() as cur:
            for statement in SCHEMA:
                cur.execute(statement)
            cur.executemany(
                f"INSERT INTO {DATABASE}.strings (id, s) VALUES (%s, %s)",
                list(enumerate(hostile_values(), start=1)),
            )
            for user in USERS:  # '%%' where arguments are bound: PyMySQL %-formats those
                cur.execute(f"DROP USER IF EXISTS '{user}'@'%'")
                cur.execute(f"CREATE USER '{user}'@'%%' IDENTIFIED BY %s", (password,))
                cur.execute(f"GRANT SELECT ON {DATABASE}.* TO '{user}'@'%'")
            cur.execute(f"GRANT INSERT ON {DATABASE}.probe TO 'cteforge_probe'@'%'")
            counts = {}
            for table in ("customers", "orders", "strings"):
                cur.execute(f"SELECT COUNT(*) FROM {DATABASE}.{table}")
                counts[table] = cur.fetchone()[0]
    finally:
        admin.close()
    print(f"MySQL ready: {counts}; read-only user cteforge_ro (SELECT on {DATABASE}.* only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
