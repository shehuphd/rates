"""Tests for load()'s three tiers: mode validation, staleness warnings,
the live cache, and stable's fall-back-with-a-warning behavior."""

import json
import warnings
from datetime import datetime, timedelta, timezone

import pytest

from rates import (
    BundledSnapshotWarning,
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
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load()
    assert not [w for w in caught if issubclass(w.category, StaleLedgerWarning)]


def test_first_bundled_load_notes_the_snapshot(bundled):
    load_module._snapshot_noted = False
    with pytest.warns(BundledSnapshotWarning, match="serving its bundled snapshot.*fetch='stable'"):
        load()


def test_bundled_snapshot_notice_fires_once_per_process(bundled):
    load_module._snapshot_noted = False
    with pytest.warns(BundledSnapshotWarning):
        load()
    # A second bundled load in the same process is silent.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load()
    assert not [w for w in caught if issubclass(w.category, BundledSnapshotWarning)]


def test_stale_bundled_load_suppresses_the_snapshot_notice(bundled):
    old = (datetime.now(timezone.utc).date() - timedelta(days=40)).isoformat()
    bundled["data"] = {**FRESH, "snapshot_date": old}
    load_module._snapshot_noted = False
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load()
    assert any(issubclass(w.category, StaleLedgerWarning) for w in caught)
    assert not [w for w in caught if issubclass(w.category, BundledSnapshotWarning)]


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
    assert len(fused_live) == 1  # second call served from the 24-hour on-disk cache


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


def _live_with_envelope_sources(monkeypatch, sources):
    """The live path with every fetch healthy and the fused envelope
    carrying the given source rows, so origin-page statuses (which the
    fusion decides, a parse can fail after a good fetch) reach the
    warning."""
    _live_with_statuses(monkeypatch, {"models_dev": "ok"})
    monkeypatch.setattr(
        load_module,
        "fuse",
        lambda payloads, statuses, **kwargs: {**FRESH, "sources": sources},
    )


def test_live_warns_that_a_skipped_origin_page_costs_its_records(
    isolated_cache, monkeypatch
):
    _live_with_envelope_sources(
        monkeypatch,
        [
            {"name": "models_dev", "role": "preferred", "status": "ok"},
            {"name": "deepgram_pricing", "role": "origin", "status": "unreachable"},
            {"name": "livekit_pricing", "role": "origin", "status": "suspect"},
            {"name": "elevenlabs_pricing", "role": "origin", "status": "ok"},
        ],
    )
    with pytest.warns(SourceUnreachableWarning) as caught:
        load(fetch="live")
    (message,) = [str(w.message) for w in caught]
    assert "deepgram_pricing (unreachable)" in message
    assert "livekit_pricing (suspect)" in message
    assert "elevenlabs_pricing" not in message
    # What goes missing is whole records, never "fields they enrich".
    assert "records" in message and "absent" in message
    assert "enrich" not in message


def test_live_origin_fetch_failure_is_not_reported_as_missing_fields(
    isolated_cache, monkeypatch
):
    # An origin page's fetch status reaches fetch_sources' statuses too;
    # the feed warning must leave it to the origin warning.
    _live_with_statuses(
        monkeypatch, {"models_dev": "ok", "deepgram_pricing": "unreachable"}
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load(fetch="live")
    assert not [w for w in caught if issubclass(w.category, SourceUnreachableWarning)]


def test_live_does_not_warn_when_every_source_is_reachable(
    isolated_cache, monkeypatch
):
    _live_with_statuses(
        monkeypatch,
        {"models_dev": "ok", "genai_prices": "ok", "litellm": "ok",
         "openrouter": "ok"},
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load(fetch="live")  # a fully-healthy fusion stays quiet
    assert not [w for w in caught if issubclass(w.category, SourceUnreachableWarning)]


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


@pytest.mark.parametrize(
    "releases",
    [
        {"message": "API rate limit exceeded"},
        ["not a release object"],
        [{"published_at": "2030-01-01T00:00:00Z", "assets": ["not an asset"]}],
        [{"published_at": "2030-01-01T00:00:00Z", "assets": None}],
        [{"published_at": "2030-01-01T00:00:00Z",
          "assets": [{"name": "ledger-ai.json"}]}],
        [{"tag_name": 20300101, "assets": [{"name": "ledger-ai.json"}]}],
    ],
)
def test_stable_falls_back_on_any_malformed_releases_payload(
    bundled, monkeypatch, releases
):
    _patch_sync_fetch(monkeypatch, releases)
    with pytest.warns(SyncFallbackWarning, match="unexpected shape"):
        registry = load(fetch="stable")
    assert len(registry) == 1


def test_stable_falls_back_when_the_published_asset_is_not_an_object(
    bundled, monkeypatch
):
    _patch_sync_fetch(monkeypatch, [_release("2030-01-01")], ledger=["not a ledger"])
    with pytest.warns(SyncFallbackWarning, match="isn't a JSON object"):
        registry = load(fetch="stable")
    assert len(registry) == 1


@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        ({**FRESH, "models": [{"provider": "x"}, "not a record"]}, "couldn't be read"),
        ({**FRESH, "schema_version": 1}, "schema"),
        ({**FRESH, "snapshot_date": "2099-01"}, "couldn't be read"),
        ({k: v for k, v in FRESH.items() if k != "snapshot_date"}, "couldn't be read"),
        ({**FRESH, "models": [{
            "provider": "x", "id": "a",
            "alias": {"convention": "c", "verified": None, "note": "n"},
        }]}, "couldn't be read"),
    ],
)
def test_stable_falls_back_and_caches_nothing_when_the_new_ledger_does_not_parse(
    bundled, monkeypatch, broken, reason
):
    _patch_sync_fetch(monkeypatch, [_release("2030-01-01")], ledger=broken)
    with pytest.warns(SyncFallbackWarning, match=reason):
        registry = load(fetch="stable")
    assert len(registry) == 1
    assert not load_module._sync_cache_path().exists()


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
    # The bundled ledger's fetched_at strings must load as timezone-aware
    # UTC instants, not naive datetimes or bare dates.
    registry = load()
    stamped = [s.fetched_at for s in registry.sources if s.fetched_at]
    assert stamped, "expected at least one reachable source with a timestamp"
    for fetched_at in stamped:
        assert fetched_at.tzinfo == timezone.utc


# GITHUB_TOKEN pickup


def test_stable_sends_the_environments_github_token_to_the_releases_api_only(
    bundled, monkeypatch
):
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    tokens = {}

    def fake(url, timeout=None, token=None):
        tokens[url] = token
        if "api.github.com" in url:
            return [
                {
                    "tag_name": f"ledger-{tomorrow}",
                    "assets": [
                        {
                            "name": "ledger-ai.json",
                            "browser_download_url": "https://example.test/l.json",
                        }
                    ],
                }
            ]
        return {**FRESH, "snapshot_date": tomorrow}

    monkeypatch.setattr(load_module, "fetch_json", fake)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-not-a-credential")
    load(fetch="stable")
    assert tokens[load_module.RELEASES_URL] == "test-token-not-a-credential"
    assert tokens["https://example.test/l.json"] is None


def test_stable_sends_no_token_when_the_environment_has_none(bundled, monkeypatch):
    tokens = {}

    def fake(url, timeout=None, token=None):
        tokens[url] = token
        return []

    monkeypatch.setattr(load_module, "fetch_json", fake)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    load(fetch="stable")
    assert tokens == {load_module.RELEASES_URL: None}
