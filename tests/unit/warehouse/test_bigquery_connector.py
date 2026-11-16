"""BigQuery connector against a fake google.cloud.bigquery module."""

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.bigquery import DEFAULT_MAXIMUM_BYTES_BILLED, BigQueryWarehouse

PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----MIIEsecretkeymaterial"


class NotFound(Exception):
    code = 404


class FakeRow:
    def __init__(self, values):
        self._values = values

    def values(self):
        return self._values


class FakeIterator:
    def __init__(self, rows, schema):
        self._rows = rows
        self.schema = schema

    def __iter__(self):
        return iter(self._rows)


class FakeJob:
    def __init__(self, client):
        self.client = client
        self.cancelled = False
        client.jobs.append(self)

    def result(self, max_results=None, timeout=None):
        self.client.max_results.append(max_results)
        self.client.timeouts.append(timeout)
        if self.client.result_error is not None:
            raise self.client.result_error
        rows = self.client.rows if max_results is None else self.client.rows[:max_results]
        return FakeIterator(rows, self.client.result_schema)

    def cancel(self):
        self.cancelled = True


class FakeClient:
    def __init__(self, project=None, location=None, credentials=None):
        self.project = project or "default-proj"
        self.location = location
        self.credentials = credentials
        self.queries: list[tuple] = []
        self.max_results: list[int | None] = []
        self.timeouts: list[int | None] = []
        self.jobs: list = []
        self.result_error: Exception | None = None
        self.tables: dict[str, list] = {}
        self.table_requests: list[str] = []
        self.rows: list[FakeRow] = []
        self.result_schema: list = []
        self.fail_with: Exception | None = None
        self.closed = False
        FAKE_STATE["clients"].append(self)

    def query(self, sql, job_config=None):
        self.queries.append((sql, job_config))
        if self.fail_with is not None:
            raise self.fail_with
        return FakeJob(self)

    def get_table(self, table_id):
        self.table_requests.append(table_id)
        if self.fail_with is not None:
            raise self.fail_with
        if table_id not in self.tables:
            raise NotFound(f"Not found: Table {table_id}")
        return SimpleNamespace(schema=self.tables[table_id])

    def close(self):
        self.closed = True


class FakeQueryJobConfig:
    job_timeout_ms = None  # a client version that supports server-side job timeouts

    def __init__(self, **kwargs):
        self.dry_run = False
        self.use_query_cache = True
        self.maximum_bytes_billed = None
        self.default_dataset = None
        for key, value in kwargs.items():
            setattr(self, key, value)


FAKE_STATE: dict = {}


def field(name, field_type, mode="NULLABLE"):
    return SimpleNamespace(name=name, field_type=field_type, mode=mode)


@pytest.fixture()
def bq(monkeypatch):
    FAKE_STATE.clear()
    FAKE_STATE["clients"] = []
    FAKE_STATE["credentials"] = []
    module = ModuleType("google.cloud.bigquery")
    module.Client = FakeClient
    module.QueryJobConfig = FakeQueryJobConfig

    class Credentials:
        @staticmethod
        def from_service_account_info(info):
            FAKE_STATE["credentials"].append(("info", info))
            return "creds-from-info"

        @staticmethod
        def from_service_account_file(path):
            FAKE_STATE["credentials"].append(("file", path))
            return "creds-from-file"

    sa = ModuleType("google.oauth2.service_account")
    sa.Credentials = Credentials
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", module)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa)
    return FAKE_STATE


def profile(**kwargs) -> WarehouseProfile:
    base = {
        "name": "bq",
        "dialect": Dialect.BIGQUERY,
        "options": {"project": "acme-analytics", "location": "US", "dataset": "sales"},
        "maximum_bytes_billed": 5_000_000,
    }
    return WarehouseProfile(**(base | kwargs))


def client(state) -> FakeClient:
    return state["clients"][-1]


def test_explain_is_capped_dry_run_without_cache(bq):
    wh = BigQueryWarehouse(profile())
    wh.explain("SELECT 1")
    sql, config = client(bq).queries[-1]
    assert sql == "SELECT 1"
    assert config.dry_run is True and config.use_query_cache is False
    assert config.maximum_bytes_billed == 5_000_000
    assert config.default_dataset == "acme-analytics.sales"


def test_run_sets_bytes_cap_and_limits_rows(bq):
    wh = BigQueryWarehouse(profile(max_rows=3))
    c = client(bq)
    c.rows = [FakeRow((i, f"r{i}")) for i in range(10)]
    c.result_schema = [field("n", "INTEGER"), field("label", "STRING")]
    result = wh.run("SELECT n, label FROM t", limit=50)
    assert result.rows == [(0, "r0"), (1, "r1"), (2, "r2")]
    assert result.truncated is True and result.columns == ["n", "label"]
    assert c.max_results == [4]
    config = c.queries[-1][1]
    assert config.maximum_bytes_billed == 5_000_000 and config.dry_run is False


