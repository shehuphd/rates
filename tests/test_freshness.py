"""Tests for _freshness.py: Atom feed parsing, the on-disk cache and its
TTL, and the two public lookups' degrade-to-None behavior. Network calls
are never made here — _get_bytes is monkeypatched throughout."""

import json
from datetime import date, datetime, timedelta, timezone

from rates.ai import _freshness

ATOM_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:github.com,2008:Grit::Commit/abc123</id>
    <updated>{updated}</updated>
  </entry>
  <entry>
    <id>tag:github.com,2008:Grit::Commit/def456</id>
    <updated>2020-01-01T00:00:00Z</updated>
  </entry>
</feed>
"""


def _atom(updated="2026-08-20T12:00:00Z"):
    return ATOM_TEMPLATE.format(updated=updated).encode()


# _latest_commit_date


def test_latest_commit_date_reads_the_first_entrys_updated_field():
    assert _freshness._latest_commit_date(_atom()) == date(2026, 8, 20)


def test_latest_commit_date_on_malformed_xml_returns_none():
    assert _freshness._latest_commit_date(b"not xml at all") is None


def test_latest_commit_date_with_no_entries_returns_none():
    empty_feed = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    assert _freshness._latest_commit_date(empty_feed) is None


def test_latest_commit_date_missing_updated_element_returns_none():
    feed = (
        b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
        b"<entry><id>x</id></entry></feed>"
    )
    assert _freshness._latest_commit_date(feed) is None


def test_latest_commit_date_unparseable_updated_text_returns_none():
    assert _freshness._latest_commit_date(_atom(updated="not-a-date")) is None


# _get_bytes never raises


def test_get_bytes_on_network_failure_returns_none(monkeypatch):
    import urllib.error

    def raise_it(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(_freshness.urllib.request, "urlopen", raise_it)
    assert _freshness._get_bytes("https://example.test/feed.atom", 5.0) is None


def test_get_bytes_on_timeout_returns_none(monkeypatch):
    def raise_it(request, timeout=None):
        raise TimeoutError

    monkeypatch.setattr(_freshness.urllib.request, "urlopen", raise_it)
    assert _freshness._get_bytes("https://example.test/feed.atom", 5.0) is None


# Cache round-trip and TTL


def test_cache_write_then_read_round_trips(isolated_rates_cache):
    url = "https://example.test/a.atom"
    _freshness._write_cache(url, date(2026, 8, 20))
    assert _freshness._read_cache(url) == date(2026, 8, 20)


def test_cache_miss_for_an_unwritten_url_returns_none(isolated_rates_cache):
    assert _freshness._read_cache("https://example.test/never-written.atom") is None


def test_cache_entry_past_the_ttl_returns_none(isolated_rates_cache):
    url = "https://example.test/a.atom"
    _freshness._write_cache(url, date(2026, 8, 20))
    cache_path = isolated_rates_cache / "freshness.json"
    envelope = json.loads(cache_path.read_text())
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=_freshness.CACHE_TTL_SECONDS + 1
    )
    envelope[url]["fetched_at"] = stale.isoformat()
    cache_path.write_text(json.dumps(envelope))
    assert _freshness._read_cache(url) is None


def test_corrupt_cache_file_is_treated_as_a_miss_not_a_crash(isolated_rates_cache):
    isolated_rates_cache.mkdir(parents=True, exist_ok=True)
    cache_path = isolated_rates_cache / "freshness.json"
    cache_path.write_text("not json{")
    assert _freshness._read_cache("https://example.test/a.atom") is None


def test_cached_freshness_skips_the_network_on_a_hit(monkeypatch, isolated_rates_cache):
    url = "https://example.test/a.atom"
    _freshness._write_cache(url, date(2026, 8, 20))
    calls = []
    monkeypatch.setattr(
        _freshness, "_get_bytes", lambda url, timeout: calls.append(url) or _atom()
    )
    assert _freshness._cached_freshness(url, None) == date(2026, 8, 20)
    assert calls == []


def test_cached_freshness_fetches_and_caches_on_a_miss(monkeypatch, isolated_rates_cache):
    url = "https://example.test/a.atom"
    monkeypatch.setattr(_freshness, "_get_bytes", lambda url, timeout: _atom())
    assert _freshness._cached_freshness(url, None) == date(2026, 8, 20)
    assert _freshness._read_cache(url) == date(2026, 8, 20)


# gather_source_freshness


def test_gather_source_freshness_skips_sources_not_ok(monkeypatch, isolated_rates_cache):
    fetched = []
    monkeypatch.setattr(
        _freshness, "_get_bytes", lambda url, timeout: fetched.append(url) or _atom()
    )
    result = _freshness.gather_source_freshness(
        {"litellm": "ok", "genai_prices": "unreachable"}
    )
    assert result["litellm"] == date(2026, 8, 20)
    assert result["genai_prices"] is None
    assert len(fetched) == 1


def test_gather_source_freshness_a_failed_fetch_reports_none(
    monkeypatch, isolated_rates_cache
):
    monkeypatch.setattr(_freshness, "_get_bytes", lambda url, timeout: None)
    result = _freshness.gather_source_freshness({"litellm": "ok", "genai_prices": "ok"})
    assert result == {"litellm": None, "genai_prices": None}


# record_freshness_lookup


def test_record_freshness_lookup_returns_a_date_per_record(
    monkeypatch, isolated_rates_cache
):
    monkeypatch.setattr(_freshness, "_get_bytes", lambda url, timeout: _atom())
    lookup = _freshness.record_freshness_lookup()
    assert lookup("anthropic", "claude-opus-5") == date(2026, 8, 20)


def test_record_freshness_lookup_stops_after_the_cap(monkeypatch, isolated_rates_cache):
    calls = []
    monkeypatch.setattr(
        _freshness, "_get_bytes", lambda url, timeout: calls.append(url) or _atom()
    )
    monkeypatch.setattr(_freshness, "MAX_RECORD_LOOKUPS_PER_RUN", 2)
    lookup = _freshness.record_freshness_lookup()
    results = [lookup("p", f"model-{i}") for i in range(4)]
    assert results == [date(2026, 8, 20), date(2026, 8, 20), None, None]
    assert len(calls) == 2
