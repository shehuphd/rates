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
    """Every feed failed. The origin pricing pages aren't tried once the
    feeds are all down, since they cover a few vendors' rows and can't
    stand in for the catalog."""


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
    """The best local snapshot (the bundled ledger, or a newer cached
    ``stable`` download) is older than this domain's staleness
    threshold. The message names how stale and how to refresh."""


class SyncFallbackWarning(RatesWarning):
    """A fetch="stable" freshness check couldn't complete, and the local
    ledger is being served instead. The message names why the check
    failed."""


class SourceUnreachableWarning(RatesWarning):
    """A fetch="live" fusion ran with one or more non-preferred sources
    skipped. The preferred source was healthy (its absence raises
    instead), so a result was produced. A skipped feed leaves the records
    it corroborates, and the fields and units it supplies, absent; a skipped origin pricing page
    (unreachable, or fetched but yielding no records) leaves that
    vendor's records absent altogether. The message names which sources
    were skipped and which of the two applies."""
