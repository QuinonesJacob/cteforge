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
    assert len(_token(boot.output)) >= 40

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

    rotated = cli("user", "token", "bob")
    assert rotated.exit_code == 0
    assert _token(rotated.output) != _token(bob.output)


def test_user_errors(cli: Invoke):
    assert cli("user", "add", "admin", "--bootstrap").exit_code == 0
    no_role = cli("user", "add", "bob")
    assert no_role.exit_code == 2 and "--role is required" in no_role.output
    bad_role = cli("user", "add", "bob", "--role", "king")
    assert bad_role.exit_code == 2
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
    own = cli("user", "token", "vic", user="vic")  # Anyone may rotate their own token.
    assert own.exit_code == 0


def test_settings_show_and_mode(cli: Invoke):
    show = cli("settings", "show")  # First local use creates the local admin.
    assert show.exit_code == 0, show.output
    assert "mode: open" in show.output
    changed = cli("settings", "mode", "strict")
    assert changed.exit_code == 0 and "mode: strict" in changed.output
    as_json = cli("settings", "show", "--json")
    assert json.loads(as_json.stdout) == {"mode": "strict"}
    assert cli("settings", "mode", "lenient").exit_code == 2
