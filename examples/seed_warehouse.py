"""Build examples/warehouse.duckdb: a small synthetic sales warehouse (no real data).

Run: uv run python examples/seed_warehouse.py [output_path]
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

REGIONS = ["EMEA", "NA", "APAC", "LATAM"]
STATUSES = ["closed_won", "closed_won_renewal", "closed_lost", "open"]


def seed(path: Path) -> None:
    rng = random.Random(42)  # Deterministic data for repeatable demos and tests.
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE accounts (account_id INTEGER, name VARCHAR, region VARCHAR)")
    con.execute(
        "CREATE TABLE deals (deal_id INTEGER, account_id INTEGER, closed_at TIMESTAMP, "
        "status VARCHAR, amount DECIMAL(12, 2))"
    )
    con.execute(
        "CREATE TABLE invoices (invoice_id INTEGER, deal_id INTEGER, invoiced_at TIMESTAMP, "
        "status VARCHAR, paid_amount DECIMAL(12, 2))"
    )
    accounts = [(i, f"Account {i}", rng.choice(REGIONS)) for i in range(1, 201)]
    con.executemany("INSERT INTO accounts VALUES (?, ?, ?)", accounts)

    deals, invoices = [], []
    start = datetime(2025, 7, 1)
    invoice_id = 1
    for deal_id in range(1, 2001):
        closed_at = start + timedelta(minutes=rng.randint(0, 60 * 24 * 540))
        status = rng.choices(STATUSES, weights=[50, 15, 25, 10])[0]
        amount = round(rng.uniform(1_000, 50_000), 2)
        deals.append((deal_id, rng.randint(1, 200), closed_at, status, amount))
        if status.startswith("closed_won"):
            for _ in range(rng.randint(1, 3)):
                paid = round(amount * rng.uniform(0.1, 0.5), 2)
                inv_status = rng.choices(["paid", "unpaid", "void"], weights=[70, 20, 10])[0]
                invoices.append(
                    (
                        invoice_id,
                        deal_id,
                        closed_at + timedelta(days=rng.randint(1, 90)),
                        inv_status,
                        paid,
                    )
                )
                invoice_id += 1
    con.executemany("INSERT INTO deals VALUES (?, ?, ?, ?, ?)", deals)
    con.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?, ?)", invoices)
    con.close()


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("warehouse.duckdb")
    seed(out)
    print(f"wrote {out}")
