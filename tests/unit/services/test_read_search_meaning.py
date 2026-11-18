"""search: hybrid keyword + meaning ranking (DESIGN §19, ADR 0023), with a fake embedder.

The fake maps words to topics: a query "means" the same as a definition when it hits the
same topics, with no words in common. Real-model thresholds apply (similarity 1.0 = same
topics, 0 = different topics)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cteforge.domain.errors import EmbeddingError, PermissionDeniedError, PolicyError
from cteforge.domain.models import Definition, DefinitionState, TeamMode
from cteforge.domain.search import SearchVector, document_sha256
from cteforge.domain.users import ActorKind, User, UserRole
from cteforge.files.definitions import load_definition
from cteforge.services import _meaning, describe, search_index
from cteforge.services.context import AppConfig, AppContext, open_context, set_team_mode
from cteforge.services.search import RRF_K, search

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
AGENT = User(name="claude-agent", role=UserRole.VIEWER, kind=ActorKind.AGENT)
ADMIN = User(name="root", role=UserRole.ADMIN)
TOPICS = {
    "collections": ["realization", "collected", "collection", "cash", "billed", "received"],
    "weather": ["weather", "sunny", "rain"],
    "people": ["employee", "headcount", "staff"],
}
MEANING_ONLY = "billed money received"  # None of these words is in any definition.


def example(name: str, **update) -> Definition:
    return load_definition(EXAMPLES / "definitions" / f"{name}.yml").model_copy(update=update)


def bare(id: str, title: str, meaning: str = "", **update) -> Definition:
    return Definition.model_validate(
        {
            "id": id,
            "title": title,
            "meaning": meaning,
            "source_dialect": "duckdb",
            "template": "SELECT 1 AS x",
            "state": DefinitionState.LLM_ANNOTATED,
            **update,
        }
    )


@pytest.fixture
def embedder(fake_embedder_class):
    return fake_embedder_class(TOPICS)


def _open(tmp_path: Path, embedder) -> AppContext:
    return open_context(
        AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}"), profiles=[], embedder=embedder
    )


@pytest.fixture
def ctx(tmp_path: Path, embedder):
    with _open(tmp_path, embedder) as c:
        c.store.add_definition_version(example("realization_rate"), author="analyst")
        c.store.add_definition_version(example("booked_vs_collected"), author="analyst")
        c.store.add_definition_version(
            bare("headcount", "Employee Headcount", "Staff per team at month end."),
            author="analyst",
        )
        yield c


def by_id(hits) -> dict[str, object]:
    return {h.id: h for h in hits}


# ── Matching ─────────────────────────────────────────────────────────────────────────


def test_meaning_only_match(ctx: AppContext) -> None:
    hits = search(ctx, AGENT, MEANING_ONLY)
    assert {h.id for h in hits} == {"realization_rate", "booked_vs_collected"}
    for h in hits:
        assert h.match == "meaning" and h.matched_terms == [] and h.matched_fields == []
        assert h.note is None
        assert h.score == pytest.approx(round(1 / (RRF_K + 1), 6))  # equal similarity → rank 1


def test_unrelated_query_returns_nothing(ctx: AppContext) -> None:
    assert search(ctx, AGENT, "sunny weather tomorrow") == []


def test_keyword_and_meaning_fuse(ctx: AppContext) -> None:
    hits = search(ctx, AGENT, "cash realization")
    found = by_id(hits)
    assert hits[0].id == "realization_rate"
    assert found["realization_rate"].match == "both"
    assert found["realization_rate"].matched_terms == ["cash", "realization"]
    assert "headcount" not in found
    # keyword rank 1 + meaning rank 1 (tied similarity shares rank 1)
    assert found["realization_rate"].score == pytest.approx(round(2 / (RRF_K + 1), 6))
    assert found["booked_vs_collected"].score == pytest.approx(
        round(1 / (RRF_K + 2) + 1 / (RRF_K + 1), 6)
    )


def test_keyword_only_hit_below_meaning_threshold(ctx: AppContext) -> None:
    # "team" is only in headcount's meaning; its topic vector (people) is far from the query's
    # (none: zero vector), so it matches by keyword alone.
    (hit,) = search(ctx, AGENT, "team")
    assert hit.id == "headcount" and hit.match == "keyword"


def test_meaning_gap_keeps_only_close_matches(tmp_path: Path, embedder) -> None:
    with _open(tmp_path, embedder) as c:
        c.store.add_definition_version(bare("pure", "Cash", "Realization."), author="a")
        # Half collections, half people: similarity 0.71 ≥ MIN_SIMILARITY but 0.29 below best.
        c.store.add_definition_version(bare("mixed", "Cash", "Staff."), author="a")
        assert [h.id for h in search(c, AGENT, MEANING_ONLY)] == ["pure"]


def test_dont_use_when_only_ranks_last_even_with_meaning(ctx: AppContext) -> None:
    ctx.store.add_definition_version(
        bare("aaa_trap", "Aaa", "", dont_use_when="Not for billed amounts."), author="a"
    )
    hits = search(ctx, AGENT, MEANING_ONLY)
    assert hits[-1].id == "aaa_trap"
    assert hits[-1].match == "both" and hits[-1].matched_fields == ["dont_use_when"]


def test_empty_query_unchanged(ctx: AppContext) -> None:
    hits = search(ctx, AGENT, "")
    assert [h.id for h in hits] == ["booked_vs_collected", "headcount", "realization_rate"]
    assert all(h.match is None and h.score == 0 and h.note is None for h in hits)


def test_without_embedder_scores_are_bm25(tmp_path: Path, ctx: AppContext) -> None:
    with_meaning = by_id(search(ctx, AGENT, "cash realization"))
    plain = open_context(
        AppConfig(db_url=f"sqlite:///{tmp_path / 'lib.db'}"), profiles=[], embedder=None
    )
    with plain:
        hits = search(plain, AGENT, "cash realization")
        assert search(plain, AGENT, MEANING_ONLY) == []
    assert all(h.match == "keyword" and h.note is None for h in hits)
    assert hits[0].id == "realization_rate" and hits[0].score > 1  # BM25 scale, not RRF
    assert hits[0].score != with_meaning["realization_rate"].score


def test_drafts_stay_hidden_from_meaning(ctx: AppContext) -> None:
    ctx.store.add_definition_version(
        bare("draft_cash", "Draft", "Cash collected.", state=DefinitionState.DRAFT), author="a"
    )
    assert "draft_cash" not in by_id(search(ctx, AGENT, MEANING_ONLY))
    assert by_id(search(ctx, AGENT, MEANING_ONLY, include_drafts=True))["draft_cash"].match == (
        "meaning"
    )
    set_team_mode(ctx, TeamMode.STRICT)  # only verified: none here
    assert search(ctx, AGENT, MEANING_ONLY) == []


# ── Model availability ───────────────────────────────────────────────────────────────


def test_failed_model_falls_back_to_keywords_with_a_note(tmp_path: Path, fake_embedder_class):
    broken = fake_embedder_class(TOPICS, fail=True)
    with _open(tmp_path, broken) as c:
        c.store.add_definition_version(example("realization_rate"), author="a")
        (hit,) = search(c, AGENT, "cash realization")
        assert hit.match == "keyword" and hit.note == _meaning.NOTE_FAILED
        assert hit.score > 1  # plain BM25
        assert search(c, AGENT, MEANING_ONLY) == []


def test_server_never_waits_for_the_model(tmp_path: Path, fake_embedder_class) -> None:
    cold = fake_embedder_class(TOPICS, ready=False)
    with _open(tmp_path, cold) as c:
        c.store.add_definition_version(example("realization_rate"), author="a")
        c.background_model = True
        (hit,) = search(c, AGENT, "cash realization")
        assert hit.note == _meaning.NOTE_LOADING and hit.match == "keyword"
        assert cold.loads == 0 and cold.background_loads == 1 and cold.calls == []
        cold._ready = True  # the background load finished
        (hit,) = search(c, AGENT, "cash realization")
        assert hit.match == "both" and hit.note is None


def test_cli_mode_loads_and_waits(tmp_path: Path, fake_embedder_class) -> None:
    cold = fake_embedder_class(TOPICS, ready=False)
    with _open(tmp_path, cold) as c:
        c.store.add_definition_version(example("realization_rate"), author="a")
        (hit,) = search(c, AGENT, MEANING_ONLY)
        assert hit.match == "meaning" and cold.loads >= 1 and cold.background_loads == 0


def test_embedding_error_during_search_is_keyword_only(ctx: AppContext, monkeypatch) -> None:
    def boom(texts, *, query=False):
        raise EmbeddingError("onnx broke")

    monkeypatch.setattr(ctx.embedder, "embed", boom)
    hits = search(ctx, AGENT, "realization")
    assert hits and hits[0].id == "realization_rate"
    assert all(h.match == "keyword" and h.note == _meaning.NOTE_ERROR for h in hits)


# ── Vectors: backfill and indexing ───────────────────────────────────────────────────


def test_search_backfills_vectors_once(ctx: AppContext, embedder) -> None:
    search(ctx, AGENT, MEANING_ONLY)
    stored = ctx.store.search_vectors(embedder.model_name)
    assert {(v.definition_id, v.version) for v in stored} == {
        ("booked_vs_collected", 1),
        ("headcount", 1),
        ("realization_rate", 1),
    }
    doc = _meaning.document_for(ctx.store.get_definition("realization_rate"))
    rr = next(v for v in stored if v.definition_id == "realization_rate")
    assert rr.doc_sha256 == document_sha256(doc)
    embedder.calls.clear()
    search(ctx, AGENT, "cash")
    assert embedder.calls == [(("cash",), True)]  # only the query, as a query


def test_background_search_embeds_at_most_a_few(ctx: AppContext, embedder, monkeypatch) -> None:
    monkeypatch.setattr(_meaning, "INCREMENTAL_LIMIT", 1)
    ctx.background_model = True
    search(ctx, AGENT, MEANING_ONLY)
    assert len(ctx.store.search_vectors(embedder.model_name)) == 1


def test_index_definition_only_with_a_loaded_model(tmp_path: Path, fake_embedder_class) -> None:
    cold = fake_embedder_class(TOPICS, ready=False)
    with _open(tmp_path, cold) as c:
        d = c.store.add_definition_version(example("realization_rate"), author="a")
        assert _meaning.index_definition(c, d) is False
        assert cold.loads == 0 and c.store.search_vectors(cold.model_name) == []
        cold._ready = True
        assert _meaning.index_definition(c, d) is True
        assert len(c.store.search_vectors(cold.model_name)) == 1


def test_index_definition_never_raises(ctx: AppContext, monkeypatch) -> None:
    def boom(texts, *, query=False):
        raise RuntimeError("anything")

    monkeypatch.setattr(ctx.embedder, "embed", boom)
    assert _meaning.index_definition(ctx, ctx.store.get_definition("headcount")) is False


def test_indexable_includes_visible_older_versions(ctx: AppContext) -> None:
    ctx.store.add_definition_version(
        bare("headcount", "Employee Headcount", "x", version=2, state=DefinitionState.DRAFT),
        author="a",
    )
    keys = {(d.id, d.version) for d in _meaning.indexable(ctx)}
    assert {("headcount", 1), ("headcount", 2)} <= keys


# ── rebuild_search_index ─────────────────────────────────────────────────────────────


def test_rebuild_embeds_missing_and_changed(ctx: AppContext, embedder) -> None:
    ctx.store.put_search_vector(
        SearchVector(
            definition_id="headcount",
            version=1,
            model=embedder.model_name,
            doc_sha256="stale",
            vector=(1.0, 0.0, 0.0),
        )
    )
    ctx.store.put_search_vector(
        SearchVector(
            definition_id="headcount",
            version=1,
            model="old/model",
            doc_sha256="x",
            vector=(1.0,),
        )
    )
    report = search_index.rebuild_search_index(ctx, ADMIN)
    assert (report.definitions, report.embedded, report.unchanged, report.removed) == (3, 3, 0, 1)
    assert ctx.store.search_vector_models() == [embedder.model_name]
    again = search_index.rebuild_search_index(ctx, ADMIN)
    assert (again.embedded, again.unchanged) == (0, 3)
    forced = search_index.rebuild_search_index(ctx, ADMIN, force=True)
    assert (forced.embedded, forced.removed) == (3, 3)


def test_rebuild_needs_admin_and_meaning_on(ctx: AppContext) -> None:
    with pytest.raises(PermissionDeniedError):
        search_index.rebuild_search_index(ctx, AGENT)
    with open_context(AppConfig(db_url="sqlite://"), profiles=[], embedder=None) as off:
        with pytest.raises(PolicyError, match="off"):
            search_index.rebuild_search_index(off, ADMIN)


def test_start_background_warms_up(ctx: AppContext, embedder) -> None:
    thread = search_index.start_background(ctx)
    assert ctx.background_model is True and thread is not None
    thread.join(timeout=10)
    assert len(ctx.store.search_vectors(embedder.model_name)) == 3


# ── Search document (describe) ───────────────────────────────────────────────────────


def test_search_document_use_case(ctx: AppContext) -> None:
    doc = describe.search_document(ctx, AGENT, "realization_rate")
    assert doc.id == "realization_rate" and doc.version == 1
    assert doc.markdown.startswith("# Realization Rate")
    assert "## Joins" in doc.markdown  # mined from the SQL
    card = describe.get_definition(ctx, AGENT, "realization_rate", include_search_document=True)
    assert card.search_document == doc.markdown
    assert describe.get_definition(ctx, AGENT, "realization_rate").search_document is None
