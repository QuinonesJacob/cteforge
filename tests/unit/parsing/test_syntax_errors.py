"""Readable syntax errors from parse(): position plus the likely cause."""

from __future__ import annotations

import pytest

from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import SqlParseError
from cteforge.parsing.analyze import parse

SF = Dialect.SNOWFLAKE


def _message(sql: str, dialect: Dialect = SF) -> str:
    with pytest.raises(SqlParseError) as info:
        parse(sql, dialect)
    return str(info.value)


def test_missing_closing_quote_points_at_the_string_that_runs_onto_the_next_line() -> None:
    sql = (
        "select c.id\nfrom customers as c\n"
        "where date(c.created_at) >= '2026-01-01\n"
        "and date(c.created_at) <= '2026-11-01'"
    )
    msg = _message(sql)
    assert msg.startswith("SQL syntax error at line 3, column 29:")
    assert "quote (') opened here is never closed" in msg
    assert "`'2026-01-01`" in msg
    assert "tokenizing" not in msg.lower()


def test_unclosed_quote_at_the_end() -> None:
    msg = _message("select a from t where x = 'ab")
    assert msg.startswith("SQL syntax error at line 1, column 27:")


@pytest.mark.parametrize(
    ("sql", "what", "col"),
    [
        ("select a /* hi from t", "comment (/*)", 10),
        ('select "a from t', 'double quote (")', 8),
    ],
)
def test_unclosed_comment_and_identifier(sql: str, what: str, col: int) -> None:
    msg = _message(sql)
    assert msg.startswith(f"SQL syntax error at line 1, column {col}: the {what}")


def test_escaped_quotes_and_line_comments_are_not_openers() -> None:
    msg = _message("select 'it''s' as a, -- don't\n 'x from t", Dialect.DUCKDB)
    assert msg.startswith("SQL syntax error at line 2, column 2:")


def test_redshift_backslash_escaped_quote_is_not_an_opener() -> None:
    # Redshift (like sqlglot's redshift tokenizer) reads \\' inside a string as a quote.
    msg = _message("select 'it\\'s' as a,\n 'x from t", Dialect.REDSHIFT)
    assert msg.startswith("SQL syntax error at line 2, column 2:")


def test_missing_comma_names_the_token_and_common_causes() -> None:
    msg = _message("select\nc.id\nc.name\nfrom customers as c")
    assert msg.startswith("SQL syntax error at line 3, column 2: unexpected `.`")
    assert "missing comma" in msg
    assert "Read as snowflake" in msg


def test_unclosed_parenthesis() -> None:
    msg = _message("select (a + b from t")
    assert "a '(' is never closed; expected ')' before `from`" in msg


def test_statement_count() -> None:
    assert _message("   ") == "No SQL statement found."
    assert _message("select 1; select 2").startswith("Found 2 SQL statements")
