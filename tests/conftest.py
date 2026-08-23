import os

import pytest


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
