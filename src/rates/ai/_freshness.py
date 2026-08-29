"""Which of models.dev, LiteLLM, and genai-prices last touched its own
data most recently, read from each source's public commit history.

Used only to break a price disagreement between the preferred source
(models.dev) and one fallback: whichever source's underlying data
changed more recently ships its value, instead of the preferred source
winning by declaration (see ARCHITECTURE.md § Resolving price
disagreements). Every function here degrades to ``None`` on any
failure, never raises: a freshness check is a quality signal, not a
requirement, and the fusion falls back to a fixed preference order
when one isn't available.

GitHub serves a commit-history feed per repo file, no auth required,
with no visible rate-limit headers distinct from ``api.github.com``'s
documented core budget (confirmed live, 2026-08-24): a separate,
cheaper channel from the one ``sync``/``live`` already use for GitHub's
REST API.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

CACHE_TTL_SECONDS = 3600
LOOKUP_TIMEOUT_SECONDS = 5.0

# A hard ceiling on per-record models.dev lookups in one fusion run, so a
# catalog with an unusually large disagreement count can't turn a single
# live() call into hundreds of sequential network round trips. Past the
# cap, remaining records fall straight to PRICE_PREFERENCE, same as any
# other unreachable freshness check.
MAX_RECORD_LOOKUPS_PER_RUN = 200

_USER_AGENT = "rates (+https://github.com/shehuphd/rates)"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Source-wide: one commit-history feed per file covers that source's
# whole dataset. Both are single bulk files, so freshness here is
# dataset-wide, not per-model.
_SOURCE_FEEDS = {
    "litellm": (
        "https://github.com/BerriAI/litellm/commits/main/"
        "model_prices_and_context_window.json.atom"
    ),
    "genai_prices": (
        "https://github.com/pydantic/genai-prices/commits/main/"
        "prices/data.json.atom"
    ),
}

# models.dev keeps one file per model, so its freshness is per-record,
# not per-dataset; the URL is built lazily, per lookup, from that
# record's own provider and model id.
_MODELS_DEV_FEED = (
    "https://github.com/anomalyco/models.dev/commits/dev/"
    "providers/{provider}/models/{model_id}.toml.atom"
)


def gather_source_freshness(
    statuses: dict[str, str],
    timeout: float | None = None,
) -> dict[str, date | None]:
    """The last-touched date for each single-file source's whole
    dataset, one feed fetch per source. A source not reachable this run,
    or whose own freshness check fails, reports None."""
    return {
        name: (
            _cached_freshness(url, timeout)
            if statuses.get(name) == "ok"
            else None
        )
        for name, url in _SOURCE_FEEDS.items()
    }


def record_freshness_lookup(
    timeout: float | None = None,
) -> Callable[[str, str], date | None]:
    """A callable the fusion invokes lazily, once per record that
    already shows a price disagreement, for models.dev's per-model
    freshness. Bounded to MAX_RECORD_LOOKUPS_PER_RUN network calls;
    beyond that it returns None without trying, same as any other
    unreachable check."""
    remaining = [MAX_RECORD_LOOKUPS_PER_RUN]

    def lookup(provider: str, model_id: str) -> date | None:
        if remaining[0] <= 0:
            return None
        remaining[0] -= 1
        url = _MODELS_DEV_FEED.format(
            provider=urllib.parse.quote(provider, safe=""),
            model_id=urllib.parse.quote(model_id, safe=""),
        )
        return _cached_freshness(url, timeout)

    return lookup


def _cached_freshness(url: str, timeout: float | None) -> date | None:
    cached = _read_cache(url)
    if cached is not None:
        return cached
    fetched = _fetch_freshness(url, timeout)
    if fetched is not None:
        _write_cache(url, fetched)
    return fetched


def _fetch_freshness(url: str, timeout: float | None) -> date | None:
    effective = min(timeout, LOOKUP_TIMEOUT_SECONDS) if timeout else LOOKUP_TIMEOUT_SECONDS
    body = _get_bytes(url, effective)
    if body is None:
        return None
    return _latest_commit_date(body)


def _get_bytes(url: str, timeout: float) -> bytes | None:
    """A single-attempt GET, never retried: this is a nice-to-have
    signal, not a required fetch, so any failure just means the caller
    treats freshness as unavailable rather than waiting out a retry
    ladder built for data that has to arrive."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body: bytes = response.read()
            return body
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _latest_commit_date(atom_bytes: bytes) -> date | None:
    try:
        root = ElementTree.fromstring(atom_bytes)
    except ElementTree.ParseError:
        return None
    entry = root.find(f"{_ATOM_NS}entry")
    if entry is None:
        return None
    updated = entry.find(f"{_ATOM_NS}updated")
    if updated is None or not updated.text:
        return None
    try:
        return datetime.fromisoformat(updated.text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _cache_path() -> Path:
    from .. import _cache

    return _cache.cache_dir() / "freshness.json"


def _read_cache(url: str) -> date | None:
    try:
        cache = json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cache, dict):
        return None
    entry = cache.get(url)
    if not isinstance(entry, dict):
        return None
    fetched_at, value = entry.get("fetched_at"), entry.get("date")
    if not fetched_at or not value:
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)
        return date.fromisoformat(value) if age.total_seconds() <= CACHE_TTL_SECONDS else None
    except ValueError:
        return None


def _write_cache(url: str, value: date) -> None:
    path = _cache_path()
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = None
    if not isinstance(cache, dict):
        cache = {}
    cache[url] = {
        "date": value.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.write_text(json.dumps(cache))
    except OSError:
        pass  # an unwritable cache dir costs the cache, never the result
