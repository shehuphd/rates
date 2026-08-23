"""Tests for the stdlib HTTP helper: timeout ladder, transient-status
retries, and backoff, failure paths first."""

import email.message
import urllib.error

import pytest

from rates._http import (
    MAX_TIMEOUT,
    TIMEOUT_LADDER,
    FetchError,
    fetch_json,
    validate_timeout,
)


def _http_error(code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.test/x", code, "err", headers, None)


@pytest.fixture
def transport(monkeypatch):
    """Scripted transport: records each attempt's timeout and each backoff
    pause; yields per-attempt results or raises per-attempt exceptions."""

    class Transport:
        def __init__(self):
            self.responses = []
            self.timeouts = []
            self.pauses = []

        def script(self, *responses):
            self.responses = list(responses)

    t = Transport()

    def fake_get(url, timeout, token):
        t.timeouts.append(timeout)
        result = t.responses[len(t.timeouts) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("rates._http._get", fake_get)
    monkeypatch.setattr("rates._http._sleep", t.pauses.append)
    return t


# Timeout policy


def test_override_above_ceiling_is_rejected_not_clamped():
    with pytest.raises(ValueError, match="300"):
        validate_timeout(MAX_TIMEOUT + 1)


def test_zero_and_negative_timeouts_are_rejected():
    with pytest.raises(ValueError):
        validate_timeout(0)
    with pytest.raises(ValueError):
        validate_timeout(-5)


def test_ceiling_itself_is_allowed():
    assert validate_timeout(MAX_TIMEOUT) == MAX_TIMEOUT


def test_ladder_starts_generous():
    assert TIMEOUT_LADDER == (30, 60, 120)


# Never-retry cases


def test_clean_http_error_never_retries(transport):
    transport.script(_http_error(404))
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_json("https://example.test/x")
    assert len(transport.timeouts) == 1
    assert transport.pauses == []


def test_malformed_json_never_retries(transport):
    transport.script(b"<html>not json</html>")
    with pytest.raises(FetchError, match="valid JSON"):
        fetch_json("https://example.test/x")
    assert len(transport.timeouts) == 1


# Connectivity retries: the escalating ladder


def test_timeouts_walk_the_ladder_then_succeed(transport):
    transport.script(TimeoutError(), TimeoutError(), b'{"ok": true}')
    assert fetch_json("https://example.test/x") == {"ok": True}
    assert transport.timeouts == [30, 60, 120]
    assert transport.pauses == [1, 2]


def test_ladder_exhausted_raises_after_three_attempts(transport):
    transport.script(TimeoutError(), TimeoutError(), TimeoutError())
    with pytest.raises(FetchError, match="unreachable"):
        fetch_json("https://example.test/x")
    assert len(transport.timeouts) == 3


def test_connection_errors_retry_like_timeouts(transport):
    refused = urllib.error.URLError(OSError("connection refused"))
    transport.script(refused, b"{}")
    fetch_json("https://example.test/x")
    assert transport.timeouts == [30, 60]


def test_caller_timeout_replaces_rungs_below_it(transport):
    transport.script(TimeoutError(), TimeoutError(), b"{}")
    fetch_json("https://example.test/x", timeout=45)
    assert transport.timeouts == [45, 60, 120]


def test_caller_timeout_above_every_rung_holds_throughout(transport):
    transport.script(TimeoutError(), TimeoutError(), b"{}")
    fetch_json("https://example.test/x", timeout=200)
    assert transport.timeouts == [200, 200, 200]


# Transient-status retries


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_transient_statuses_retry(transport, code):
    transport.script(_http_error(code), b'{"ok": true}')
    assert fetch_json("https://example.test/x") == {"ok": True}
    assert len(transport.timeouts) == 2


def test_transient_status_on_final_attempt_raises_with_the_code(transport):
    transport.script(_http_error(503), _http_error(503), _http_error(503))
    with pytest.raises(FetchError, match="HTTP 503"):
        fetch_json("https://example.test/x")
    assert len(transport.timeouts) == 3


def test_retry_after_extends_the_pause(transport):
    transport.script(_http_error(429, retry_after="15"), b"{}")
    fetch_json("https://example.test/x")
    assert transport.pauses == [15.0]


def test_hostile_retry_after_is_capped(transport):
    transport.script(_http_error(429, retry_after="9999"), b"{}")
    fetch_json("https://example.test/x")
    assert transport.pauses == [60]


def test_garbage_retry_after_falls_back_to_the_ladder_pause(transport):
    transport.script(_http_error(429, retry_after="Thu, 01 Jan 2026 00:00:00 GMT"), b"{}")
    fetch_json("https://example.test/x")
    assert transport.pauses == [1.0]


def test_retry_after_shorter_than_the_ladder_pause_does_not_shrink_it(transport):
    transport.script(_http_error(503), _http_error(503, retry_after="0"), b"{}")
    fetch_json("https://example.test/x")
    assert transport.pauses == [1.0, 2.0]
