"""A small stdlib HTTP helper for sync and live.

Hand-rolled on urllib so the package carries no HTTP dependency: the whole
job is a handful of sequential JSON GETs, none of the pooling, streaming,
or HTTP/2 a client library would add.

Retry policy: the slow connection is the design case, not the edge case,
so the timeout ladder starts generous (a first attempt short enough to
save a fast user a few seconds would guarantee-fail the slow user these
numbers are for) and escalates from there. Server-side transient failures
(429, 5xx) retry with backoff, since those resolve on the server's
schedule. Clean errors (404, malformed JSON) never retry; no wait fixes
those. A volatile universe (forex, say) can later override the ladder and
statuses as its own profile; the mechanism stays this one.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_LADDER = (30, 60, 120)
MAX_TIMEOUT = 300
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
BACKOFF_SECONDS = (1, 2)
RETRY_AFTER_CAP = 60

_USER_AGENT = "rates (+https://github.com/shehuphd/rates)"

# Module-level so tests can patch the pause out.
_sleep = time.sleep


class FetchError(Exception):
    """A URL couldn't be fetched or didn't return usable JSON. Carries the
    reason; callers classify it per-source rather than letting transport
    details leak upward."""


def validate_timeout(timeout: float | None) -> float | None:
    """Check a caller-supplied timeout against the ceiling. None means the
    ladder's own rungs apply unchanged."""
    if timeout is None:
        return None
    if timeout <= 0:
        raise ValueError(f"timeout must be positive, got {timeout}")
    if timeout > MAX_TIMEOUT:
        raise ValueError(
            f"timeout can't exceed {MAX_TIMEOUT} seconds, got {timeout}"
        )
    return timeout


def fetch_json(
    url: str,
    timeout: float | None = None,
    token: str | None = None,
) -> Any:
    """GET a URL and parse its body as JSON.

    Up to three attempts on escalating timeouts (30s, 60s, 120s; a caller
    timeout above a rung replaces that rung). Timeouts, connection errors,
    and transient HTTP statuses (429, 500, 502, 503, 504) retry with a
    short backoff pause, honoring a Retry-After header up to a cap. Clean
    error responses and malformed JSON never retry. Raises FetchError with
    the reason when every attempt fails.
    """
    caller = validate_timeout(timeout)
    rungs = [max(r, caller) if caller is not None else r for r in TIMEOUT_LADDER]
    final = len(rungs) - 1

    for attempt, rung in enumerate(rungs):
        try:
            body = _get(url, rung, token)
        except urllib.error.HTTPError as exc:
            if exc.code in TRANSIENT_STATUSES and attempt < final:
                _sleep(_retry_delay(attempt, exc.headers.get("Retry-After")))
                continue
            raise FetchError(f"{url}: HTTP {exc.code}") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt < final:
                _sleep(BACKOFF_SECONDS[attempt])
                continue
            raise FetchError(f"{url}: unreachable ({exc})") from exc

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise FetchError(
                f"{url}: response wasn't valid JSON ({exc})"
            ) from exc

    raise AssertionError("unreachable: the final attempt always raises or returns")


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    """Backoff before the next attempt: the ladder's pause, or the server's
    Retry-After when it asks for longer, capped so a hostile or broken
    header can't stall the caller for minutes."""
    delay = float(BACKOFF_SECONDS[attempt])
    if retry_after is not None:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            pass  # HTTP-date form or garbage; the ladder's pause stands
    return min(delay, RETRY_AFTER_CAP)


def _get(url: str, timeout: float, token: str | None) -> bytes:
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body: bytes = response.read()
        return body
