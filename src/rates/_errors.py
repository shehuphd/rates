"""Exception and warning types shared across universes.

Distinct types for distinct situations, so a caller can catch or filter
broadly (the base classes) or specifically. ``sync`` never raises any of
the exceptions here; ``live`` does, since it's the mode that promises a
result or an explanation of why not.
"""


class RatesError(Exception):
    """Base for everything rates raises."""


class LiveFusionError(RatesError):
    """A live=True call couldn't produce a result."""


class AllSourcesUnreachableError(LiveFusionError):
    """Every upstream source failed."""


class PreferredSourceUnavailableError(LiveFusionError):
    """The preferred source specifically failed. Even with the fallbacks
    healthy, the result would be missing most fields per ERD.md's source
    map, so it's refused rather than returned looking successful."""


class RatesWarning(Warning):
    """Base for every warning rates emits, so one filter covers them all."""


class StaleLedgerWarning(RatesWarning):
    """The bundled ledger is older than this universe's staleness
    threshold. The message names how stale and how to refresh."""


class SyncFallbackWarning(RatesWarning):
    """A sync=True freshness check couldn't complete, and the local ledger
    is being served instead. The message names why the check failed."""
