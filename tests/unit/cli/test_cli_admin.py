"""db / user / settings commands: happy paths and exit codes (1 error, 2 bad input)."""

from __future__ import annotations

import json
from collections.abc import Callable

from click.testing import Result

Invoke = Callable[..., Result]


def _token(output: str) -> str:
    """The token line printed after the shown-once warning."""
    lines = output.splitlines()
    index = next(i for i, line in enumerate(lines) if "shown ONCE" in line)
    return lines[index + 1].strip()


def test_db_init_creates_the_database_and_is_idempotent(cli: Invoke):
    first = cli("db", "init")
    assert first.exit_code == 0, first.output
    assert "database ready: sqlite:///" in first.output
    assert "warehouse profiles: (none)" in first.output
    assert cli("db", "init").exit_code == 0


def test_db_init_bad_auth_setting_exits_1(cli: Invoke):
    result = cli("db", "init", CTEFORGE_AUTH="maybe")
    assert result.exit_code == 1
    assert "CTEFORGE_AUTH" in result.output


def test_bootstrap_then_manage_users(cli: Invoke):
    boot = cli("user", "add", "admin", "--bootstrap")
    assert boot.exit_code == 0, boot.output
    assert "created human user 'admin' with role admin" in boot.output
    assert _token(boot.output).startswith("cfg_pat_")

    again = cli("user", "add", "other", "--bootstrap")
    assert again.exit_code == 1
    assert "Users already exist" in again.output

    bob = cli("user", "add", "bob", "--role", "viewer")
    assert bob.exit_code == 0, bob.output
    bot = cli("user", "add", "bot", "--role", "contributor", "--agent")
    assert bot.exit_code == 0, bot.output
    assert "created agent user 'bot' with role contributor" in bot.output
    assert _token(bob.output) != _token(bot.output)

    listed = cli("user", "list", "--json")
    assert listed.exit_code == 0, listed.output
    users = {u["name"]: u for u in json.loads(listed.stdout)}
    assert users["bob"]["role"] == "viewer"
    assert users["bot"]["kind"] == "agent"

    text = cli("user", "list")
    assert "bob" in text.output and "NAME" in text.output

    role = cli("user", "role", "bob", "approver")
    assert role.exit_code == 0 and "'bob' is now approver" in role.output

    minted = cli("token", "create", "laptop", "--user", "bob")
    assert minted.exit_code == 0, minted.output
    assert _token(minted.output) != _token(bob.output)
    assert "for 'bob'" in minted.output


def test_user_errors(cli: Invoke):
    assert cli("user", "add", "admin", "--bootstrap").exit_code == 0
    no_role = cli("user", "add", "bob")
    assert no_role.exit_code == 2 and "--role is required" in no_role.output
    bad_role = cli("user", "add", "bob", "--role", "king")
    assert bad_role.exit_code == 1 and "role must be one of" in bad_role.output
    boot_role = cli("user", "add", "x", "--bootstrap", "--role", "viewer")
    assert boot_role.exit_code == 2
    missing = cli("user", "role", "nobody", "viewer")
    assert missing.exit_code == 1 and "does not exist" in missing.output
    last_admin = cli("user", "role", "admin", "viewer")
    assert last_admin.exit_code == 1 and "last admin" in last_admin.output
    bad_name = cli("user", "add", "-bad name", "--role", "viewer")
    assert bad_name.exit_code in (1, 2)
    unknown_local = cli("user", "list", user="ghost")
    assert unknown_local.exit_code == 1 and "does not exist" in unknown_local.output


def test_non_admin_is_refused(cli: Invoke):
    assert cli("user", "add", "admin", "--bootstrap").exit_code == 0
    assert cli("user", "add", "vic", "--role", "viewer").exit_code == 0
    refused = cli("user", "list", user="vic")
    assert refused.exit_code == 1 and "may not" in refused.output
    assert cli("settings", "mode", "strict", user="vic").exit_code == 1
    own = cli("token", "create", "mine", user="vic")  # Anyone may generate their own.
    assert own.exit_code == 0 and _token(own.output).startswith("cfg_pat_")
    other = cli("token", "create", "x", "--user", "admin", user="vic")
    assert other.exit_code == 1 and "may not" in other.output
    assert cli("role", "list", user="vic").exit_code == 1


def test_settings_show_and_mode(cli: Invoke):
    show = cli("settings", "show")  # First local use creates the local admin.
    assert show.exit_code == 0, show.output
    assert "mode: open" in show.output
    changed = cli("settings", "mode", "strict")
    assert changed.exit_code == 0 and "mode: strict" in changed.output
    as_json = cli("settings", "show", "--json")
    assert json.loads(as_json.stdout) == {
        "mode": "strict",
        "default_connection": None,
        "effective_default_connection": None,  # no profiles configured
        "issues": [],
    }
    assert cli("settings", "mode", "lenient").exit_code == 2


