"""The AI universe's entry point: one ``load()``, three access tiers.

``load()`` reads the bundled ledger, zero network. ``load(sync=True)``
checks GitHub's Releases API for a newer published ledger, falling back to
the local one with a visible warning when the check can't complete, never
raising. ``load(live=True)`` runs the full fusion against the raw sources,
raising typed exceptions when it can't produce an honest result. All three
return the same ``Registry``.

Every duration check here anchors to UTC, never local wall-clock time: a
system's timezone shifting mid-session must not change when data reads as
stale or a cache as expired.
"""

from __future__ import annotations

import gzip
import json
import tempfile
import warnings
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Optional

from .._errors import StaleLedgerWarning, SyncFallbackWarning
from .._http import FetchError, fetch_json, validate_timeout
from ._fusion import SCHEMA_VERSION, fetch_sources, fuse
from ._registry import Registry

STALENESS_THRESHOLD_DAYS = 28
LIVE_CACHE_HOURS = 24
LEDGER_ASSET = "ledger-ai.json"
RELEASES_URL = "https://api.github.com/repos/shehuphd/rates/releases?per_page=10"

_BUNDLED_LEDGER = "ledger-ai.json.gz"


def load(
    *,
    sync: bool = False,
    live: bool = False,
    timeout: Optional[float] = None,
) -> Registry:
    """Load the AI universe's registry.

    ``sync`` and ``live`` are different promises and don't compose;
    passing both raises. ``timeout`` (seconds, up to 300) applies to the
    network tiers only.
    """
    if sync and live:
        raise ValueError(
            "sync and live are different promises and don't combine: "
            "sync checks for our newer published ledger, live refetches "
            "the raw sources; pick one"
        )
    validate_timeout(timeout)
    if live:
        return _load_live(timeout)
    if sync:
        return _load_sync(timeout)
    return _load_ledger()


def _load_ledger() -> Registry:
    data = _read_bundled()
    _warn_if_stale(data.get("snapshot_date"))
    return Registry.from_dict(data)


def _read_bundled() -> dict[str, Any]:
    raw = resources.files(__package__).joinpath(_BUNDLED_LEDGER).read_bytes()
    return json.loads(gzip.decompress(raw))


def _warn_if_stale(snapshot_date: Optional[str]) -> None:
    if not snapshot_date:
        return
    snapshot = datetime.fromisoformat(snapshot_date).date()
    age = (datetime.now(timezone.utc).date() - snapshot).days
    if age > STALENESS_THRESHOLD_DAYS:
        warnings.warn(
            f"the bundled AI-pricing ledger is {age} days old "
            f"(snapshot {snapshot_date}); prices may have moved. "
            "Refresh with rates.ai.load(sync=True) to fetch our newest "
            "published ledger, or rates.ai.load(live=True) to fuse the "
            "raw sources yourself; pip install -U rates also brings a "
            "newer bundled ledger",
            StaleLedgerWarning,
            stacklevel=3,
        )


# live: full fusion, cached per session for 24 hours


def _live_cache_path() -> Path:
    return Path(tempfile.gettempdir()) / "rates-live-ai.json"


def _load_live(timeout: Optional[float]) -> Registry:
    cached = _read_live_cache()
    if cached is not None:
        return Registry.from_dict(cached)

    payloads, statuses = fetch_sources(timeout=timeout)
    fused = fuse(payloads, statuses)
    try:
        _live_cache_path().write_text(
            json.dumps(
                {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "registry": fused,
                }
            )
        )
    except OSError:
        pass  # an unwritable temp dir costs the cache, never the result
    return Registry.from_dict(fused)


def _read_live_cache() -> Optional[dict[str, Any]]:
    try:
        envelope = json.loads(_live_cache_path().read_text())
        fetched_at = datetime.fromisoformat(envelope["fetched_at"])
        registry = envelope["registry"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if datetime.now(timezone.utc) - fetched_at > timedelta(hours=LIVE_CACHE_HOURS):
        return None
    return registry


# sync: cheap freshness check against our own published releases


def _load_sync(timeout: Optional[float]) -> Registry:
    local = _read_bundled()
    try:
        fetched = _fetch_newer_ledger(local.get("snapshot_date"), timeout)
    except FetchError as exc:
        warnings.warn(
            f"couldn't check for a newer ledger ({exc}); serving the "
            f"local one (snapshot {local.get('snapshot_date')})",
            SyncFallbackWarning,
            stacklevel=3,
        )
        _warn_if_stale(local.get("snapshot_date"))
        return Registry.from_dict(local)

    if fetched is None:
        return Registry.from_dict(local)

    if not _schema_compatible(fetched.get("schema_version")):
        warnings.warn(
            f"a newer published ledger exists but its schema "
            f"({fetched.get('schema_version')}) needs a newer rates than "
            f"this one ({SCHEMA_VERSION}); run pip install -U rates. "
            f"Serving the local ledger (snapshot "
            f"{local.get('snapshot_date')})",
            SyncFallbackWarning,
            stacklevel=3,
        )
        return Registry.from_dict(local)

    return Registry.from_dict(fetched)


def _fetch_newer_ledger(
    local_snapshot: Optional[str],
    timeout: Optional[float],
) -> Optional[dict[str, Any]]:
    """The newest published ledger's content, or None when the local one
    is already current. Raises FetchError when the check can't complete;
    the caller turns that into a warning, never an exception."""
    import os

    token = os.environ.get("GITHUB_TOKEN")
    releases = fetch_json(RELEASES_URL, timeout=timeout, token=token)
    if not isinstance(releases, list):
        raise FetchError("GitHub's releases API returned an unexpected shape")

    for release in releases:
        asset = next(
            (
                a
                for a in release.get("assets", [])
                if a.get("name") == LEDGER_ASSET
            ),
            None,
        )
        if asset is None:
            continue
        published = (release.get("published_at") or "")[:10]
        if local_snapshot and published and published <= local_snapshot:
            return None  # our local ledger is already current
        return fetch_json(asset["browser_download_url"], timeout=timeout)
    return None  # no ledger release published yet


def _schema_compatible(version: Optional[str]) -> bool:
    if not version:
        return False
    return version.split(".", 1)[0] == SCHEMA_VERSION.split(".", 1)[0]
