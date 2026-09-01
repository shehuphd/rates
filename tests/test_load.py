"""Tests for load()'s three tiers: mode validation, staleness warnings,
the live cache, and stable's fall-back-with-a-warning behavior."""

import json
import warnings
from datetime import datetime, timedelta, timezone

import pytest

from rates import (
    SourceUnreachableWarning,
    StaleLedgerWarning,
    SyncFallbackWarning,
)
from rates import _cache as _cache_module
from rates._http import FetchError
from rates.ai import Registry, load
from rates.ai import _load as load_module

FRESH = {
    "schema_version": "1.0.0",
    "domain": "ai",
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


def test_invalid_fetch_value_raises():
    with pytest.raises(ValueError, match="bundled.*stable.*live"):
        load(fetch="nope")  # type: ignore[arg-type]


def test_timeout_ceiling_applies_to_load():
    with pytest.raises(ValueError, match="300"):
        load(fetch="live", timeout=999)


# Bundled tier


def test_bundled_ledger_loads_without_network(bundled):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry = load()
    assert isinstance(registry, Registry)
    assert len(registry) == 1


def test_stale_ledger_warns_with_next_steps(bundled):
    old = (datetime.now(timezone.utc).date() - timedelta(days=40)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    with pytest.warns(StaleLedgerWarning, match="40 days old.*fetch='stable'"):
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
    monkeypatch.setattr(
        load_module, "gather_source_freshness", lambda statuses, timeout=None: {}
    )
    monkeypatch.setattr(
        load_module, "record_freshness_lookup", lambda timeout=None: None
    )
    monkeypatch.setattr(
        load_module, "fuse", lambda payloads, statuses, **kwargs: dict(FRESH)
    )
    return calls


def test_live_fetches_and_caches(fused_live):
    assert len(load(fetch="live")) == 1
    assert len(load(fetch="live")) == 1
    assert len(fused_live) == 1  # second call served from the session cache


def test_live_cache_expires_after_24_hours_utc(fused_live):
    load(fetch="live")
    cache_path = load_module._live_cache_path()
    envelope = json.loads(cache_path.read_text())
    envelope["fetched_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=25)
    ).isoformat()
    cache_path.write_text(json.dumps(envelope))
    load(fetch="live")
    assert len(fused_live) == 2


def test_corrupt_live_cache_refetches_instead_of_crashing(fused_live):
    load_module._live_cache_path().write_text("not json{")
    assert len(load(fetch="live")) == 1
    assert len(fused_live) == 1


def test_live_timeout_passes_through(fused_live):
    load(fetch="live", timeout=120)
    assert fused_live == [120]


def test_force_bypasses_a_warm_live_cache(fused_live):
    load(fetch="live")
    load(fetch="live", force=True)
    assert len(fused_live) == 2  # the warm cache would otherwise skip this


def test_force_result_becomes_the_new_cache(fused_live):
    load(fetch="live")
    load(fetch="live", force=True)
    load(fetch="live")  # not forced: should reuse the just-forced fetch
    assert len(fused_live) == 2


def _live_with_statuses(monkeypatch, statuses):
    """Wire the live path to fuse successfully while fetch_sources reports
    the given per-source statuses, so the unreachable-source warning can be
    exercised without any network."""
    monkeypatch.setattr(
        load_module,
        "fetch_sources",
        lambda timeout=None: ({"models_dev": {}}, dict(statuses)),
    )
    monkeypatch.setattr(
        load_module, "gather_source_freshness", lambda statuses, timeout=None: {}
    )
    monkeypatch.setattr(
        load_module, "record_freshness_lookup", lambda timeout=None: None
    )
    monkeypatch.setattr(
        load_module, "fuse", lambda payloads, statuses, **kwargs: dict(FRESH)
    )


def test_live_warns_when_a_fallback_source_is_unreachable(isolated_cache, monkeypatch):
    _live_with_statuses(
        monkeypatch,
        {"models_dev": "ok", "genai_prices": "ok", "litellm": "ok",
         "openrouter": "unreachable"},
    )
    with pytest.warns(SourceUnreachableWarning, match="openrouter"):
        load(fetch="live")


def test_live_names_every_unreachable_fallback(isolated_cache, monkeypatch):
    _live_with_statuses(
        monkeypatch,
        {"models_dev": "ok", "genai_prices": "unreachable", "litellm": "ok",
         "openrouter": "unreachable"},
    )
    with pytest.warns(SourceUnreachableWarning, match="genai_prices, openrouter"):
        load(fetch="live")


def test_live_does_not_warn_when_every_source_is_reachable(
    isolated_cache, monkeypatch
):
    _live_with_statuses(
        monkeypatch,
        {"models_dev": "ok", "genai_prices": "ok", "litellm": "ok",
         "openrouter": "ok"},
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", SourceUnreachableWarning)
        load(fetch="live")  # a fully-healthy fusion stays quiet


def test_a_warm_cache_does_not_re_warn(isolated_cache, monkeypatch):
    # The warning describes a fusion that ran degraded; a cached
    # result skips fetch_sources entirely, so the second read is silent.
    _live_with_statuses(
        monkeypatch,
        {"models_dev": "ok", "openrouter": "unreachable"},
    )
    with pytest.warns(SourceUnreachableWarning):
        load(fetch="live")
    with warnings.catch_warnings():
        warnings.simplefilter("error", SourceUnreachableWarning)
        load(fetch="live")  # served from cache, no fresh fusion, no warning


def test_force_without_live_raises():
    with pytest.raises(ValueError, match="only applies to fetch='live'"):
        load(fetch="stable", force=True)
    with pytest.raises(ValueError, match="only applies to fetch='live'"):
        load(force=True)


# The cache directory

# Captured at import time, before the autouse isolation fixture replaces
# the module attribute; this is the shipped function, not the test double.
_REAL_CACHE_DIR = _cache_module.cache_dir


def test_cache_dir_is_per_user_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(_cache_module.Path, "home", lambda: tmp_path)
    cache = _REAL_CACHE_DIR()
    assert cache == tmp_path / ".cache" / "rates"
    assert cache.stat().st_mode & 0o777 == 0o700


def test_live_cache_written_by_an_incompatible_rates_version_refetches(fused_live):
    load(fetch="live")
    cache_path = load_module._live_cache_path()
    envelope = json.loads(cache_path.read_text())
    envelope["registry"]["schema_version"] = "9.0.0"
    cache_path.write_text(json.dumps(envelope))
    load(fetch="live")
    assert len(fused_live) == 2


# Stable tier


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


def test_stable_serves_a_newer_published_ledger(bundled, monkeypatch):
    old = (datetime.now(timezone.utc).date() - timedelta(days=10)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    newer = {**FRESH, "models": [{"provider": "x", "id": "a"}, {"provider": "x", "id": "b"}]}
    _patch_sync_fetch(
        monkeypatch, [_release(FRESH["snapshot_date"])], ledger=newer
    )
    registry = load(fetch="stable")
    assert len(registry) == 2


def test_stable_skips_the_download_when_local_is_current(bundled, monkeypatch):
    downloads = []

    def fake(url, timeout=None, token=None):
        if "api.github.com" in url:
            return [_release("2020-01-01")]
        downloads.append(url)
        return {}

    monkeypatch.setattr(load_module, "fetch_json", fake)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry = load(fetch="stable")
    assert len(registry) == 1
    assert downloads == []


def test_stable_failure_warns_and_falls_back_never_raises(bundled, monkeypatch):
    _patch_sync_fetch(monkeypatch, FetchError("https://api.github.com/x: HTTP 403"))
    with pytest.warns(SyncFallbackWarning, match="couldn't check"):
        registry = load(fetch="stable")
    assert len(registry) == 1


def test_stable_with_no_ledger_release_serves_local_quietly(bundled, monkeypatch):
    _patch_sync_fetch(monkeypatch, [{"published_at": "2030-01-01T00:00:00Z", "assets": []}])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry = load(fetch="stable")
    assert len(registry) == 1


def test_stable_caches_the_download_and_reuses_it(bundled, monkeypatch):
    old = (datetime.now(timezone.utc).date() - timedelta(days=10)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    newer = {
        **FRESH,
        "models": [{"provider": "x", "id": "a"}, {"provider": "x", "id": "b"}],
    }
    downloads = []

    def fake(url, timeout=None, token=None):
        if "api.github.com" in url:
            return [_release(FRESH["snapshot_date"])]
        downloads.append(url)
        return dict(newer)

    monkeypatch.setattr(load_module, "fetch_json", fake)
    assert len(load(fetch="stable")) == 2
    assert len(load(fetch="stable")) == 2  # served from the sync cache this time
    assert len(downloads) == 1


def test_stable_compares_snapshots_via_the_release_tag(bundled, monkeypatch):
    # Built one day, released the next: the tag carries the snapshot date,
    # so the ledger must not look newer than its own content.
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    downloads = []

    def fake(url, timeout=None, token=None):
        if "api.github.com" in url:
            return [
                {
                    "tag_name": f"ledger-{FRESH['snapshot_date']}",
                    "published_at": f"{tomorrow}T06:00:00Z",
                    "assets": [
                        {
                            "name": "ledger-ai.json",
                            "browser_download_url": "https://example.test/l.json",
                        }
                    ],
                }
            ]
        downloads.append(url)
        return {}

    monkeypatch.setattr(load_module, "fetch_json", fake)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry = load(fetch="stable")
    assert len(registry) == 1
    assert downloads == []


def test_stable_fallback_still_reports_a_stale_local_ledger(bundled, monkeypatch):
    old = (datetime.now(timezone.utc).date() - timedelta(days=40)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    _patch_sync_fetch(monkeypatch, FetchError("https://api.github.com/x: HTTP 403"))
    with pytest.warns() as record:
        load(fetch="stable")
    categories = {w.category for w in record}
    assert SyncFallbackWarning in categories
    assert StaleLedgerWarning in categories


def test_stable_with_no_newer_release_still_reports_staleness(bundled, monkeypatch):
    old = (datetime.now(timezone.utc).date() - timedelta(days=40)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    _patch_sync_fetch(monkeypatch, [_release(old)])
    with pytest.warns(StaleLedgerWarning):
        load(fetch="stable")


def test_stable_schema_major_mismatch_warns_and_falls_back(bundled, monkeypatch):
    old = (datetime.now(timezone.utc).date() - timedelta(days=10)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    incompatible = {**FRESH, "schema_version": "2.0.0"}
    _patch_sync_fetch(
        monkeypatch, [_release(FRESH["snapshot_date"])], ledger=incompatible
    )
    with pytest.warns(SyncFallbackWarning, match="pip install -U rates"):
        registry = load(fetch="stable")
    assert len(registry) == 1


# Bundled and stable share one "best local snapshot"


def test_bundled_tier_stops_warning_after_a_successful_stable_fetch(bundled, monkeypatch):
    old = (datetime.now(timezone.utc).date() - timedelta(days=40)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    _patch_sync_fetch(monkeypatch, [_release(FRESH["snapshot_date"])], ledger=dict(FRESH))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        load(fetch="stable")  # downloads and caches today's snapshot

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        registry = load()  # bare/default tier now sees it, not the stale bundled asset
    assert registry.snapshot_date.isoformat() == FRESH["snapshot_date"]


# The shipped ledger itself


def test_the_actual_bundled_ledger_is_loadable_and_queryable():
    registry = load()
    assert len(registry) > 5000
    assert len(registry.filter(provider="anthropic", model="claude-opus-5")) == 1


def test_bundled_source_fetched_at_reads_as_a_utc_instant():
    # The bundled ledger predates the instant change and carries day-only
    # fetched_at strings; they must load as timezone-aware UTC instants, not
    # naive datetimes or bare dates.
    registry = load()
    stamped = [s.fetched_at for s in registry.sources if s.fetched_at]
    assert stamped, "expected at least one reachable source with a timestamp"
    for fetched_at in stamped:
        assert fetched_at.tzinfo == timezone.utc
