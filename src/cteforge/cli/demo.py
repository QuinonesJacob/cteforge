"""`demo [DIR]`: one command to a working local product (synthetic data only).

Glue, not business logic: every library change goes through services (bootstrap_admin,
create_user, propose_definition). What lives here is only file plumbing for the demo
directory: seed the example DuckDB warehouse (examples/seed_warehouse.py, run by path),
write connections.yml and env.sh, and read the example queries + annotation files.
"""

from __future__ import annotations

import json
import runpy
import shlex
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from cteforge.cli._common import app, context, echo_issues, fail, warn
from cteforge.domain.dialects import Dialect
from cteforge.domain.users import ActorKind, UserRole
from cteforge.services.admin import bootstrap_admin, create_user
from cteforge.services.context import AppConfig
from cteforge.services.propose import propose_definition

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "examples"
PROFILE = "demo"
DB_FILE = "cteforge.db"
WAREHOUSE_FILE = "warehouse.duckdb"
CONNECTIONS_FILE = "connections.yml"
ENV_FILE = "env.sh"

# (annotation file stem in examples/definitions, query file stem in examples/queries)
EXAMPLE_DEFINITIONS = [
    ("realization_rate", "realization_rate"),
    ("booked_vs_collected", "booked_vs_collected"),
    ("realization_rate_grain", "realization_rate"),
]

# Output columns other definitions may join on (DESIGN §17), per example definition id. A
# final SELECT joining two demo definitions on these keys is labeled from their states
# (built_from_verified_parts / llm_annotated) instead of exploratory. An annotation file
# that declares its own `join_keys` wins.
DEMO_JOIN_KEYS: dict[str, list[str]] = {
    "realization_rate": ["month", "region"],
    "realization_rate_by_grain": ["month", "region"],
    "booked_vs_collected": ["region"],
}

CONNECTIONS_YML = f"""\
# Written by `cteforge demo`. Synthetic data only. See examples/connections.example.yml.
profiles:
  {PROFILE}:
    dialect: duckdb
    options:
      path: {WAREHOUSE_FILE}   # relative to this file
    max_rows: 10000
"""


def _env(directory: Path, admin: str) -> dict[str, str]:
    return {
        "CTEFORGE_DB_URL": f"sqlite:///{directory / DB_FILE}",
        "CTEFORGE_CONNECTIONS": str(directory / CONNECTIONS_FILE),
        "CTEFORGE_DEFAULT_PROFILE": PROFILE,
        "CTEFORGE_USER": admin,
    }


def _prepare(directory: Path, examples: Path, force: bool) -> None:
    seed_script = examples / "seed_warehouse.py"
    if not seed_script.is_file():
        fail(f"examples not found at {examples} (run the demo from a cteforge checkout)")
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / DB_FILE).exists():
        if not force:
            fail(f"{directory / DB_FILE} already exists; pass --force to start over")
        (directory / DB_FILE).unlink()
    seed = runpy.run_path(str(seed_script))["seed"]
    seed(directory / WAREHOUSE_FILE)
    (directory / CONNECTIONS_FILE).write_text(CONNECTIONS_YML, encoding="utf-8")


def _example(examples: Path, annotations: str, query: str) -> tuple[str, dict[str, Any]]:
    sql = (examples / "queries" / f"{query}.sql").read_text(encoding="utf-8")
    text = (examples / "definitions" / f"{annotations}.annotations.yml").read_text("utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        fail(f"{annotations}.annotations.yml is not a YAML mapping")
    return sql, data


def _mcp_snippet(env: dict[str, str], agent: str) -> str:
    server_env = {k: v for k, v in env.items() if k != "CTEFORGE_USER"}
    server_env["CTEFORGE_USER"] = agent
    config = {
        "mcpServers": {
            "cteforge": {
                "command": "uv",
                "args": ["run", "--directory", str(REPO), "cteforge", "mcp"],
                "env": server_env,
            }
        }
    }
    return json.dumps(config, indent=2)


@app.command()
def demo(
    directory: Annotated[str, typer.Argument(help="Where to put the demo")] = "./cteforge-demo",
    admin: Annotated[str, typer.Option("--admin", help="Admin user name")] = "admin",
    agent: Annotated[str, typer.Option("--agent", help="Agent user name")] = "claude-agent",
    examples: Annotated[
        str | None, typer.Option("--examples", help="examples/ directory (default: repo's)")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing demo db")] = False,
) -> None:
    """Seed a DuckDB warehouse, init the db, create an admin and an agent, and propose the
    example definitions (they auto-apply in open mode). Prints tokens and next steps."""
    target = Path(directory).expanduser().resolve()
    source = Path(examples).expanduser().resolve() if examples else EXAMPLES
    _prepare(target, source, force)
    typer.echo(f"warehouse: {target / WAREHOUSE_FILE} (synthetic sales data)")
    env = _env(target, admin)
    config = AppConfig(
        db_url=env["CTEFORGE_DB_URL"],
        connections_path=target / CONNECTIONS_FILE,
        default_profile=PROFILE,
        user=admin,
    )
    with context(config) as ctx:
        admin_user, admin_token = bootstrap_admin(ctx, admin)
        agent_user, agent_token = create_user(
            ctx, admin_user, agent, UserRole.CONTRIBUTOR, ActorKind.AGENT
        )
        typer.echo(f"database: {config.display_db_url}")
        typer.echo(f"users: {admin_user.name} (human admin), {agent_user.name} (agent contributor)")
        typer.echo("\nproposing example definitions:")
        for annotations, query in EXAMPLE_DEFINITIONS:
            sql, data = _example(source, annotations, query)
            metadata = {k: v for k, v in data.items() if k != "variables"}
            if "join_keys" not in metadata and metadata.get("id") in DEMO_JOIN_KEYS:
                metadata["join_keys"] = DEMO_JOIN_KEYS[metadata["id"]]
            outcome = propose_definition(
                ctx,
                admin_user,
                sql=sql,
                dialect=Dialect.DUCKDB,
                metadata=metadata,
                annotations=data.get("variables") or [],
                profile=PROFILE,
                summary=f"Demo: examples/queries/{query}.sql",
            )
            card = outcome.definition
            status = f"{card.id} v{card.version} {card.state}" if card else "pending review"
            applied = "auto-applied" if outcome.auto_applied else str(outcome.proposal.status)
            typer.echo(f"  {outcome.proposal.target_id}: {applied} ({status})")
            echo_issues(outcome.issues)

    env_lines = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items())
    (target / ENV_FILE).write_text(
        "# Written by `cteforge demo`. No secrets: tokens are never written to disk.\n" + env_lines,
        encoding="utf-8",
    )
    typer.echo("")
    warn("Tokens are shown ONCE (stored only as hashes). Keep them secret.")
    typer.echo(f"admin token ({admin}, for the GUI / HTTP API): {admin_token}")
    typer.echo(f"agent token ({agent}, for MCP over HTTP):      {agent_token}")
    typer.echo(
        f"""
next steps:
  source {shlex.quote(str(target / ENV_FILE))}
  cteforge search realization
  cteforge run --use rr=realization_rate \\
      --vars '{{"rr": {{"period": {{"start": "2025-07-01", "end": "2025-10-01"}}}}}}'
  cteforge verify realization_rate 1        # a person verifies (agents only as admin)
  cteforge serve                            # GUI + API at http://127.0.0.1:8765, MCP at /mcp

MCP (stdio) for Claude Code (.mcp.json), acting as the agent user:
{_mcp_snippet(env, agent)}"""
    )