def test_settings_connection_set_show_and_clear(fresh_demo: Invoke):
    cli = fresh_demo
    shown = cli("settings", "connection")
    assert shown.exit_code == 0, shown.output
    assert "default connection: (not set)" in shown.output
    assert "effective default connection: demo" in shown.output  # CTEFORGE_DEFAULT_PROFILE
    set_ = cli("settings", "connection", "demo")
    assert set_.exit_code == 0 and "default connection: demo" in set_.output
    as_json = json.loads(cli("settings", "show", "--json").stdout)
    assert as_json["default_connection"] == "demo"
    missing = cli("settings", "connection", "nope")
    assert missing.exit_code == 1 and "'nope' is not configured" in missing.output
    assert cli("settings", "connection", "demo", "--clear").exit_code == 2
    cleared = cli("settings", "connection", "--clear")
    assert cleared.exit_code == 0 and "default connection: (not set)" in cleared.output
    assert cli("settings", "connection", "demo", user="vic").exit_code == 1  # unknown user


def test_token_commands(cli: Invoke):
    assert cli("user", "add", "admin", "--bootstrap").exit_code == 0
    assert cli("user", "add", "bot", "--role", "contributor", "--agent").exit_code == 0
    made = cli(
        "token",
        "create",
        "support",
        "--user",
        "bot",
        "--only",
        "search,get_definition",
        "--expires",
        "2099-01-01",
    )
    assert made.exit_code == 0, made.output
    token = _token(made.output)
    assert "search, get_definition" in made.output
    listed = cli("token", "list", "--user", "bot", "--json")
    tokens = {t["name"]: t for t in json.loads(listed.stdout)}
    assert set(tokens) == {"default", "support"}
    assert tokens["support"]["tool_mode"] == "only"
    assert tokens["support"]["expires_at"].startswith("2099-01-01")
    assert token not in listed.stdout
    text = cli("token", "list", "--user", "bot")
    assert "support" in text.output and token not in text.output
    revoked = cli("token", "revoke", tokens["support"]["id"], "--user", "bot")
    assert revoked.exit_code == 0 and "revoked token 'support'" in revoked.output
    again = json.loads(cli("token", "list", "--user", "bot", "--json").stdout)
    assert {t["name"]: t["state"] for t in again} == {"default": "active", "support": "revoked"}
    assert cli("token", "create", "x", "--only", "teleport").exit_code == 2
    assert cli("token", "create", "x", "--only", "search", "--exclude", "search").exit_code == 2
    assert cli("token", "create", "x", "--expires", "soon").exit_code == 2
    beyond = cli("token", "create", "x", "--user", "bot", "--include", "manage_user")
    assert beyond.exit_code == 1 and "does not allow" in beyond.output
    agent = cli("token", "create", "x", user="bot")
    assert agent.exit_code == 1 and "agents never see tokens" in agent.output


def test_role_commands_and_deactivate(cli: Invoke):
    assert cli("user", "add", "admin", "--bootstrap").exit_code == 0
    made = cli(
        "role",
        "create",
        "support_bot",
        "--allow",
        "search",
        "--allow",
        "compile_and_run",
        "--default",
        "search",
        "--description",
        "narrow",
    )
    assert made.exit_code == 0, made.output
    listed = json.loads(cli("role", "list", "--json").stdout)
    support = next(r for r in listed if r["name"] == "support_bot")
    assert support["allowed"] == ["search", "compile_and_run"] and support["defaults"] == ["search"]
    assert "support_bot" in cli("role", "list").output
    edited = cli("role", "edit", "support_bot", "--default", "search,compile_and_run")
    assert edited.exit_code == 0 and "on by default: search, compile_and_run" in edited.output
    assert cli("role", "edit", "admin", "--allow", "search").exit_code == 1
    assert cli("role", "create", "bad", "--allow", "manage_user").exit_code == 1
    assert cli("user", "add", "sb", "--role", "support_bot", "--agent").exit_code == 0
    held = cli("role", "delete", "support_bot")
    assert held.exit_code == 1 and "held by users" in held.output
    off = cli("user", "deactivate", "sb")
    assert off.exit_code == 0 and "deactivated" in off.output
    users = {u["name"]: u for u in json.loads(cli("user", "list", "--json").stdout)}
    assert users["sb"]["active"] is False
    assert cli("user", "activate", "sb").exit_code == 0
    assert cli("user", "role", "sb", "viewer").exit_code == 0
    assert cli("role", "delete", "support_bot").exit_code == 0
    last = cli("user", "deactivate", "admin")
    assert last.exit_code == 1 and "last admin" in last.output


def test_user_profile(cli: Invoke):
    """ADR 0029: `user profile NAME --display-name --email`; "" clears."""
    assert cli("user", "add", "admin", "--bootstrap").exit_code == 0
    assert cli("user", "add", "bob", "--role", "viewer").exit_code == 0
    done = cli("user", "profile", "bob", "--display-name", "Bob B", "--email", "bob@example.com")
    assert done.exit_code == 0, done.output
    assert "display name Bob B, email bob@example.com" in done.output
    users = {u["name"]: u for u in json.loads(cli("user", "list", "--json").stdout)}
    assert (users["bob"]["display_name"], users["bob"]["email"]) == ("Bob B", "bob@example.com")
    cleared = cli("user", "profile", "bob", "--email", "")
    assert cleared.exit_code == 0 and "email (none)" in cleared.output
    bad = cli("user", "profile", "bob", "--email", "not-an-email")
    assert bad.exit_code == 1 and "email" in bad.output
    assert cli("user", "profile", "bob").exit_code == 2
