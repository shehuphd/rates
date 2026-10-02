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
    BundledSnapshotWarning,
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


# One notice per process that the default tier is serving a dated snapshot,
# so a caller pricing against it (a spend cap, say) learns it's not live data
# without a warning on every call. A newer snapshot in a long-lived process
# resets nothing here; the notice is about the tier, not the date.
_snapshot_noted = False


def _load_bundled() -> Registry:
    local = _best_local()
    snapshot_date = local.get("snapshot_date")
    # When the snapshot is stale, StaleLedgerWarning is the stronger signal
    # and already names the date and the fix, so the snapshot notice steps
    # aside to avoid a double warning on the same load.
    if not _warn_if_stale(snapshot_date):
        _note_bundled_snapshot(snapshot_date)
    return Registry.from_dict(local)


def _note_bundled_snapshot(snapshot_date: str | None) -> None:
    global _snapshot_noted
    if _snapshot_noted or not snapshot_date:
        return
    _snapshot_noted = True
    warnings.warn(
        f"the AI-pricing registry is serving its bundled snapshot "
        f"({snapshot_date}), the newest ledger already on this machine; "
        f"rates.ai.load(fetch='stable') checks for a newer published ledger "
        f"and fetch='live' fuses the raw sources directly",
        BundledSnapshotWarning,
        stacklevel=3,
    )


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


def _warn_if_stale(snapshot_date: str | None) -> bool:
    """Warn when the snapshot is past the staleness threshold. Returns
    whether a warning was emitted, so the caller can suppress the milder
    bundled-snapshot notice on the same load."""
    if not snapshot_date:
        return False
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
        return True
    return False


def _warn_unreachable_sources(statuses: dict[str, str]) -> None:
    """Name any fallback or validation feed ``fetch_sources`` couldn't
    reach. By the time this runs the preferred source is known healthy
    (its own absence raises upstream), so every feed named here is one
    whose contribution (corroborated records, fields, units, notes) may
    be absent from this fusion. Origin pages are reported separately
    (``_warn_skipped_origins``), since what goes missing with one is a
    vendor's whole record set. A best-effort tier reports what it's
    serving instead of failing silently."""
    skipped = sorted(
        name
        for name, status in statuses.items()
        if status != "ok" and _ROLES.get(name) not in ("preferred", "origin")
    )
    if not skipped:
        return
    warnings.warn(
        f"fused without {', '.join(skipped)}: "
        f"{'these feeds were' if len(skipped) > 1 else 'this feed was'} "
        "unreachable, so this result is thinner than a full fusion: "
        "any records admitted only with a skipped feed's corroboration are "
        "absent, the fields, price units, and discrepancy notes it "
        "supplies may be absent from others, and a contested price may "
        "resolve differently. This result is cached for 24 hours; "
        "rates.ai.load(fetch='live', force=True) refetches sooner",
        SourceUnreachableWarning,
        stacklevel=3,
    )


def _warn_skipped_origins(fused: dict[str, Any]) -> None:
    """Name any origin page that contributed nothing to this fusion,
    read from the fused envelope so both ways a page drops out are
    covered: ``unreachable`` (the fetch failed) and ``suspect`` (the page
    was fetched but yielded no records). Either way that vendor's
    records are absent from the result, which the caller should hear
    about rather than discover from an empty filter."""
    skipped = sorted(
        (source["name"], source["status"])
        for source in fused["sources"]
        if source.get("role") == "origin" and source["status"] != "ok"
    )
    if not skipped:
        return
    named = ", ".join(f"{name} ({status})" for name, status in skipped)
    warnings.warn(
        f"fused without {named}: the records read from "
        f"{'these pricing pages are' if len(skipped) > 1 else 'this pricing page are'} "
        "absent from this result, which is cached for 24 hours. An "
        "unreachable page may answer on a forced refetch, "
        "rates.ai.load(fetch='live', force=True); a suspect page was "
        "fetched but no longer parses, which a newer rates release "
        "corrects",
        SourceUnreachableWarning,
        stacklevel=3,
    )


# Caches live in a per-user 0700 directory (see rates._cache), never under
# a predictable name in the world-shared temp root, where another local
# user could pre-create the file and feed fabricated prices to everyone.

# live: full fusion, cached on disk for 24 hours


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
    _warn_skipped_origins(fused)
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
        registry = Registry.from_dict(fetched)
        # The staleness clock parses this date the same way on every
        # later load, so one it can't read must never reach the cache.
        datetime.fromisoformat(fetched["snapshot_date"])
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # Parsed before it's cached, so a ledger that doesn't read is
        # never kept as the local copy later checks compare against.
        warnings.warn(
            f"a newer published ledger exists but couldn't be read "
            f"({type(exc).__name__}: {exc}); serving the local one "
            f"(snapshot {local.get('snapshot_date')})",
            SyncFallbackWarning,
            stacklevel=3,
        )
        _warn_if_stale(local.get("snapshot_date"))
        return Registry.from_dict(local)

    try:
        _sync_cache_path().write_text(json.dumps(fetched))
    except OSError:
        pass  # an unwritable cache dir costs the cache, never the result
    return registry


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

    try:
        return _newer_ledger_from(releases, local_snapshot, timeout)
    except (AttributeError, KeyError, TypeError) as exc:
        # A release or asset entry of an unexpected form: the same
        # can't-complete outcome as a failed request, never a traceback.
        raise FetchError(
            "GitHub's releases API returned an unexpected shape"
        ) from exc


def _newer_ledger_from(
    releases: list[Any],
    local_snapshot: str | None,
    timeout: float | None,
) -> dict[str, Any] | None:
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
        if not isinstance(fetched, dict):
            raise FetchError("the published ledger asset isn't a JSON object")
        return fetched
    return None  # no ledger release published yet


def _schema_compatible(version: str | None) -> bool:
    if not version or not isinstance(version, str):
        return False
    return version.split(".", 1)[0] == SCHEMA_VERSION.split(".", 1)[0]
