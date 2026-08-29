import csv
import os

import pytest

_results = []


@pytest.fixture(autouse=True, scope="session")
def traceact_sink(tmp_path_factory):
    """The test suite is the consuming app, so it configures tracing:
    a file sink in a temp dir, keeping traces out of captured stdout
    (traceact's no-sink fallback prints to console) and out of the
    developer's home directory."""
    try:
        import traceact
    except ImportError:
        yield
        return
    path = tmp_path_factory.mktemp("traces") / "test-traces.jsonl"
    traceact.configure(sinks=[traceact.JsonlSink(str(path))])
    yield
    traceact.reset_config()


def pytest_runtest_logreport(report):
    """Collect every test's outcome for TEST_INDEX.CSV: the call phase
    always, plus setup-phase skips and errors (which never reach call)."""
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        _results.append((report.nodeid, report.outcome, round(report.duration, 4)))


def pytest_sessionfinish(session, exitstatus):
    """Write the run's results to TEST_INDEX.CSV at the repo root
    (gitignored). Rows sorted by test id so the file is diffable even
    though execution order is randomized."""
    path = session.config.rootpath / "TEST_INDEX.CSV"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["test", "outcome", "duration_s"])
        writer.writerows(sorted(_results))


@pytest.fixture(autouse=True)
def isolated_rates_cache(tmp_path, monkeypatch):
    """Every test gets its own rates cache dir; nothing touches the
    developer's own ~/.cache/rates."""
    from rates import _cache

    cache = tmp_path / "rates-cache"

    def fake_cache_dir():
        cache.mkdir(parents=True, exist_ok=True)
        return cache

    # Every caller reads _cache.cache_dir() through the module, so one patch
    # redirects the live/sync caches, the freshness cache, and the completion
    # cache together.
    monkeypatch.setattr(_cache, "cache_dir", fake_cache_dir)
    return cache


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: capability-drift probe against a live upstream source; "
        "runs only with RATES_LIVE_PROBES=1",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RATES_LIVE_PROBES") == "1":
        return
    skip = pytest.mark.skip(reason="live probe; set RATES_LIVE_PROBES=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
