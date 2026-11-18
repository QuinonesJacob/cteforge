"""`search` (match kind, notes) and `search-index rebuild | download-model` (ADR 0023),
with a fake embedder in place of the real model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cteforge.services import context as context_module
from cteforge.services import search_index

TOPICS = {"collections": ["realization", "collected", "cash", "billed", "received"]}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, fake_embedder_class):
    embedder = fake_embedder_class(TOPICS)
    monkeypatch.setattr(context_module, "default_embedder", lambda config: embedder)
    return embedder


def test_search_shows_match_kind(fresh_demo, fake) -> None:
    result = fresh_demo("search", "billed money received")
    assert result.exit_code == 0, result.output
    header, *rows = result.stdout.strip().splitlines()
    assert header.split()[:5] == ["ID", "VERSION", "STATE", "MATCH", "SCORE"]
    assert rows and all(row.split()[3] == "meaning" for row in rows)
    hits = json.loads(fresh_demo("search", "cash realization", "--json").stdout)
    assert hits[0]["match"] == "both"


def test_search_note_when_model_unavailable(fresh_demo, monkeypatch, fake_embedder_class):
    broken = fake_embedder_class(TOPICS, fail=True)
    monkeypatch.setattr(context_module, "default_embedder", lambda config: broken)
    result = fresh_demo("search", "realization")
    assert result.exit_code == 0
    assert "note: Meaning search is unavailable" in result.stderr
    assert "keyword" in result.stdout


def test_search_with_meaning_off_shows_keyword(demo_cli) -> None:
    result = demo_cli("search", "realization")
    assert result.exit_code == 0 and "note:" not in result.output
    assert " keyword " in result.stdout


def test_rebuild(fresh_demo, fake) -> None:
    result = fresh_demo("search-index", "rebuild")
    assert result.exit_code == 0, result.output
    assert "fake/bag-of-words: 3 versions; 3 embedded, 0 unchanged" in result.stdout
    report = json.loads(fresh_demo("search-index", "rebuild", "--json").stdout)
    assert report["embedded"] == 0 and report["unchanged"] == 3


def test_rebuild_needs_admin(fresh_demo, fake) -> None:
    refused = fresh_demo("search-index", "rebuild", user="claude-agent")
    assert refused.exit_code == 1 and "error:" in refused.output


def test_rebuild_with_meaning_off(fresh_demo) -> None:
    result = fresh_demo("search-index", "rebuild")
    assert result.exit_code == 1 and "CTEFORGE_SEARCH_EMBEDDINGS=off" in result.output


def test_download_model(cli, monkeypatch, tmp_path: Path) -> None:
    seen = []

    def fake_download(config):
        seen.append(config.embed_cache_dir)
        return config.embed_cache_dir

    monkeypatch.setattr(search_index, "download_model", fake_download)
    result = cli("search-index", "download-model", CTEFORGE_EMBED_CACHE=str(tmp_path / "m"))
    assert result.exit_code == 0, result.output
    assert seen == [tmp_path / "m"] and f"ready in {tmp_path / 'm'}" in result.stdout
