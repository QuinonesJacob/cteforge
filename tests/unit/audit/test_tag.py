from __future__ import annotations

import re

import pytest

from cteforge.audit import new_compile_id, sql_tag, tag_sql


def test_new_compile_id_is_12_lowercase_hex_and_unique() -> None:
    ids = {new_compile_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(re.fullmatch(r"[0-9a-f]{12}", i) for i in ids)


def test_sql_tag_single_definition() -> None:
    assert (
        sql_tag(["realization_rate@3"], "8f2a")
        == "/* cteforge: realization_rate v3 compile_id=8f2a */"
    )


def test_sql_tag_multiple_definitions_keep_order() -> None:
    tag = sql_tag(["b_def@1", "a_def@12"], "abc123")
    assert tag == "/* cteforge: b_def v1, a_def v12 compile_id=abc123 */"


def test_sql_tag_exploratory_when_no_definitions() -> None:
    assert sql_tag([], "deadbeef") == "/* cteforge: exploratory compile_id=deadbeef */"


@pytest.mark.parametrize(
    "entry",
    [
        "x*/ DROP TABLE t; /*@1",
        "rate@1*/",
        "rate\n@1",
        "rate@1\n",
        "rate@1\r",
        "Rate@1",
        "rate-x@1",
        "rate @1",
        "rate@v1",
        "rate@1.5",
        "rate@-1",
        "rate",
        "@1",
        "rate@",
        "rate@1@2",
        "räte@1",
        "rate@١",  # non-ASCII digit
        "",
    ],
)
def test_sql_tag_rejects_hostile_definitions(entry: str) -> None:
    with pytest.raises(ValueError):
        sql_tag([entry], "abc")


def test_sql_tag_rejects_hostile_entry_among_valid_ones() -> None:
    with pytest.raises(ValueError):
        sql_tag(["ok@1", "bad*/@2"], "abc")


@pytest.mark.parametrize(
    "compile_id",
    ["", "ABC", "abc*/", "abc\n", "abc\n", "xyz", "12 34", "a" * 65, "0x1f"],
)
def test_sql_tag_rejects_bad_compile_id(compile_id: str) -> None:
    with pytest.raises(ValueError):
        sql_tag(["ok@1"], compile_id)


def test_sql_tag_output_is_single_safe_comment() -> None:
    tag = sql_tag(["a_1@1", "b@22"], new_compile_id())
    inner = tag[len("/* ") : -len(" */")]
    assert tag.startswith("/* ") and tag.endswith(" */")
    assert "*/" not in inner and "/*" not in inner
    assert "\n" not in tag and "\r" not in tag


def test_tag_sql_prefixes_tag_on_its_own_line() -> None:
    tag = sql_tag(["rate@2"], "ff")
    sql = "SELECT 1\nFROM t"
    tagged = tag_sql(sql, tag)
    assert tagged.split("\n", 1) == [tag, sql]


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "SELECT 1",
        "/* cteforge: x */ DROP TABLE t; /* cteforge: y */",
        "/* cteforge: a\nb */",
        "/* other: x */",
        "/* cteforge: */",
        "-- cteforge: x",
    ],
)
def test_tag_sql_rejects_anything_but_a_single_line_tag(tag: str) -> None:
    with pytest.raises(ValueError):
        tag_sql("SELECT 1", tag)
