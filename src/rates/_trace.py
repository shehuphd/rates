"""Optional traceact instrumentation.

traceact is a soft dependency: when it's installed alongside rates, key
operations record traces; when it isn't, the decorator below is an
identity and nothing changes. rates never imports it as a requirement,
never configures sinks (that's the consuming app's decision), and never
fails because tracing is unavailable.

Verified against traceact 1.0.0's public API (traced_action) on
2026-08-23.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])

try:
    from traceact import traced_action as _traced_action
except ImportError:
    _traced_action = None  # type: ignore[assignment]


def traced(action: str, **kwargs: Any) -> Callable[[F], F]:
    """traceact's traced_action when available, identity otherwise."""
    if _traced_action is None:
        return lambda fn: fn
    return cast(
        "Callable[[F], F]", _traced_action(action, project="rates", **kwargs)
    )


def configure_cli_tracing() -> None:
    """Route the CLI's traces to a JSONL file when nothing else is set.

    traceact with no sinks configured falls back to printing traces on
    the console, the right developer default for an app, and noise on a
    CLI's stdout. In CLI context rates is the consuming app, so it sets a
    quiet file sink, but only when the surrounding environment hasn't
    configured its own; an app's configure() always wins. Library imports
    never touch tracing config at all.
    """
    if _traced_action is None:
        return
    from pathlib import Path

    from traceact import JsonlSink, configure
    from traceact.config import get_package_sinks

    if get_package_sinks():
        return
    path = Path.home() / ".traceact" / "rates.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        configure(sinks=[JsonlSink(str(path), max_bytes=10_000_000)])
    except OSError:
        pass  # an unwritable home dir costs the traces, never the command
