"""Tool catalog, roles, tokens and users (ADR 0027 addendum)."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from cteforge.domain.access import (
    ADMIN_ONLY_TOOLS,
    ALL_TOOLS,
    BUILTIN_ROLES,
    TOOL_GROUPS,
    Role,
    TokenInfo,
    Tool,
    ToolGroup,
    ToolMode,
    sorted_tools,
    tool_group,
)
from cteforge.domain.users import ActorKind, User, UserRole

T0 = dt.datetime(2026, 11, 17, tzinfo=dt.UTC)


def test_catalog_names_match_the_addendum() -> None:
    assert [t.value for t in TOOL_GROUPS[ToolGroup.USE]] == [
        "search",
        "get_definition",
        "compile_and_run",
        "check_and_run",
        "describe_tables",
        "list_joins",
        "list_recipes",
    ]
    assert [t.value for t in TOOL_GROUPS[ToolGroup.BUILD]] == [
        "parse_query",
        "propose_definition",
        "revise_definition",
        "propose_join",
        "propose_recipe",
    ]
    assert [t.value for t in TOOL_GROUPS[ToolGroup.REVIEW]] == [
        "list_proposals",
        "review_proposal",
        "verify",
    ]
    assert [t.value for t in TOOL_GROUPS[ToolGroup.ADMIN]] == [
        "view_audit",
        "list_users",
        "manage_user",
        "manage_role",
        "manage_settings",
    ]


def test_every_tool_is_in_exactly_one_group() -> None:
    grouped = [t for tools in TOOL_GROUPS.values() for t in tools]
    assert sorted(grouped) == sorted(Tool) and len(grouped) == len(set(grouped))
    assert tool_group(Tool.VERIFY) is ToolGroup.REVIEW
    assert ADMIN_ONLY_TOOLS <= set(TOOL_GROUPS[ToolGroup.ADMIN])
    assert Tool.VIEW_AUDIT not in ADMIN_ONLY_TOOLS  # approvers read the audit log
    assert ALL_TOOLS == frozenset(Tool)


def test_sorted_tools_uses_catalog_order() -> None:
    assert sorted_tools({Tool.VERIFY, Tool.SEARCH, Tool.PARSE_QUERY}) == [
        Tool.SEARCH,
        Tool.PARSE_QUERY,
        Tool.VERIFY,
    ]


def test_role_validation() -> None:
    role = Role(name="support_bot", allowed=frozenset({Tool.SEARCH}))
    assert role.defaults == frozenset() and role.builtin is False
    with pytest.raises(ValidationError, match="defaults"):
        Role(name="x", allowed=frozenset({Tool.SEARCH}), defaults=frozenset({Tool.VERIFY}))
    for bad in ("Support", "a b", "1x", "x__y", "x" * 33):
        with pytest.raises(ValidationError):
            Role(name=bad)


def test_builtin_names_are_the_userrole_values() -> None:
    assert list(BUILTIN_ROLES) == [r.value for r in UserRole]
    assert BUILTIN_ROLES["admin"].allowed == ALL_TOOLS


def test_token_info_usable_at() -> None:
    info = TokenInfo(
        id="t",
        user_name="u",
        name="n",
        prefix="cfg_pat_abcd",
        created_at=T0,
        created_by="u",
        expires_at=T0 + dt.timedelta(days=1),
    )
    assert info.tool_mode is ToolMode.FOLLOW_ROLE
    assert info.usable_at(T0) and not info.usable_at(T0 + dt.timedelta(days=1))
    assert not info.model_copy(update={"revoked_at": T0}).usable_at(T0)
    with pytest.raises(ValidationError):
        TokenInfo(id="t", user_name="u", name="", prefix="", created_at=T0, created_by="u")
    assert not {"token", "token_sha256", "hash"} & set(TokenInfo.model_fields)


def test_user_role_is_a_name_and_access_is_optional() -> None:
    user = User(name="jq", role=UserRole.APPROVER)
    assert user.role == "approver" and type(user.role) is str
    assert user.active is True and user.tools is None and user.review_all is None
    custom = User(name="bot", role="support_bot", kind=ActorKind.AGENT)
    assert custom.is_agent and not user.is_agent
    dumped = User(name="a", role="viewer", tools=frozenset({Tool.SEARCH})).model_dump(mode="json")
    assert dumped["tools"] == ["search"]
