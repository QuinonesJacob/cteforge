"""search: BM25-style lexical ranking over the store's definitions (ADR 0017)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cteforge.domain.audit import AuditKind
from cteforge.domain.models import Definition, DefinitionState, TeamMode
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode
from cteforge.services.search import search, tokenize

ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"
AGENT = User(name="claude-agent", role=UserRole.VIEWER, kind=ActorKind.AGENT)


@pytest.fixture
def ctx(tmp_path: Path):
    with open_context(AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}"), profiles=[]) as c:
        yield c


def example(name: str, **update) -> Definition:
    return load_definition(EXAMPLES / "definitions" / f"{name}.yml").model_copy(update=update)


def add(ctx: AppContext, definition: Definition) -> Definition:
    return ctx.store.add_definition_version(definition, author="analyst")


def plain(id: str, title: str, state: DefinitionState, **update) -> Definition:
    base = example("realization_rate")
    return base.model_copy(
        update={
            "id": id,
            "title": title,
            "meaning": "",
            "keywords": [],
            "use_when": "",
            "dont_use_when": "",
            "state": state,
            **update,
        }
    )


@pytest.fixture
def library(ctx: AppContext) -> AppContext:
    add(ctx, example("realization_rate"))
    add(ctx, example("booked_vs_collected"))
    return ctx


def test_tokenize() -> None:
    assert tokenize("Realization_Rate by REGIONS") == ["realization", "rate", "region"]
    assert tokenize("categories, classes; status, analysis") == [
        "category",
        "class",
        "status",
        "analysis",
    ]
    assert tokenize("  ,;  ") == []


@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("cash realization", "realization_rate"),
        ("collection rate", "realization_rate"),
        ("booked vs collected", "booked_vs_collected"),
        ("booked revenue in a period", "booked_vs_collected"),
        ("min booked threshold", "booked_vs_collected"),  # variable name
    ],
)
def test_ranking(library: AppContext, query: str, first: str) -> None:
    hits = search(library, AGENT, query)
    assert hits[0].id == first
    assert hits[0].score > (hits[1].score if len(hits) > 1 else 0)


def test_no_match_returns_nothing(library: AppContext) -> None:
    assert search(library, AGENT, "inventory warehouse shipments") == []


def test_hit_carries_variables_for_the_agent(library: AppContext) -> None:
    [hit] = search(library, AGENT, "cash realization", limit=1)
    assert hit.version == 1 and hit.state is DefinitionState.LLM_ANNOTATED
    period = next(v for v in hit.variables if v.name == "period")
    assert period.input_format and period.original_value == {
        "start": "2026-01-01",
        "end": "2026-04-01",
    }


def test_verified_ranks_above_llm_annotated_at_equal_score(ctx: AppContext) -> None:
    add(ctx, plain("a_unreviewed", "Churn rate", DefinitionState.LLM_ANNOTATED))
    add(ctx, plain("z_verified", "Churn rate", DefinitionState.VERIFIED))
    hits = search(ctx, AGENT, "churn")
    assert [h.id for h in hits] == ["z_verified", "a_unreviewed"]
    assert hits[0].score == hits[1].score > 0


def test_relevance_beats_state(ctx: AppContext) -> None:
    add(ctx, plain("weak", "Churn", DefinitionState.VERIFIED))
    add(ctx, plain("strong", "Churn churn", DefinitionState.LLM_ANNOTATED, keywords=["churn"]))
    assert [h.id for h in search(ctx, AGENT, "churn")] == ["strong", "weak"]


def test_drafts_hidden_unless_asked(ctx: AppContext) -> None:
    add(ctx, plain("churn_draft", "Churn", DefinitionState.DRAFT))
    assert search(ctx, AGENT, "churn") == []
    assert [h.id for h in search(ctx, AGENT, "churn", include_drafts=True)] == ["churn_draft"]


def test_latest_draft_falls_back_to_earlier_usable_version(ctx: AppContext) -> None:
    add(ctx, plain("churn", "Churn", DefinitionState.VERIFIED))
    add(ctx, plain("churn", "Churn v2", DefinitionState.DRAFT, version=2))
    [hit] = search(ctx, AGENT, "churn")
    assert hit.version == 1 and hit.state is DefinitionState.VERIFIED
    [draft] = search(ctx, AGENT, "churn", include_drafts=True)
    assert draft.version == 2


def test_strict_mode_shows_only_verified(ctx: AppContext) -> None:
    add(ctx, plain("churn_llm", "Churn", DefinitionState.LLM_ANNOTATED))
    add(ctx, plain("churn_ok", "Churn", DefinitionState.VERIFIED))
    set_team_mode(ctx, TeamMode.STRICT)
    assert [h.id for h in search(ctx, AGENT, "churn")] == ["churn_ok"]
    assert len(search(ctx, AGENT, "churn", include_drafts=True)) == 2


def test_empty_query_lists_by_title(library: AppContext) -> None:
    hits = search(library, AGENT, "  ")
    assert [h.title for h in hits] == sorted(h.title for h in hits)
    assert [h.id for h in hits] == ["booked_vs_collected", "realization_rate"]
    assert all(h.score == 0 for h in hits)


def test_limit(ctx: AppContext) -> None:
    for i in range(5):
        add(ctx, plain(f"churn_{i}", f"Churn {i}", DefinitionState.VERIFIED))
    assert len(search(ctx, AGENT, "churn", limit=3)) == 3
    assert len(search(ctx, AGENT, "churn", limit=0)) == 1  # clamped to at least one


def test_search_is_audited(library: AppContext) -> None:
    hits = search(library, AGENT, "cash realization", question="How much cash did we collect?")
    event = library.audit.list_events(limit=1)[0]
    assert event.kind is AuditKind.SEARCH and event.actor == "claude-agent"
    assert event.question == "How much cash did we collect?"
    assert event.definitions == [f"{h.id}@{h.version}" for h in hits]
    assert event.extra["hits"] == len(hits)


def test_hit_shows_when_to_use_it_and_why_it_matched(library: AppContext) -> None:
    hit = next(h for h in search(library, AGENT, "realization rate") if h.id == "realization_rate")
    rr = example("realization_rate")
    assert (hit.use_when, hit.dont_use_when, hit.grain) == (
        rr.use_when,
        rr.dont_use_when,
        rr.grain,
    )
    assert hit.matched_terms == ["realization", "rate"]
    assert "title" in hit.matched_fields


def test_matches_only_in_dont_use_when_rank_last(ctx: AppContext) -> None:
    """A query naming exactly what a definition is NOT for must not rank it first."""
    add(
        ctx,
        plain(
            "rr",
            "Realization rate",
            DefinitionState.VERIFIED,
            dont_use_when="Pipeline or forecast questions",
        ),
    )
    add(ctx, plain("pf", "Open pipeline", DefinitionState.LLM_ANNOTATED))
    hits = search(ctx, AGENT, "pipeline forecast")
    assert [h.id for h in hits] == ["pf", "rr"]
    assert hits[1].matched_fields == ["dont_use_when"]
    assert hits[1].dont_use_when == "Pipeline or forecast questions"
