"""BigQuery connector via google-cloud-bigquery (HARDENED module — read CLAUDE.md here before
editing). Install with ``pip install 'cteforge[bigquery]'``.

Profile ``options``: project, optional location and dataset (default dataset for
unqualified table names). Credentials, by env var: ``secrets_env: {credentials_file:
BQ_KEY_PATH}`` (path to a service-account key file) or ``{credentials_json: BQ_KEY_JSON}``
(the key itself). With neither, Application Default Credentials are used.

Read-only: use a service account with only ``roles/bigquery.dataViewer`` on the datasets
and ``roles/bigquery.jobUser`` on the project.

Every job sets ``maximum_bytes_billed``: the profile's value, else
DEFAULT_MAXIMUM_BYTES_BILLED (10 GiB). ``explain`` is a dry run (no cache, nothing billed).
Column types come from ``client.get_table(...).schema``, not from SQL text.
"""

from __future__ import annotations

import json
from typing import Any

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.base import (
    QueryResult,
    driver_error,
    effective_limit,
    load_driver,
    resolve_secrets,
    sensitive_values,
    split_table,
)

DEFAULT_MAXIMUM_BYTES_BILLED = 10 * 1024**3  # 10 GiB when the profile sets none.
_NOT_FOUND = 404


class BigQueryWarehouse:
    dialect: Dialect = Dialect.BIGQUERY

    def __init__(self, profile: WarehouseProfile) -> None:
        self._bq = load_driver("google.cloud.bigquery", "bigquery")
        secrets = resolve_secrets(profile)
        self._sensitive = sensitive_values(profile, secrets)
        self._max_rows = profile.max_rows
        self._max_bytes = profile.maximum_bytes_billed or DEFAULT_MAXIMUM_BYTES_BILLED
        dataset = profile.options.get("dataset")
        self._dataset = dataset if isinstance(dataset, str) and dataset else None
        try:
            credentials = self._credentials(secrets)
            self._client = self._bq.Client(
                project=profile.options.get("project"),
                location=profile.options.get("location"),
                credentials=credentials,
            )
        except WarehouseError:
            raise
        except Exception as exc:
            raise self._error("Could not connect to BigQuery", exc) from None

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as exc:
            raise self._error("Could not close connection", exc) from None

    def column_types(self, table: str) -> dict[str, str]:
        parts = split_table(table)
        if parts is None:
            return {}
        project, dataset, name = parts
        dataset = dataset or self._dataset
        if dataset is None:
            return {}
        table_id = ".".join(p for p in (project, dataset, name) if p)
        try:
            schema = self._client.get_table(table_id).schema
        except Exception as exc:
            if getattr(exc, "code", None) == _NOT_FOUND or isinstance(exc, ValueError):
                return {}
            raise self._error("Schema lookup failed", exc) from None
        return {field.name.lower(): _field_type(field) for field in schema}

    def explain(self, sql: str) -> None:
        config = self._job_config(dry_run=True, use_query_cache=False)
        try:
            self._client.query(sql, job_config=config)
        except Exception as exc:
            raise self._error("Query does not compile", exc) from None

    def run(self, sql: str, limit: int) -> QueryResult:
        limit = effective_limit(limit, self._max_rows)
        try:
            job = self._client.query(sql, job_config=self._job_config())
            iterator = job.result(max_results=limit + 1)
            rows = [tuple(row.values()) for row in iterator]
            columns = [field.name for field in iterator.schema or []]
        except Exception as exc:
            raise self._error("Query failed", exc) from None
        return QueryResult(columns=columns, rows=rows[:limit], truncated=len(rows) > limit)

    def _job_config(self, **kwargs: Any) -> Any:
        config = self._bq.QueryJobConfig(maximum_bytes_billed=self._max_bytes, **kwargs)
        if self._dataset is not None:
            qualified = "." in self._dataset
            config.default_dataset = (
                self._dataset if qualified else f"{self._client.project}.{self._dataset}"
            )
        return config

    def _credentials(self, secrets: dict[str, str]) -> Any:
        if not ({"credentials_file", "credentials_json"} & set(secrets)):
            return None
        service_account = load_driver("google.oauth2.service_account", "bigquery")
        accounts = service_account.Credentials
        if "credentials_json" in secrets:
            info = json.loads(secrets["credentials_json"])
            keys = ("private_key", "private_key_id")
            self._sensitive = self._sensitive.with_secrets(str(info.get(k, "")) for k in keys)
            return accounts.from_service_account_info(info)
        return accounts.from_service_account_file(secrets["credentials_file"])

    def _error(self, context: str, exc: BaseException) -> WarehouseError:
        return driver_error(context, exc, self._sensitive)


def _field_type(field: Any) -> str:
    kind = str(field.field_type)
    return f"ARRAY<{kind}>" if getattr(field, "mode", None) == "REPEATED" else kind
