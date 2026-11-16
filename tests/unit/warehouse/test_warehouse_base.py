import sys

import pytest

from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import WarehouseError
from cteforge.warehouse.base import (
    Sensitive,
    columns_query,
    connect_kwargs,
    effective_limit,
    load_driver,
    reject_plaintext_secrets,
    resolve_secrets,
    scrub,
    sensitive_values,
    split_table,
)


def _profile(**kwargs) -> WarehouseProfile:
    return WarehouseProfile(name="p", dialect=Dialect.POSTGRES, **kwargs)


def test_scrub_removes_secret_values_urls_and_key_values():
    message = (
        "auth failed for s3cr3t-pw at postgresql://bob:pw@db.internal:5432/x "
        "(password=other123 token: abc)"
    )
    out = scrub(message, ["s3cr3t-pw"])
    assert "s3cr3t-pw" not in out
    assert "db.internal" not in out and "bob:pw" not in out
    assert "other123" not in out and "abc" not in out
    assert out.startswith("auth failed for ***")


def test_scrub_ignores_empty_secret():
    assert scrub("plain message", [""]) == "plain message"


def test_split_table_pads_missing_parts():
    assert split_table("t") == (None, None, "t")
    assert split_table("s.t") == (None, "s", "t")
    assert split_table(" c.s.t ") == ("c", "s", "t")


@pytest.mark.parametrize("bad", ["", "a..b", "a.b.c.d", ".t"])
def test_split_table_rejects_malformed(bad):
    assert split_table(bad) is None


def test_effective_limit_caps_and_floors():
    assert effective_limit(50, 10) == 10
    assert effective_limit(5, 10) == 5
    assert effective_limit(-3, 10) == 0


def test_columns_query_keeps_identifiers_out_of_sql():
    sql, params = columns_query(("Cat", "Sch", "Tbl"), "?", "current_schema()")
    assert params == ["Tbl", "Sch", "Cat"]
    assert sql.count("?") == 3
    assert "Tbl" not in sql and "Sch" not in sql and "Cat" not in sql


def test_columns_query_unqualified_uses_current_schema():
    sql, params = columns_query((None, None, "t"), "%s", "CURRENT_SCHEMA()")
    assert params == ["t"]
    assert "table_schema = CURRENT_SCHEMA()" in sql


def test_resolve_secrets_reads_named_env_var(monkeypatch):
    monkeypatch.setenv("X_PW", "value-1")
    assert resolve_secrets(_profile(secrets_env={"password": "X_PW"})) == {"password": "value-1"}


def test_resolve_secrets_missing_var_names_var_not_values(monkeypatch):
    monkeypatch.setenv("X_PW", "value-1")
    monkeypatch.delenv("X_TOKEN", raising=False)
    profile = _profile(secrets_env={"password": "X_PW", "token": "X_TOKEN"})
    with pytest.raises(WarehouseError) as err:
        resolve_secrets(profile)
    assert "X_TOKEN" in str(err.value)
    assert "value-1" not in str(err.value)


def test_connect_kwargs_rejects_secret_also_given_as_option():
    with pytest.raises(WarehouseError, match="password"):
        connect_kwargs(_profile(options={"password": "x"}), {"password": "y"})


def test_sensitive_values_include_all_connection_details():
    options = {"host": "db.internal", "user": "ab", "port": 5432, "dbname": "prod", "sslmode": "x"}
    sensitive = sensitive_values(_profile(options=options), {"password": "pw"})
    assert sensitive == Sensitive(secrets=("pw",), details=("db.internal", "ab", "5432", "prod"))


def test_scrub_removes_short_details_as_whole_words_only():
    message = "password authentication failed for ro at db; error in role ro"
    out = scrub(message, [], ["ro", "db"])
    assert out == "password authentication failed for *** at ***; error in role ***"


def test_scrub_removes_quoted_identity_ips_and_ports():
    message = (
        'auth failed for user "ro"; database "proddb"; '
        "connection to server at \"10.1.2.3\", port 5432 failed; hostaddr: '::1'; "
        "failed to resolve host 'db.corp'; via fe80::1ff:fe23:4567:890a"
    )
    out = scrub(message, [])
    for leaked in ("ro", "proddb", "10.1.2", "5432", "::1", "db.corp", "fe80"):
        assert leaked not in out
    assert out.startswith("auth failed for user ***; database ***; connection to server")


def test_scrub_keeps_sql_casts_and_times():
    message = "cannot cast '12:30:45'::time to x::date at LINE 3"
    assert scrub(message, []) == message


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "sslpassword",
        "access_token",
        "token",
        "private_key_file_pwd",
        "client_secret",
        "passcode",
        "conninfo",
        "dsn",
        "credentials_json",
    ],
)
def test_reject_plaintext_secrets_names_key_not_value(key):
    profile = _profile(options={"host": "h", key: "plain-value-1"})
    with pytest.raises(WarehouseError, match=key) as err:
        reject_plaintext_secrets(profile)
    assert "plain-value-1" not in str(err.value)


def test_reject_plaintext_secrets_allows_ordinary_options():
    options = {"host": "h", "port": 5432, "user": "u", "sslmode": "require", "role": "r"}
    reject_plaintext_secrets(_profile(options=options))


def test_resolve_secrets_refuses_plaintext_secret_option(monkeypatch):
    monkeypatch.setenv("X_PW", "value-1")
    profile = _profile(options={"token": "abc"}, secrets_env={"password": "X_PW"})
    with pytest.raises(WarehouseError, match="secrets_env"):
        resolve_secrets(profile)


def test_load_driver_missing_names_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "fake_missing_driver", None)
    with pytest.raises(WarehouseError, match=r"cteforge\[thing\]"):
        load_driver("fake_missing_driver", "thing")
