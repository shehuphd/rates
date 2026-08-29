"""The AI domain's entry point: one ``load()``, three access tiers.

``load()`` (``fetch="bundled"``, the default) resolves to the best
snapshot already on this machine: the one installed with the package, or
a newer one a prior ``fetch="stable"`` call already downloaded. Zero
network. ``load(fetch="stable")`` checks GitHub's Releases API for a
newer published ledger, falling back to the local one with a visible
warning when the check can't complete, never raising.
``load(fetch="live")`` runs the full fusion against the raw sources,
raising typed exceptions when it can't produce an honest result. All
three return the same ``Registry``.

Every duration check here anchors to UTC, never local wall-clock time: a
system's timezone shifting mid-session must not change when data reads as
stale or a cache as expired.
"""

from __future__ import annotations

import gzip
import json
import warnings
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from .. import _cache
from .._errors import (
    SourceUnreachableWarning,
    StaleLedgerWarning,
    SyncFallbackWarning,
)
from .._http import FetchError, fetch_json, validate_timeout
from .._trace import traced
from ._freshness import gather_source_freshness, record_freshness_lookup
from ._fusion import _ROLES, SCHEMA_VERSION, fetch_sources, fuse
from ._registry import Registry

STALENESS_THRESHOLD_DAYS = 28
LIVE_CACHE_HOURS = 24
LEDGER_ASSET = "ledger-ai.json"
RELEASES_URL = "https://api.github.com/repos/shehuphd/rates/releases?per_page=10"

_BUNDLED_LEDGER = "ledger-ai.json.gz"


@traced("registry.load")
def load(
    *,
    fetch: Literal["bundled", "stable", "live"] = "bundled",
    timeout: float | None = None,
    force: bool = False,
) -> Registry:
    """Load the AI domain's registry.

    ``fetch`` picks one of three tiers, not a combination: ``"bundled"``
    (the default) is the best snapshot already on this machine, offline;
    ``"stable"`` checks for a newer one we've published; ``"live"`` fuses
    the raw sources directly. ``timeout`` (seconds, up to 300) applies to
    the network tiers only. ``force`` skips ``"live"``'s 24-hour cache and
    fuses fresh regardless of how recent the cached result is, for a
    volatile domain (crypto, say) where "an hour old" is already wrong
    after news moves a price; it's an error with any other ``fetch``, since
    ``"bundled"`` and ``"stable"`` don't hold a cached result of their own
    for it to bypass.
    """
    if fetch not in ("bundled", "stable", "live"):
        raise ValueError(
            f"fetch must be 'bundled', 'stable', or 'live', got {fetch!r}"
        )
    if force and fetch != "live":
        raise ValueError(
            "force only applies to fetch='live'; 'bundled' and 'stable' "
            "don't cache a result for it to bypass"
        )
    validate_timeout(timeout)
    if fetch == "live":
        return _load_live(timeout, force=force)
    if fetch == "stable":
        return _load_stable(timeout)
    return _load_bundled()


def _load_bundled() -> Registry:
    local = _best_local()
    _warn_if_stale(local.get("snapshot_date"))
    return Registry.from_dict(local)


def _best_local() -> dict[str, Any]:
    """The best snapshot already on this machine: the one installed with
    the package, or a newer one a prior ``fetch="stable"`` call already
    downloaded and cached. Shared by the bundled and stable tiers so a
    successful stable fetch benefits both, not just the call that made
    it."""
    local = _read_bundled()
    cached = _read_sync_cache()
    if cached is not None and (cached.get("snapshot_date") or "") > (
        local.get("snapshot_date") or ""
    ):
        local = cached
    return local


def _read_bundled() -> dict[str, Any]:
    raw = resources.files(__package__).joinpath(_BUNDLED_LEDGER).read_bytes()
    data: dict[str, Any] = json.loads(gzip.decompress(raw))
    return data


def _warn_if_stale(snapshot_date: str | None) -> None:
    if not snapshot_date:
        return
    snapshot = datetime.fromisoformat(snapshot_date).date()
    age = (datetime.now(timezone.utc).date() - snapshot).days
    if age > STALENESS_THRESHOLD_DAYS:
        warnings.warn(
            f"the AI-pricing ledger is {age} days old "
            f"(snapshot {snapshot_date}); prices may have moved. "
            "Refresh with rates.ai.load(fetch='stable') to fetch our "
            "newest published ledger, or rates.ai.load(fetch='live') to "
            "fuse the raw sources yourself; pip install -U rates also "
            "brings a newer bundled ledger",
            StaleLedgerWarning,
            stacklevel=3,
        )


