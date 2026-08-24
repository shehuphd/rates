"""Tests for the traceact soft-dependency shim."""

from rates import _trace


def test_traced_is_identity_when_traceact_is_absent(monkeypatch):
    monkeypatch.setattr(_trace, "_traced_action", None)

    @_trace.traced("x.y")
    def fn(a, b):
        return a + b

    assert fn(2, 3) == 5
    assert fn.__name__ == "fn"


def test_configure_cli_tracing_is_a_noop_without_traceact(monkeypatch):
    monkeypatch.setattr(_trace, "_traced_action", None)
    _trace.configure_cli_tracing()  # must not raise or import anything


def test_traced_wraps_when_traceact_is_present():
    # traceact is installed in the dev venv; the wrapped function must
    # still return its own result.
    @_trace.traced("test.op")
    def fn():
        return 41 + 1

    assert fn() == 42
