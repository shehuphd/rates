"""Tests for the stdlib HTTP helper: timeout ladder, transient-status
retries, and backoff, failure paths first."""

import email.message
import http.client
import urllib.error

import pytest

from rates._http import (
    MAX_TIMEOUT,
    TIMEOUT_LADDER,
    FetchError,
    fetch_json,
    fetch_text,
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
            self.accepts = []

        def script(self, *responses):
            self.responses = list(responses)

    t = Transport()

    def fake_get(url, timeout, token, accept="application/json"):
        t.timeouts.append(timeout)
        t.accepts.append(accept)
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


# fetch_text: the same ladder and failure contract, over a text body


def test_fetch_text_wraps_a_clean_http_error_without_retrying(transport):
    transport.script(_http_error(404))
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_text("https://example.test/pricing")
    assert len(transport.timeouts) == 1 and transport.pauses == []


def test_fetch_text_retries_a_transient_status_then_returns_the_body(transport):
    transport.script(_http_error(503), b"# Pricing\n")
    assert fetch_text("https://example.test/pricing") == "# Pricing\n"
    assert len(transport.timeouts) == 2 and len(transport.pauses) == 1


def test_fetch_text_gives_up_after_the_whole_ladder(transport):
    transport.script(*[TimeoutError("slow")] * len(TIMEOUT_LADDER))
    with pytest.raises(FetchError, match="unreachable"):
        fetch_text("https://example.test/pricing")
    assert transport.timeouts == list(TIMEOUT_LADDER)


def test_fetch_text_asks_for_markdown_first_and_json_fetches_do_not(transport):
    transport.script(b"body", b"{}")
    fetch_text("https://example.test/pricing")
    fetch_json("https://example.test/feed.json")
    markdown_accept, json_accept = transport.accepts
    assert markdown_accept.startswith("text/markdown")
    assert "text/html" in markdown_accept
    assert json_accept == "application/json"


def test_fetch_text_decodes_undecodable_bytes_instead_of_raising(transport):
    transport.script(b"price \xff\xfe $0.01")
    body = fetch_text("https://example.test/pricing")
    assert body.startswith("price ") and body.endswith(" $0.01")


# Failures urllib doesn't wrap


@pytest.mark.parametrize("fetch", [fetch_json, fetch_text])
@pytest.mark.parametrize(
    "error",
    [
        http.client.RemoteDisconnected("closed without response"),
        http.client.IncompleteRead(b"{", 100),
        ConnectionResetError("reset by peer"),
    ],
)
def test_a_dropped_or_truncated_connection_retries_then_raises_fetch_error(
    transport, fetch, error
):
    transport.script(error, error, error)
    with pytest.raises(FetchError, match="unreachable"):
        fetch("https://example.test/x")
    assert len(transport.timeouts) == len(TIMEOUT_LADDER)


def test_a_body_that_isnt_utf8_raises_fetch_error_without_retry(transport):
    transport.script(b"\xff\xfe{not utf-8}")
    with pytest.raises(FetchError, match="valid JSON"):
        fetch_json("https://example.test/x")
    assert len(transport.timeouts) == 1


@pytest.mark.parametrize("fetch", [fetch_json, fetch_text])
def test_a_malformed_url_raises_fetch_error(fetch):
    with pytest.raises(FetchError, match="not a fetchable URL"):
        fetch("not a url at all")
