"""revalidate: drift check of the latest versions against the DuckDB demo warehouse."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind, AuditOutcome
from cteforge.domain.connections import WarehouseProfile
from cteforge.domain.dialects import Dialect
from cteforge.domain.errors import NotFoundError, PermissionDeniedError
from cteforge.domain.issues import IssueCode, Severity
from cteforge.domain.models import Definition, DefinitionState
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services._library import annotation_file
from cteforge.services.context import AppConfig, AppContext, open_context
from cteforge.services.revalidate import revalidate

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"
VIEWER = User(name="viewer", role=UserRole.VIEWER, kind=ActorKind.AGENT)
APPROVER = User(name="ana", role=UserRole.APPROVER)


@pytest.fixture(scope="module")
def wh_file(warehouse_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("reval_wh") / "warehouse.duckdb"
    shutil.copy(warehouse_path, path)
    return path


@pytest.fixture
def ctx(tmp_path: Path, wh_file: Path):
    profile = WarehouseProfile(name="demo", dialect=Dialect.DUCKDB, options={"path": str(wh_file)})
    config = AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}")
    with open_context(config, profiles=[profile]) as context:
        yield context


def example(name: str, **update) -> Definition:
    return load_definition(EXAMPLES / "definitions" / f"{name}.yml").model_copy(update=update)


def add(ctx: AppContext, definition: Definition) -> Definition:
    return ctx.store.add_definition_version(definition, author="analyst")


def with_column_type(definition: Definition, name: str, column_type: str) -> Definition:
    variables = [
        v.model_copy(update={"column_type": column_type}) if v.name == name else v
        for v in definition.variables
    ]
    return definition.model_copy(update={"variables": variables})


def test_annotations_round_trip_the_variables() -> None:
    d = example("booked_vs_collected")
    ann = annotation_file(d)
    period = next(a for a in ann.variables if a.name == "period")
    assert period.literals == [lit for s in d.variable("period").slots for lit in s.literals]
    assert len(period.literals) == 4  # two places of [start, end]
    assert (
        next(a for a in ann.variables if a.name == "min_booked").unit
        == d.variable("min_booked").unit
    )


def test_unchanged_definitions_have_no_drift(ctx: AppContext) -> None:
    add(ctx, example("realization_rate", state=DefinitionState.VERIFIED))
    add(ctx, example("booked_vs_collected"))
    report = revalidate(ctx, APPROVER)
    assert [(r.id, r.version) for r in report] == [
        ("booked_vs_collected", 1),
        ("realization_rate", 1),
    ]
    for r in report:
        assert r.checked and r.ok and r.results_match is True and r.profile == "demo"
        assert not [i for i in r.issues if i.code is IssueCode.DRIFT]
    event = ctx.audit.list_events(limit=1)[0]
    assert event.kind is AuditKind.REVALIDATE and event.outcome is AuditOutcome.OK
    assert event.definitions == ["booked_vs_collected@1", "realization_rate@1"]
    assert event.extra["drifted"] == []


def test_changed_column_type_is_reported(ctx: AppContext) -> None:
    add(ctx, with_column_type(example("realization_rate"), "period", "DATE"))
    [r] = revalidate(ctx, VIEWER, ["realization_rate"])
    drift = [i for i in r.issues if i.code is IssueCode.DRIFT]
    assert not r.ok and len(drift) == 1
    assert drift[0].severity is Severity.WARNING and drift[0].variable == "period"
    assert "DATE" in drift[0].message and "TIMESTAMP" in drift[0].message


def test_missing_table_is_drift_error_and_state_is_unchanged(ctx: AppContext) -> None:
    d = example("realization_rate", state=DefinitionState.VERIFIED, verified_by="ana")
    broken = d.model_copy(
        update={"template": d.template.replace("FROM deals AS d", "FROM deals_archive AS d")}
    )
    add(ctx, broken)
    [r] = revalidate(ctx, VIEWER, ["realization_rate"])
    assert r.checked and not r.ok
    errors = [i for i in r.issues if i.code is IssueCode.DRIFT and i.severity is Severity.ERROR]
    assert errors and "compile_failed" in errors[0].message
    assert IssueCode.COMPILE_FAILED in {i.code for i in r.issues}
    stored = ctx.store.get_definition("realization_rate")
    assert stored.state is DefinitionState.VERIFIED and stored.verified_by == "ana"
    assert len(ctx.store.definition_history("realization_rate")) == 1
    assert ctx.audit.list_events(limit=1)[0].extra["drifted"] == ["realization_rate"]


def test_unconfigured_profile_is_not_checked(ctx: AppContext) -> None:
    add(ctx, example("realization_rate", profile="elsewhere"))
    [r] = revalidate(ctx, VIEWER, ["realization_rate"])
    assert not r.checked and r.ok and r.profile == "elsewhere"
    assert [i.code for i in r.issues] == [IssueCode.CHECKS_INCOMPLETE]


def test_permissions_and_unknown_ids(ctx: AppContext) -> None:
    add(ctx, example("realization_rate"))
    with pytest.raises(PermissionDeniedError, match="whole library"):
        revalidate(ctx, VIEWER)
    assert ctx.audit.list_events(limit=1)[0].outcome is AuditOutcome.REJECTED
    with pytest.raises(NotFoundError):
        revalidate(ctx, VIEWER, ["realization_rate", "nope"])
