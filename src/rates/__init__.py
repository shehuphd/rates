"""rates: a pricing registry and decision layer.

Exposes verified facts (price, capabilities, lifecycle) and leaves the
deciding to the caller. The AI domain lives at ``rates.ai``.
"""

from ._errors import (
    AllSourcesUnreachableError,
    BundledSnapshotWarning,
    LiveFusionError,
    PreferredSourceUnavailableError,
    RatesError,
    RatesWarning,
    SourceUnreachableWarning,
    StaleLedgerWarning,
    SyncFallbackWarning,
)

__version__ = "1.0.2"

__all__ = [
    "AllSourcesUnreachableError",
    "BundledSnapshotWarning",
    "LiveFusionError",
    "PreferredSourceUnavailableError",
    "RatesError",
    "RatesWarning",
    "SourceUnreachableWarning",
    "StaleLedgerWarning",
    "SyncFallbackWarning",
    "__version__",
]