def _warn_unreachable_sources(statuses: dict[str, str]) -> None:
    """Name any non-preferred source that ``fetch_sources`` couldn't
    reach. By the time this runs the preferred source is known healthy
    (its own absence raises upstream), so every unreachable source here is
    a fallback whose fields may be absent from this fusion. A best-effort
    tier reports what it's serving instead of failing silently."""
    skipped = sorted(
        name
        for name, status in statuses.items()
        if status != "ok" and _ROLES.get(name) != "preferred"
    )
    if not skipped:
        return
    warnings.warn(
        f"fused without {', '.join(skipped)}: "
        f"{'these sources were' if len(skipped) > 1 else 'this source was'} "
        "unreachable, so the fields they enrich (model type, reasoning "
        "defaults, whether a reasoning-effort parameter is required) may be "
        "absent for some models. The result is otherwise complete; retry "
        "later for full enrichment",
        SourceUnreachableWarning,
        stacklevel=3,
    )


# Caches live in a per-user 0700 directory (see rates._cache), never under
# a predictable name in the world-shared temp root, where another local
# user could pre-create the file and feed fabricated prices to everyone.

# live: full fusion, cached per session for 24 hours


def _live_cache_path() -> Path:
    return _cache.cache_dir() / "live-ai.json"


def _load_live(timeout: float | None, force: bool = False) -> Registry:
    if not force:
        cached = _read_live_cache()
        if cached is not None:
            return Registry.from_dict(cached)

    payloads, statuses = fetch_sources(timeout=timeout)
    _warn_unreachable_sources(statuses)
    fused = fuse(
        payloads,
        statuses,
        source_freshness=gather_source_freshness(statuses, timeout=timeout),
        record_freshness=record_freshness_lookup(timeout=timeout),
    )
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
        pass  # an unwritable cache dir costs the cache, never the result
    return Registry.from_dict(fused)


def _read_live_cache() -> dict[str, Any] | None:
    try:
        envelope = json.loads(_live_cache_path().read_text())
        fetched_at = datetime.fromisoformat(envelope["fetched_at"])
        registry = envelope["registry"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if datetime.now(timezone.utc) - fetched_at > timedelta(hours=LIVE_CACHE_HOURS):
        return None
    if not isinstance(registry, dict) or not _schema_compatible(
        registry.get("schema_version")
    ):
        return None  # written by a different rates version; refetch
    return registry


# stable: cheap freshness check against our own published releases


def _sync_cache_path() -> Path:
    return _cache.cache_dir() / "sync-ai.json"


def _load_stable(timeout: float | None) -> Registry:
    local = _best_local()

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
        _warn_if_stale(local.get("snapshot_date"))
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
        _warn_if_stale(local.get("snapshot_date"))
        return Registry.from_dict(local)

    try:
        _sync_cache_path().write_text(json.dumps(fetched))
    except OSError:
        pass  # an unwritable cache dir costs the cache, never the result
    return Registry.from_dict(fetched)


def _read_sync_cache() -> dict[str, Any] | None:
    try:
        cached = json.loads(_sync_cache_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or not _schema_compatible(
        cached.get("schema_version")
    ):
        return None
    return cached


def _fetch_newer_ledger(
    local_snapshot: str | None,
    timeout: float | None,
) -> dict[str, Any] | None:
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
        # The tag carries the snapshot date (ledger-YYYY-MM-DD), so this
        # compares snapshot to snapshot; a ledger built one day and
        # released the next doesn't look newer than its own content. The
        # publish date is the fallback for an unexpected tag shape.
        tag = release.get("tag_name") or ""
        snapshot = (
            tag.removeprefix("ledger-")
            if tag.startswith("ledger-")
            else (release.get("published_at") or "")[:10]
        )
        if local_snapshot and snapshot and snapshot <= local_snapshot:
            return None  # our local ledger is already current
        fetched = fetch_json(asset["browser_download_url"], timeout=timeout)
        return fetched if isinstance(fetched, dict) else None
    return None  # no ledger release published yet


def _schema_compatible(version: str | None) -> bool:
    if not version:
        return False
    return version.split(".", 1)[0] == SCHEMA_VERSION.split(".", 1)[0]
