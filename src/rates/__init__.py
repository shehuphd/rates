"""rates: a pricing registry and decision layer.

Exposes verified facts (price, capabilities, lifecycle) and leaves the
deciding to the caller. The AI universe lives at ``rates.ai``.
"""

from ._errors import (
    AllSourcesUnreachableError,
    LiveFusionError,
    PrimarySourceUnavailableError,
    RatesError,
    RatesWarning,
    StaleLedgerWarning,
    SyncFallbackWarning,
)

__version__ = "0.0.3"

__all__ = [
    "AllSourcesUnreachableError",
    "LiveFusionError",
    "PrimarySourceUnavailableError",
    "RatesError",
    "RatesWarning",
    "StaleLedgerWarning",
    "SyncFallbackWarning",
    "__version__",
]
