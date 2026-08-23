"""Tests for load()'s three tiers: mode validation, staleness warnings,
the live cache, and sync's fall-back-with-a-warning behavior."""

import json
import warnings
from datetime import datetime, timedelta, timezone

import pytest

from rates import StaleLedgerWarning, SyncFallbackWarning
from rates.ai import Registry, load
from rates.ai import _load as load_module
from rates._http import FetchError

FRESH = {
    "schema_version": "1.0.0",
    "universe": "ai",
    "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
    "sources": [],
    "models": [{"provider": "anthropic", "id": "claude-opus-5"}],
}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(load_module, "_live_cache_path", lambda: tmp_path / "live.json")


@pytest.fixture
def bundled(monkeypatch):
    """Replace the bundled ledger with a controllable dict."""
    state = {"data": dict(FRESH)}
    monkeypatch.setattr(load_module, "_read_bundled", lambda: dict(state["data"]))
    return state


# Mode validation


def test_sync_and_live_together_raise():
    with pytest.raises(ValueError, match="don't combine"):
        load(sync=True, live=True)


def test_timeout_ceiling_applies_to_load():
    with pytest.raises(ValueError, match="300"):
        load(live=True, timeout=999)


# Ledger tier


def test_bundled_ledger_loads_without_network(bundled):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry = load()
    assert isinstance(registry, Registry)
    assert len(registry) == 1


def test_stale_ledger_warns_with_next_steps(bundled):
    old = (datetime.now(timezone.utc).date() - timedelta(days=40)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    with pytest.warns(StaleLedgerWarning, match="40 days old.*sync=True"):
        load()


def test_ledger_at_the_threshold_does_not_warn(bundled):
    edge = (datetime.now(timezone.utc).date() - timedelta(days=28)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": edge}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        load()


# Live tier


@pytest.fixture
def fused_live(monkeypatch):
    calls = []

    def fake_fetch(timeout=None):
        calls.append(timeout)
        return {"models_dev": {}}, {"models_dev": "ok"}

    monkeypatch.setattr(load_module, "fetch_sources", fake_fetch)
    monkeypatch.setattr(load_module, "fuse", lambda payloads, statuses: dict(FRESH))
    return calls


def test_live_fetches_and_caches(fused_live):
    assert len(load(live=True)) == 1
    assert len(load(live=True)) == 1
    assert len(fused_live) == 1  # second call served from the session cache


def test_live_cache_expires_after_24_hours_utc(fused_live):
    load(live=True)
    cache_path = load_module._live_cache_path()
    envelope = json.loads(cache_path.read_text())
    envelope["fetched_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=25)
    ).isoformat()
    cache_path.write_text(json.dumps(envelope))
    load(live=True)
    assert len(fused_live) == 2


def test_corrupt_live_cache_refetches_instead_of_crashing(fused_live):
    load_module._live_cache_path().write_text("not json{")
    assert len(load(live=True)) == 1
    assert len(fused_live) == 1


def test_live_timeout_passes_through(fused_live):
    load(live=True, timeout=120)
    assert fused_live == [120]


# Sync tier


def _release(published, asset_url="https://example.test/ledger-ai.json"):
    return {
        "published_at": f"{published}T06:00:00Z",
        "assets": [{"name": "ledger-ai.json", "browser_download_url": asset_url}],
    }


def _patch_sync_fetch(monkeypatch, releases, ledger=None):
    def fake(url, timeout=None, token=None):
        if isinstance(releases, Exception):
            raise releases
        if "api.github.com" in url:
            return releases
        return ledger

    monkeypatch.setattr(load_module, "fetch_json", fake)


def test_sync_serves_a_newer_published_ledger(bundled, monkeypatch):
    old = (datetime.now(timezone.utc).date() - timedelta(days=10)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    newer = {**FRESH, "models": [{"provider": "x", "id": "a"}, {"provider": "x", "id": "b"}]}
    _patch_sync_fetch(
        monkeypatch, [_release(FRESH["snapshot_date"])], ledger=newer
    )
    registry = load(sync=True)
    assert len(registry) == 2


def test_sync_skips_the_download_when_local_is_current(bundled, monkeypatch):
    downloads = []

    def fake(url, timeout=None, token=None):
        if "api.github.com" in url:
            return [_release("2020-01-01")]
        downloads.append(url)
        return {}

    monkeypatch.setattr(load_module, "fetch_json", fake)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry = load(sync=True)
    assert len(registry) == 1
    assert downloads == []


def test_sync_failure_warns_and_falls_back_never_raises(bundled, monkeypatch):
    _patch_sync_fetch(monkeypatch, FetchError("https://api.github.com/x: HTTP 403"))
    with pytest.warns(SyncFallbackWarning, match="couldn't check"):
        registry = load(sync=True)
    assert len(registry) == 1


def test_sync_with_no_ledger_release_serves_local_quietly(bundled, monkeypatch):
    _patch_sync_fetch(monkeypatch, [{"published_at": "2030-01-01T00:00:00Z", "assets": []}])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry = load(sync=True)
    assert len(registry) == 1


def test_sync_schema_major_mismatch_warns_and_falls_back(bundled, monkeypatch):
    old = (datetime.now(timezone.utc).date() - timedelta(days=10)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    incompatible = {**FRESH, "schema_version": "2.0.0"}
    _patch_sync_fetch(
        monkeypatch, [_release(FRESH["snapshot_date"])], ledger=incompatible
    )
    with pytest.warns(SyncFallbackWarning, match="pip install -U rates"):
        registry = load(sync=True)
    assert len(registry) == 1


# The shipped ledger itself


def test_the_actual_bundled_ledger_is_loadable_and_queryable():
    registry = load()
    assert len(registry) > 5000
    assert len(registry.filter(provider="anthropic", model="claude-opus-5")) == 1
