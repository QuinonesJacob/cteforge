"""demo end to end, then the library and review commands against it: search, run, verify,
revalidate, export / import-library, proposals list/show/approve/reject.

Uses the demo's seeded DuckDB warehouse (synthetic data, built in a tmp dir)."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from pathlib import Path

import yaml
from click.testing import Result
from typer.testing import CliRunner

from cteforge.cli.main import app

Invoke = Callable[..., Result]
BLANK_ENV = dict.fromkeys(
    [
        "CTEFORGE_DB_URL",
        "CTEFORGE_CONNECTIONS",
        "CTEFORGE_DEFAULT_PROFILE",
        "CTEFORGE_AUTH",
        "CTEFORGE_USER",
    ],
    "",
)

RR_VARS = '{"rr": {"period": {"start": "2025-07-01", "end": "2025-10-01"}, "regions": ["APAC"]}}'
EXAMPLE_IDS = {"realization_rate", "booked_vs_collected", "realization_rate_by_grain"}


# ── demo ─────────────────────────────────────────────────────────────────────────────


def test_demo_sets_up_a_working_product(demo_run):
    directory, result = demo_run
    out = result.output
    for name in ("cteforge.db", "warehouse.duckdb", "connections.yml", "env.sh"):
        assert (directory / name).is_file(), name
    for id_ in EXAMPLE_IDS:
        assert f"{id_}: auto-applied ({id_} v1 llm_annotated)" in out
    assert "admin token (admin" in out and "agent token (claude-agent" in out
    assert "cteforge serve" in out
    snippet = json.loads(out[out.index('{\n  "mcpServers"') :])
    server = snippet["mcpServers"]["cteforge"]
    assert server["command"] == "uv" and server["args"][-2:] == ["cteforge", "mcp"]
    assert server["env"]["CTEFORGE_USER"] == "claude-agent"
    assert server["env"]["CTEFORGE_CONNECTIONS"] == str(directory / "connections.yml")
    profiles = yaml.safe_load((directory / "connections.yml").read_text())["profiles"]
    assert profiles["demo"]["dialect"] == "duckdb"


def test_demo_env_file_has_no_tokens_and_quotes_values(demo_run):
    directory, result = demo_run
    env_text = (directory / "env.sh").read_text()
    exports = dict(
        shlex.split(line.removeprefix("export "))[0].split("=", 1)
        for line in env_text.splitlines()
        if line.startswith("export ")
    )
    assert exports["CTEFORGE_USER"] == "admin"
    assert exports["CTEFORGE_DEFAULT_PROFILE"] == "demo"
    token_line = next(line for line in result.output.splitlines() if "admin token" in line)
    assert token_line.rsplit(" ", 1)[-1] not in env_text


def test_demo_refuses_existing_db_unless_forced(fresh_demo: Invoke, tmp_path: Path):
    target = tmp_path / "demo"  # fresh_demo's copy
    env = dict(BLANK_ENV)
    runner = CliRunner()
    again = runner.invoke(app, ["demo", str(target)], env=env)
    assert again.exit_code == 1 and "--force" in again.output
    forced = runner.invoke(app, ["demo", str(target), "--force"], env=env)
    assert forced.exit_code == 0, forced.output


def test_demo_without_examples_exits_1(tmp_path: Path):
    result = CliRunner().invoke(
        app,
        ["demo", str(tmp_path / "d"), "--examples", str(tmp_path / "nope")],
        env=dict(BLANK_ENV),
    )
    assert result.exit_code == 1 and "examples not found" in result.output


# ── search / run ─────────────────────────────────────────────────────────────────────


def test_search_realization(demo_cli: Invoke):
    result = demo_cli("search", "realization")
    assert result.exit_code == 0, result.output
    assert "realization_rate " in result.output and "llm_annotated" in result.output
    hits = json.loads(demo_cli("search", "realization", "--json").stdout)
    assert hits[0]["id"] in {"realization_rate", "realization_rate_by_grain"}
    assert {"realization_rate", "realization_rate_by_grain"} <= {h["id"] for h in hits}
    none = demo_cli("search", "zzzunmatched")
    assert none.exit_code == 0 and "no matches" in none.output


def test_run_realization_rate(demo_cli: Invoke):
    result = demo_cli("run", "--use", "rr=realization_rate", "--vars", RR_VARS)
    assert result.exit_code == 0, result.output
    assert "label: llm_annotated" in result.output
    assert "used: realization_rate@1 (llm_annotated)" in result.output
    assert "month" in result.output and "APAC" in result.output and "EMEA" not in result.output
    outcome = json.loads(
        demo_cli("run", "--use", "rr=realization_rate@1", "--vars", RR_VARS, "--json").stdout
    )
    assert outcome["ran"] is True and outcome["definitions"] == ["realization_rate@1"]
    assert outcome["columns"] == ["month", "region", "realization_rate"]
    assert {row[1] for row in outcome["rows"]} == {"APAC"}


def test_run_compile_only_with_select(demo_cli: Invoke):
    result = demo_cli(
        "run",
        "--use",
        "rr=realization_rate",
        "--select",
        "SELECT region, AVG(realization_rate) AS avg_rate FROM rr GROUP BY region",
        "--compile-only",
    )
    assert result.exit_code == 0, result.output
    assert "WITH" in result.output and "cteforge" in result.output  # tagged SQL shown


BY_REGION = (
    "SELECT rr.region, AVG(rr.realization_rate) AS avg_rate, MAX(bc.collected) AS collected "
    "FROM rr JOIN bc ON rr.region = bc.region GROUP BY rr.region"
)


def test_demo_definitions_declare_join_keys_so_composition_keeps_trust(demo_cli: Invoke):
    """Regression: without join_keys every join of two demo definitions was exploratory."""
    result = demo_cli(
        "run", "--use", "rr=realization_rate", "--use", "bc=booked_vs_collected",
        "--select", BY_REGION, "--json",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    outcome = json.loads(result.stdout)
    assert outcome["label"] == "llm_annotated"
    assert "unknown_join" not in {i["code"] for i in outcome["issues"]}
    assert outcome["ran"] is True and outcome["rows"]  # the default regions of both parts


def test_verified_demo_parts_compose_to_built_from_verified_parts(fresh_demo: Invoke):
    for id_ in ("realization_rate", "booked_vs_collected"):
        assert fresh_demo("verify", id_, "1").exit_code == 0
    result = fresh_demo(
        "run", "--use", "rr=realization_rate", "--use", "bc=booked_vs_collected",
        "--select", BY_REGION, "--json",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["label"] == "built_from_verified_parts"


def test_run_input_errors(demo_cli: Invoke):
    assert demo_cli("run", "--use", "realization_rate").exit_code == 2
    assert demo_cli("run", "--use", "rr=realization_rate@x").exit_code == 2
    assert demo_cli("run", "--use", "rr=a", "--use", "rr=b").exit_code == 2
    assert demo_cli("run", "--use", "rr=realization_rate", "--vars", "[1]").exit_code == 2
    missing = demo_cli("run", "--use", "x=no_such_definition")
    assert missing.exit_code == 1 and "does not exist" in missing.output
    bad_value = demo_cli(
        "run", "--use", "rr=realization_rate", "--vars", '{"rr": {"period": "2026-Q3"}}'
    )
    assert bad_value.exit_code == 1


def test_run_refused_in_strict_mode(fresh_demo: Invoke):
    assert fresh_demo("settings", "mode", "strict").exit_code == 0
    result = fresh_demo("run", "--use", "rr=realization_rate", "--vars", RR_VARS)
    assert result.exit_code == 1
    assert "REFUSED" in result.output


# ── verify / revalidate ──────────────────────────────────────────────────────────────


def test_verify_then_run_is_labeled_verified(fresh_demo: Invoke):
    stale = fresh_demo("verify", "realization_rate", "7")
    assert stale.exit_code == 1
    missing = fresh_demo("verify", "no_such", "1")
    assert missing.exit_code == 1
    assert fresh_demo("verify", "realization_rate", "x").exit_code == 2
    ok = fresh_demo("verify", "realization_rate", "1")
    assert ok.exit_code == 0, ok.output
    assert "realization_rate v1: verified by admin" in ok.output
    run = fresh_demo("run", "--use", "rr=realization_rate", "--vars", RR_VARS)
    assert run.exit_code == 0, run.output
    assert "label: verified" in run.output and "verified by admin" in run.output


def test_revalidate(demo_cli: Invoke):
    everything = demo_cli("revalidate")
    assert everything.exit_code == 0, everything.output
    assert "DRIFT" not in everything.output
    report = json.loads(demo_cli("revalidate", "realization_rate", "--json").stdout)
    assert [(r["id"], r["checked"], r["ok"]) for r in report] == [("realization_rate", True, True)]
    assert demo_cli("revalidate", "no_such").exit_code == 1


# ── export / import-library / proposals ──────────────────────────────────────────────


def test_export_and_unchanged_import(demo_cli: Invoke, tmp_path: Path):
    out = tmp_path / "lib"
    result = demo_cli("export", str(out))
    assert result.exit_code == 0, result.output
    assert {p.stem for p in (out / "definitions").glob("*.yml")} == EXAMPLE_IDS
    again = demo_cli("import-library", str(out))
    assert again.exit_code == 0, again.output
    assert "nothing changed" in again.output


def test_import_library_bad_directory_exits_1(demo_cli: Invoke, tmp_path: Path):
    bad = tmp_path / "bad" / "definitions"
    bad.mkdir(parents=True)
    (bad / "broken.yml").write_text("::: not yaml [")
    assert demo_cli("import-library", str(tmp_path / "bad")).exit_code == 1


def _retitle(path: Path, title: str) -> None:
    data = yaml.safe_load(path.read_text())
    data["title"] = title
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def test_review_flow(fresh_demo: Invoke, tmp_path: Path):
    listed = fresh_demo("proposals", "list")
    assert listed.exit_code == 0 and listed.output.count("approved (auto)") == 3

    assert fresh_demo("settings", "mode", "strict").exit_code == 0  # nothing auto-applies
    lib = tmp_path / "lib"
    assert fresh_demo("export", str(lib)).exit_code == 0
    _retitle(lib / "definitions" / "realization_rate.yml", "Realization Rate (renamed)")
    _retitle(lib / "definitions" / "booked_vs_collected.yml", "Booked vs Collected (renamed)")
    imported = fresh_demo("import-library", str(lib), "--json")
    assert imported.exit_code == 0, imported.output
    outcomes = {o["proposal"]["target_id"]: o for o in json.loads(imported.stdout)}
    assert set(outcomes) == {"realization_rate", "booked_vs_collected"}
    assert all(o["proposal"]["status"] == "pending" for o in outcomes.values())

    pending = json.loads(fresh_demo("proposals", "list", "--status", "pending", "--json").stdout)
    assert {p["target_id"] for p in pending} == {"realization_rate", "booked_vs_collected"}
    rr_id = outcomes["realization_rate"]["proposal"]["id"]
    bvc_id = outcomes["booked_vs_collected"]["proposal"]["id"]

    shown = fresh_demo("proposals", "show", rr_id)
    assert shown.exit_code == 0, shown.output
    assert "Realization Rate (renamed)" in shown.output and "base v1" in shown.output

    approved = fresh_demo("proposals", "approve", rr_id, "--verify", "-m", "looks right")
    assert approved.exit_code == 0, approved.output
    assert "approved" in approved.output and "realization_rate v2: verified" in approved.output

    assert fresh_demo("proposals", "reject", bvc_id).exit_code == 2  # comment required
    rejected = fresh_demo("proposals", "reject", bvc_id, "--comment", "keep the old title")
    assert rejected.exit_code == 0 and "rejected" in rejected.output

    twice = fresh_demo("proposals", "approve", bvc_id)
    assert twice.exit_code == 1  # no longer pending
    assert fresh_demo("proposals", "show", "nope").exit_code == 1
    assert fresh_demo("proposals", "list", "--status", "maybe").exit_code == 2

    final = json.loads(fresh_demo("proposals", "show", bvc_id, "--json").stdout)
    assert final["status"] == "rejected" and final["comment"] == "keep the old title"


def test_agent_cannot_approve_or_verify(fresh_demo: Invoke):
    proposals = json.loads(fresh_demo("proposals", "list", "--json").stdout)
    assert proposals  # demo proposals exist
    verify = fresh_demo("verify", "realization_rate", "1", user="claude-agent")
    assert verify.exit_code == 1 and "may not" in verify.output
    users = fresh_demo("user", "list", user="claude-agent")
    assert users.exit_code == 1


def test_references_and_rename(fresh_demo: Invoke):
    # ADR 0024: list what uses an id, rename it (open mode, not verified: applied at once),
    # and the old id then fails naming the new one.
    refs = fresh_demo("references", "realization_rate")
    assert refs.exit_code == 0, refs.output
    assert "realization_rate v1" in refs.stdout and "outside cteforge" in refs.stdout
    as_json = json.loads(fresh_demo("references", "realization_rate", "--json").stdout)
    assert as_json["id"] == "realization_rate" and as_json["former_ids"] == []
    renamed = fresh_demo("rename", "realization_rate", "rr_monthly", "--summary", "clearer")
    assert renamed.exit_code == 0, renamed.output
    assert "rename_definition" in renamed.stdout and "auto-applied" in renamed.stdout
    missing = fresh_demo("run", "--use", "x=realization_rate")
    assert missing.exit_code == 1 and "was renamed to 'rr_monthly'" in missing.output
    assert "formerly realization_rate" in fresh_demo("references", "rr_monthly").stdout
    assert fresh_demo("rename", "rr_monthly", "Bad Name").exit_code == 1
