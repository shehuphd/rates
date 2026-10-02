"""Capability-drift probes: each origin vendor's live page still parses
into records for that vendor, of the speech types.

These hit the pages raw. A failing probe is the notification that the
vendor's page changed shape: update the matching parser in
rates/ai/_origins.py, refresh the fixture under tests/fixtures/origins,
and re-verify the unit's counted event in UNIT_REGISTRY. Skipped unless
RATES_LIVE_PROBES=1; run on the weekly ledger cycle.
"""

import pytest

from rates._http import fetch_text
from rates.ai._origins import _PARSERS, ORIGIN_PROVIDERS, ORIGIN_URLS

pytestmark = pytest.mark.live


@pytest.mark.parametrize("name", sorted(ORIGIN_URLS))
def test_origin_page_still_parses(name):
    page = fetch_text(ORIGIN_URLS[name])
    records = _PARSERS[name](page, "9999-01-01")
    assert records, (
        f"{ORIGIN_URLS[name]} yielded no records; the page changed shape. "
        "Update the parser, the fixture, and UNIT_REGISTRY."
    )
    provider = ORIGIN_PROVIDERS[name]
    assert all(r["provider"] == provider for r in records)
    types = {r["type"] for r in records}
    assert types <= {"audio_transcription", "audio_speech"}
