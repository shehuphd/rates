"""rates: a pricing registry and decision layer.

Exposes verified facts (price, capabilities, lifecycle) and leaves the
deciding to the caller. The AI domain lives at ``rates.ai``.
"""

from ._errors import (
    AllSourcesUnreachableError,
    LiveFusionError,
    PreferredSourceUnavailableError,
    RatesError,
    RatesWarning,
    SourceUnreachableWarning,
    StaleLedgerWarning,
    SyncFallbackWarning,
)

__version__ = "1.0.0"

__all__ = [
    "AllSourcesUnreachableError",
    "LiveFusionError",
    "PreferredSourceUnavailableError",
    "RatesError",
    "RatesWarning",
    "SourceUnreachableWarning",
    "StaleLedgerWarning",
    "SyncFallbackWarning",
    "__version__",
]
