"""CLI test fixtures: a CliRunner bound to an isolated environment (tmp db, connections).

`cli` runs commands with CTEFORGE_* pointed at a tmp directory; `demo_env` builds the demo
once per session (seeded DuckDB warehouse) and `fresh_demo` copies it for tests that change
the library.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from cteforge.cli.main import app

ENV_KEYS = (
    "CTEFORGE_DB_URL",
    "CTEFORGE_CONNECTIONS",
    "CTEFORGE_DEFAULT_PROFILE",
    "CTEFORGE_AUTH",
    "CTEFORGE_USER",
    "CTEFORGE_TOKEN",
)

Invoke = Callable[..., Result]


def make_env(directory: Path, *, user: str = "admin", connections: bool = True) -> dict[str, str]:
    env = dict.fromkeys(ENV_KEYS, "")  # Empty counts as unset (AppConfig.from_env).
    env["CTEFORGE_DB_URL"] = f"sqlite:///{directory / 'cteforge.db'}"
    if connections:
        env["CTEFORGE_CONNECTIONS"] = str(directory / "connections.yml")
        env["CTEFORGE_DEFAULT_PROFILE"] = "demo"
    env["CTEFORGE_USER"] = user
    return env


def invoker(env: dict[str, str]) -> Invoke:
    runner = CliRunner()

    def invoke(*args: str, user: str | None = None, **extra_env: str) -> Result:
        run_env = dict(env)
        if user is not None:
            run_env["CTEFORGE_USER"] = user
        run_env.update(extra_env)
        return runner.invoke(app, list(args), env=run_env)

    return invoke


@pytest.fixture
def cli(tmp_path: Path) -> Invoke:
    """A store in tmp_path with no warehouse profiles; acting user 'admin'."""
    return invoker(make_env(tmp_path, connections=False))


@pytest.fixture(scope="session")
def demo_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Result]:
    """`cteforge demo DIR` run once per session: (DIR, its result)."""
    directory = tmp_path_factory.mktemp("demo") / "cteforge-demo"
    result = CliRunner().invoke(app, ["demo", str(directory)], env=dict.fromkeys(ENV_KEYS, ""))
    assert result.exit_code == 0, result.output
    return directory, result


@pytest.fixture(scope="session")
def demo_dir(demo_run: tuple[Path, Result]) -> Path:
    return demo_run[0]


@pytest.fixture(scope="session")
def demo_cli(demo_dir: Path) -> Invoke:
    """Read-only use of the shared demo (acting user 'admin')."""
    return invoker(make_env(demo_dir))


@pytest.fixture
def fresh_demo(demo_dir: Path, tmp_path: Path) -> Invoke:
    """A private copy of the demo for tests that change the library."""
    copy = tmp_path / "demo"
    shutil.copytree(demo_dir, copy)
    return invoker(make_env(copy))
