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

import functools
from collections.abc import Callable
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])

# traceact's import chain is ~100ms (it pulls urllib/http/email for its
# sinks), so importing it is deferred to the first call of a traced
# function, never at module import. A process that never runs a traced
# function, tab completion above all, never pays for it. `_UNRESOLVED`
# means "not looked up yet"; None means "looked up, traceact absent"; a
# callable is traceact's traced_action. Tests monkeypatch this to None to
# exercise the absent path, so the sentinel must not overwrite that.
_UNRESOLVED: Any = object()
_traced_action: Any = _UNRESOLVED


def _resolve_traced_action() -> Callable[..., Any] | None:
    global _traced_action
    if _traced_action is _UNRESOLVED:
        try:
            from traceact import traced_action

            _traced_action = traced_action
        except ImportError:
            _traced_action = None
    return cast("Callable[..., Any] | None", _traced_action)


def traced(action: str, **kwargs: Any) -> Callable[[F], F]:
    """traceact's traced_action when available, identity otherwise. The
    traceact lookup happens on the wrapped function's first call, not at
    decoration time, so importing this module stays cheap."""

    def decorator(fn: F) -> F:
        wrapped: Callable[..., Any] | None = None

        @functools.wraps(fn)
        def lazy(*args: Any, **call_kwargs: Any) -> Any:
            nonlocal wrapped
            if wrapped is None:
                impl = _resolve_traced_action()
                wrapped = (
                    fn
                    if impl is None
                    else impl(action, project="rates", **kwargs)(fn)
                )
            return wrapped(*args, **call_kwargs)

        return cast("F", lazy)

    return decorator


def configure_cli_tracing() -> None:
    """Route the CLI's traces to a JSONL file when nothing else is set.

    traceact with no sinks configured falls back to printing traces on
    the console, the right developer default for an app, and noise on a
    CLI's stdout. In CLI context rates is the consuming app, so it sets a
    quiet file sink, but only when the surrounding environment hasn't
    configured its own; an app's configure() always wins. Library imports
    never touch tracing config at all.
    """
    if _resolve_traced_action() is None:
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