def test_run_not_truncated_when_rows_fit(bq):
    wh = BigQueryWarehouse(profile())
    client(bq).rows = [FakeRow((1,))]
    result = wh.run("SELECT 1", limit=5)
    assert result.rows == [(1,)] and result.truncated is False


def test_default_bytes_cap_when_profile_has_none(bq):
    wh = BigQueryWarehouse(profile(maximum_bytes_billed=None))
    wh.explain("SELECT 1")
    wh.run("SELECT 1", limit=1)
    caps = [config.maximum_bytes_billed for _, config in client(bq).queries]
    assert caps == [DEFAULT_MAXIMUM_BYTES_BILLED, DEFAULT_MAXIMUM_BYTES_BILLED]
    assert DEFAULT_MAXIMUM_BYTES_BILLED == 10 * 1024**3


def test_column_types_from_table_schema(bq):
    wh = BigQueryWarehouse(profile())
    c = client(bq)
    c.tables["sales.deals"] = [
        field("Deal_ID", "INTEGER"),
        field("closed_at", "TIMESTAMP"),
        field("tags", "STRING", mode="REPEATED"),
    ]
    assert wh.column_types("deals") == {
        "deal_id": "INTEGER",
        "closed_at": "TIMESTAMP",
        "tags": "ARRAY<STRING>",
    }
    assert wh.column_types("other.deals") == {}
    assert c.table_requests == ["sales.deals", "other.deals"]
    assert c.queries == []  # metadata API only, no SQL


def test_column_types_without_dataset_is_empty(bq):
    wh = BigQueryWarehouse(profile(options={"project": "p"}))
    assert wh.column_types("deals") == {}
    assert client(bq).table_requests == []


def test_credentials_json_from_env_and_scrubbed(monkeypatch, bq):
    key = {"type": "service_account", "private_key": PRIVATE_KEY, "private_key_id": "kid123456"}
    monkeypatch.setenv("BQ_KEY_JSON", json.dumps(key))
    wh = BigQueryWarehouse(profile(secrets_env={"credentials_json": "BQ_KEY_JSON"}))
    assert bq["credentials"] == [("info", key)]
    assert client(bq).credentials == "creds-from-info"
    client(bq).fail_with = RuntimeError(
        f"400 POST https://bigquery.googleapis.com/x: bad key {PRIVATE_KEY} kid123456"
    )
    with pytest.raises(WarehouseError) as err:
        wh.run("SELECT 1", limit=1)
    message = str(err.value)
    assert PRIVATE_KEY not in message and "kid123456" not in message
    assert "googleapis.com" not in message
    assert message.startswith("Query failed: 400 POST ***")


def test_credentials_file_path_from_env(monkeypatch, bq):
    monkeypatch.setenv("BQ_KEY_PATH", "/secure/key.json")
    BigQueryWarehouse(profile(secrets_env={"credentials_file": "BQ_KEY_PATH"}))
    assert bq["credentials"] == [("file", "/secure/key.json")]


def test_missing_credentials_env_var_named(monkeypatch, bq):
    monkeypatch.delenv("BQ_KEY_JSON", raising=False)
    with pytest.raises(WarehouseError, match="BQ_KEY_JSON"):
        BigQueryWarehouse(profile(secrets_env={"credentials_json": "BQ_KEY_JSON"}))
    assert bq["clients"] == []


def test_dry_run_error_becomes_warehouse_error(bq):
    wh = BigQueryWarehouse(profile())
    client(bq).fail_with = RuntimeError("Syntax error: Unexpected end of script")
    with pytest.raises(WarehouseError, match="does not compile: Syntax error"):
        wh.explain("SELECT")


def test_close_and_dialect(bq):
    wh = BigQueryWarehouse(profile())
    wh.close()
    assert client(bq).closed is True
    assert wh.dialect is Dialect.BIGQUERY


def test_missing_driver_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", None)
    with pytest.raises(WarehouseError, match=r"cteforge\[bigquery\]"):
        BigQueryWarehouse(profile())


# --- statement timeout ----------------------------------------------------------------


def test_run_waits_at_most_the_timeout_and_sets_job_timeout(bq):
    wh = BigQueryWarehouse(profile(statement_timeout_s=7))
    wh.run("SELECT 1", limit=10)
    c = client(bq)
    assert c.timeouts == [7]
    assert c.queries[0][1].job_timeout_ms == 7000


def test_run_timeout_cancels_job_and_raises_timed_out(bq):
    wh = BigQueryWarehouse(profile(statement_timeout_s=3))
    c = client(bq)
    c.result_error = TimeoutError()
    with pytest.raises(WarehouseError, match=r"^query timed out after 3s$"):
        wh.run("SELECT 1", limit=10)
    assert c.jobs[0].cancelled is True


def test_no_timeout_when_profile_turns_it_off(bq):
    wh = BigQueryWarehouse(profile(statement_timeout_s=None))
    wh.run("SELECT 1", limit=10)
    c = client(bq)
    assert c.timeouts == [None]
    assert c.queries[0][1].job_timeout_ms is None
