"""Exception and warning types shared across domains.

Distinct types for distinct situations, so a caller can catch or filter
broadly (the base classes) or specifically. ``fetch="stable"`` never
raises any of the exceptions here; ``fetch="live"`` does, since it's the
mode that promises a result or an explanation of why not.
"""


class RatesError(Exception):
    """Base for everything rates raises."""


class LiveFusionError(RatesError):
    """A fetch="live" call couldn't produce a result."""


class AllSourcesUnreachableError(LiveFusionError):
    """Every upstream source failed."""


class PreferredSourceUnavailableError(LiveFusionError):
    """The preferred source specifically failed. Even with the fallbacks
    healthy, the result would be missing most fields per ERD.md's source
    map, so it's refused rather than returned looking successful."""


class RatesWarning(Warning):
    """Base for every warning rates emits, so one filter covers them all."""


class BundledSnapshotWarning(RatesWarning):
    """A default ``load()`` served the bundled snapshot, emitted once per
    process on the first such call. The message names the snapshot date and
    the opt-in tiers, so a caller relying on current prices (a spend cap,
    say) knows it's reading a dated snapshot rather than live data. It fires
    even when the snapshot is fresh; a caller that has chosen offline data
    on purpose can silence it with a ``RatesWarning`` filter. When the
    snapshot is also past the staleness threshold, ``StaleLedgerWarning``
    carries the stronger signal and this one is suppressed."""


class StaleLedgerWarning(RatesWarning):
    """The bundled ledger is older than this domain's staleness
    threshold. The message names how stale and how to refresh."""


class SyncFallbackWarning(RatesWarning):
    """A fetch="stable" freshness check couldn't complete, and the local
    ledger is being served instead. The message names why the check
    failed."""


class SourceUnreachableWarning(RatesWarning):
    """A fetch="live" fusion ran with one or more non-preferred sources
    unreachable. The preferred source was healthy (its absence raises
    instead), so a result was produced, but the fields those sources
    enrich may be absent from it. The message names which sources were
    skipped."""
