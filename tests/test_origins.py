"""Origin page sources: parsers against recorded fixtures, shape
qualification on hostile input, and the fusion's origin admission.

Fixtures under tests/fixtures/origins are reduced extracts of the live
pages (2026-09-26); the live-drift twin of these tests is
tests/test_origin_probes.py.
"""

import math
from pathlib import Path

import pytest

from rates.ai._fusion import fuse
from rates.ai._origins import (
    ORIGIN_CARDS,
    ORIGIN_PROVIDERS,
    ORIGIN_URLS,
    UNIT_REGISTRY,
    normalize_origins,
    parse_assemblyai,
    parse_deepgram,
    parse_elevenlabs,
    parse_livekit,
    parse_typesafe,
)

FIXTURES = Path(__file__).parent / "fixtures" / "origins"
TODAY = "2026-09-26"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text()


def _minimal_feeds() -> dict:
    return {
        "models_dev": {
            "anthropic": {
                "models": {
                    "claude-x": {
                        "cost": {"input": 1, "output": 2},
                        "limit": {},
                        "modalities": {},
                    }
                }
            }
        },
        "genai_prices": [],
        "litellm": {},
        "openrouter": {},
    }


# Hostile and degenerate input first.


@pytest.mark.parametrize(
    "parse",
    [
        parse_deepgram,
        parse_assemblyai,
        parse_elevenlabs,
        parse_livekit,
        parse_typesafe,
    ],
)
@pytest.mark.parametrize(
    "page",
    ["", "no prices here", "<html><body>$0.0043/min</body></html>", "| a | b |"],
)
def test_parsers_yield_nothing_on_unrecognized_pages(parse, page):
    assert parse(page, TODAY) == []


def test_deepgram_garbled_offer_json_yields_nothing():
    page = (
        '<script id="product-offer-schema" type="application/ld+json">'
        '{"offers": [{"name": "X - Streaming - Nova-3 - Pay As You Go",'
        '</script>'
    )
    assert parse_deepgram(page, TODAY) == []


def test_deepgram_refuses_negative_zero_and_absurd_rates():
    def offer(price):
        return (
            '<script id="product-offer-schema" type="application/ld+json">'
            '{"offers": [{"name": "X - Streaming - Nova-3 - Pay As You Go",'
            f'"price": "{price}", "priceCurrency": "USD"}}]}}</script>'
        )

    for bad in ("0", "-0.004", "1000000", "abc"):
        assert parse_deepgram(offer(bad), TODAY) == [], bad


def test_normalize_origins_marks_an_unparsable_fetched_page_suspect():
    payloads = {name: "fetched but wrong shape" for name in ORIGIN_URLS}
    records, statuses = normalize_origins(payloads, TODAY)
    assert records == []
    assert statuses == {name: "suspect" for name in ORIGIN_URLS}


@pytest.mark.parametrize(
    ("name", "page"),
    [
        ("deepgram_pricing", (
            '<script id="product-offer-schema" type="application/ld+json">'
            '[{"offers": []}]</script>'
        )),
        ("deepgram_pricing", (
            '<script id="product-offer-schema" type="application/ld+json">'
            '{"offers": ["x"]}</script>'
        )),
        ("assemblyai_pricing",
         "## Pre-recorded\n| Universal | `universal-2` | **$0.1.5/hr** |\n"),
        ("elevenlabs_pricing", "### Scribe v2\nSpeech to Text\n$0.2.2\nPrice per hour\n"),
        ("livekit_pricing", (
            "<h3>STT model prices Build/Ship plan</h3><ul><li>Foo: $./min</li></ul>"
        )),
    ],
)
def test_a_page_that_makes_its_parser_raise_is_marked_suspect(name, page):
    records, statuses = normalize_origins({name: page}, TODAY)
    assert records == [] and statuses == {name: "suspect"}


def test_normalize_origins_skips_unfetched_pages_without_status():
    records, statuses = normalize_origins({"deepgram_pricing": None}, TODAY)
    assert records == [] and statuses == {}


# The recorded pages.


def test_deepgram_fixture_yields_stt_and_tts_and_nothing_else():
    records = parse_deepgram(_page("deepgram.html"), TODAY)
    by_id = {r["id"]: r for r in records}
    # Batch and streaming rates are two units on one record.
    nova = by_id["nova-3-monolingual"]
    assert nova["type"] == "audio_transcription"
    assert nova["price"]["audio_minute"] == 0.0043
    assert nova["price"]["streaming_audio_minute"] == 0.0048
    # Whisper is batch-only on the page, so no streaming unit appears.
    assert "streaming_audio_minute" not in by_id["whisper-large"]["price"]
    assert by_id["aura-2"] == {
        **by_id["aura-2"],
        "type": "audio_speech",
        "price": {"currency": "USD", "kchar": 0.03},
    }
    # Add-on rates (redaction, diarization), agent bundles, and Growth
    # plan prices never become records.
    assert not {"redaction", "speaker-diarization", "standard"} & set(by_id)
    assert all(r["provider"] == "deepgram" for r in records)


def test_assemblyai_fixture_splits_audio_and_session_meters():
    records = parse_assemblyai(_page("assemblyai.md"), TODAY)
    by_id = {r["id"]: r for r in records}
    # Hourly rates become per-minute rates by denominator arithmetic.
    assert by_id["universal-2"]["price"]["audio_minute"] == pytest.approx(
        0.15 / 60
    )
    # Streaming bills the session, the page's own wording, so its unit
    # is connection time, never audio time.
    assert by_id["u3-rt-pro"]["price"] == {
        "currency": "USD",
        "session_minute": pytest.approx(0.45 / 60),
    }
    # The priceless deprecated row and the voice-agent bundle stay out.
    assert "slam-1" not in by_id
    assert not any("agent" in model_id for model_id in by_id)


def test_elevenlabs_fixture_keeps_speech_and_drops_the_rest():
    records = parse_elevenlabs(_page("elevenlabs.md"), TODAY)
    by_id = {r["id"]: r for r in records}
    assert by_id["v3"]["price"] == {"currency": "USD", "kchar": 0.1}
    assert by_id["scribe-v2"]["price"]["audio_minute"] == pytest.approx(
        0.22 / 60
    )
    # Realtime's page entry doesn't state what its hour counts; no row.
    assert "scribe-v2-realtime" not in by_id
    # Agents, music, dubbing, and audio processing aren't speech records.
    types = {r["type"] for r in records}
    assert types == {"audio_speech", "audio_transcription"}


def test_typesafe_fixture_yields_the_versioned_model_and_its_aliases():
    records = parse_typesafe(_page("typesafe.md"), TODAY)
    assert [r["id"] for r in records] == ["jev-1.13.0", "jev-latest", "jev-preview"]
    for record in records:
        assert record["type"] == "evaluation"
        assert record["modalities"] == {"input": ["text"], "output": ["text"]}
        # Output is $0 because the page states the tokens are free in
        # words; the zero is a carried value here, never a parse artifact.
        assert record["price"] == {
            "currency": "USD",
            "input_mtok": 0.042,
            "output_mtok": 0.0,
        }
        assert record["provider"] == "typesafe"


def test_typesafe_without_the_free_output_statement_prices_input_only():
    page = (
        "## Current models\n"
        "| Jev | `jev-1.13.0` |\n| :- | :- |\n"
        "| Price (per Btok / per Mtok) | $42 / $0.042 |\n\n"
        "* **Price:** Charged per input token.\n"
    )
    (record,) = parse_typesafe(page, TODAY)
    assert record["price"] == {"currency": "USD", "input_mtok": 0.042}


def test_typesafe_refuses_a_zero_input_rate_and_an_unchargeable_page():
    zero_rate = (
        "## Current models\n"
        "| Jev | `jev-1.13.0` |\n| :- | :- |\n"
        "| Price (per Btok / per Mtok) | $0 / $0 |\n\n"
        "* **Price:** Charged per input token. Output tokens are free.\n"
    )
    assert parse_typesafe(zero_rate, TODAY) == []
    # No statement of which tokens are charged, no rows: the price
    # figures alone don't say what the money buys.
    no_note = (
        "## Current models\n"
        "| Jev | `jev-1.13.0` |\n| :- | :- |\n"
        "| Price (per Btok / per Mtok) | $42 / $0.042 |\n"
    )
    assert parse_typesafe(no_note, TODAY) == []


def test_typesafe_reads_the_mtok_figure_by_label_not_position():
    swapped = (
        "## Current models\n"
        "| Jev | `jev-1.13.0` |\n| :- | :- |\n"
        "| Price (per Mtok / per Btok) | $0.042 / $42 |\n\n"
        "* **Price:** Charged per input token. Output tokens are free.\n"
    )
    (record,) = parse_typesafe(swapped, TODAY)
    assert record["price"]["input_mtok"] == 0.042
    # A labels/figures mismatch ships nothing for that model.
    mismatched = swapped.replace("$0.042 / $42", "$0.042")
    assert parse_typesafe(mismatched, TODAY) == []


def test_typesafe_drops_an_alias_whose_target_is_not_priced():
    page = (
        "## Current models\n"
        "| Jev | `jev-1.13.0` |\n| :- | :- |\n"
        "| Price (per Btok / per Mtok) | $42 / $0.042 |\n\n"
        "* **Price:** Charged per input token. Output tokens are free.\n\n"
        "## Aliases\n"
        "| Alias | Points to |\n| :- | :- |\n"
        "| `jev-preview` | `jev-2.0.0-preview` |\n"
    )
    assert [r["id"] for r in parse_typesafe(page, TODAY)] == ["jev-1.13.0"]


def test_livekit_fixture_keeps_entry_plan_stt_only():
    records = parse_livekit(_page("livekit.html"), TODAY)
    by_id = {r["id"]: r for r in records}
    assert by_id["deepgram-nova-3-monolingual"]["price"] == {
        "currency": "USD",
        "streaming_audio_minute": 0.0048,
    }
    # TTS and LLM per-minute figures display character- and token-billed
    # models, so no such rows exist; Scale-plan rates don't either
    # (Nova-3 monolingual on Scale is 0.0042).
    assert all(r["type"] == "audio_transcription" for r in records)
    rates = {
        r["price"]["streaming_audio_minute"]
        for r in records
        if r["id"] == "deepgram-nova-3-monolingual"
    }
    assert rates == {0.0048}


def test_every_emitted_unit_is_registered_with_a_vendor_verification():
    pages = {
        "deepgram_pricing": _page("deepgram.html"),
        "assemblyai_pricing": _page("assemblyai.md"),
        "elevenlabs_pricing": _page("elevenlabs.md"),
        "livekit_pricing": _page("livekit.html"),
        "typesafe_pricing": _page("typesafe.md"),
    }
    records, statuses = normalize_origins(pages, TODAY)
    assert statuses == {name: "ok" for name in ORIGIN_URLS}
    for record in records:
        provider = record["provider"]
        for unit in record["price"]:
            if unit == "currency":
                continue
            entry = UNIT_REGISTRY[unit]
            assert provider in entry["verified"], (
                f"{provider} emits {unit} without a dated verification "
                "of the counted event in UNIT_REGISTRY"
            )
            assert not math.isnan(record["price"][unit])


def test_origin_cards_are_first_party_for_their_own_provider_only():
    for name, card in ORIGIN_CARDS.items():
        assert card.origin_providers == (ORIGIN_PROVIDERS[name],)
        assert card.registry_rank >= 4  # feeds keep the declared front ranks


# Fusion integration.


def test_fuse_admits_origin_records_and_marks_the_envelope():
    payloads = {
        **_minimal_feeds(),
        "deepgram_pricing": _page("deepgram.html"),
        "assemblyai_pricing": _page("assemblyai.md"),
        "elevenlabs_pricing": _page("elevenlabs.md"),
        "livekit_pricing": _page("livekit.html"),
        "typesafe_pricing": _page("typesafe.md"),
    }
    out = fuse(payloads)
    providers = {m["provider"] for m in out["models"]}
    assert {"deepgram", "assemblyai", "elevenlabs", "livekit", "typesafe"} <= providers
    rows = {s["name"]: s for s in out["sources"]}
    for name in ORIGIN_URLS:
        assert rows[name]["role"] == "origin"
        assert rows[name]["status"] == "ok"
        assert rows[name]["fetched_at"] is not None
    assert set(out["resolution"]["sources"]) >= set(ORIGIN_CARDS)
    # Origin rows carry their page as their only source, dated today.
    sample = next(m for m in out["models"] if m["provider"] == "elevenlabs")
    assert list(sample["sources"]) == ["elevenlabs_pricing"]


def test_fuse_without_origin_payloads_reports_them_unreachable():
    out = fuse(_minimal_feeds())
    rows = {s["name"]: s for s in out["sources"]}
    for name in ORIGIN_URLS:
        assert rows[name]["status"] == "unreachable"
        assert rows[name]["fetched_at"] is None
    assert all(m["provider"] == "anthropic" for m in out["models"])


def test_fuse_marks_a_fetched_but_unparsable_origin_suspect():
    payloads = {**_minimal_feeds(), "deepgram_pricing": "<html>redesigned</html>"}
    statuses = {name: "ok" for name in payloads}
    out = fuse(payloads, statuses)
    row = next(s for s in out["sources"] if s["name"] == "deepgram_pricing")
    assert row["status"] == "suspect"
    # A suspect source contributes nothing, and the run still succeeds.
    assert not any(m["provider"] == "deepgram" for m in out["models"])


def test_an_origin_row_replaces_a_feed_row_with_the_same_key_whole():
    # A feed carrying the vendor's own model at another price: the page's
    # record stands in its place, with the page as its only source and no
    # discrepancy note, and nothing of the feed's record survives.
    feeds = _minimal_feeds()
    feeds["models_dev"]["deepgram"] = {
        "models": {
            "aura-2": {
                "cost": {"input": 9, "output": 9},
                "limit": {"context": 1234},
                "modalities": {},
                "family": "from-the-feed",
            }
        }
    }
    payloads = {**feeds, "deepgram_pricing": _page("deepgram.html")}
    statuses = {name: "ok" for name in payloads}
    out = fuse(payloads, statuses)
    rows = [m for m in out["models"] if (m["provider"], m["id"]) == ("deepgram", "aura-2")]
    assert len(rows) == 1
    (row,) = rows
    assert row["price"] == {"currency": "USD", "kchar": 0.03}
    assert row["sources"] == {"deepgram_pricing": out["snapshot_date"]}
    assert row["price_discrepancies"] == []
    assert row["family"] is None

    # Without the page, the feed's row is what ships.
    out = fuse(feeds, {name: "ok" for name in feeds})
    (row,) = [m for m in out["models"] if m["provider"] == "deepgram"]
    assert row["price"]["input_mtok"] == 9 and "models_dev" in row["sources"]


def test_an_origin_row_survives_registry_round_trip():
    from rates.ai._registry import Registry

    payloads = {**_minimal_feeds(), "assemblyai_pricing": _page("assemblyai.md")}
    statuses = {name: "ok" for name in payloads}
    registry = Registry.from_dict(fuse(payloads, statuses))
    hits = registry.filter(provider="assemblyai")
    assert {m.id for m in hits} == {
        "universal-3-5-pro",
        "universal-2",
        "u3-rt-pro",
        "universal-streaming-english",
        "universal-streaming-multilingual",
    }
    assert all(m.type == "audio_transcription" for m in hits)


# Deepgram's two served forms.


def test_deepgram_markdown_and_html_forms_yield_identical_records():
    # The vendor serves markdown on content negotiation or HTML with
    # embedded offer data; whichever arrives, the same records ship.
    markdown = parse_deepgram(_page("deepgram.md"), TODAY)
    html = parse_deepgram(_page("deepgram.html"), TODAY)
    assert markdown == html
    assert len(markdown) == 8


def test_deepgram_markdown_takes_the_current_rate_not_the_struck_one():
    records = parse_deepgram(_page("deepgram.md"), TODAY)
    by_id = {r["id"]: r for r in records}
    # Flux English's cell reads "$0.0065/min ~~$0.0077/min~~": the
    # struck price is the earlier one and never ships.
    assert by_id["flux-english"]["price"]["streaming_audio_minute"] == 0.0065
    # Whichever side of the current price the struck one stands on.
    struck_first = (
        "## Speech to Text\n### Streaming\n#### $/minute\n"
        "| Model | Pay As You Go | Growth |\n| --- | --- | --- |\n"
        "| Nova-3 Monolingual | ~~$0.0077/min~~ $0.0048/min | $0.0042/min |\n"
    )
    (record,) = parse_deepgram(struck_first, TODAY)
    assert record["price"] == {"currency": "USD", "streaming_audio_minute": 0.0048}
    # Growth-plan rates and the $/hour restatements don't ship either.
    rates = {
        rate
        for r in records
        for unit, rate in r["price"].items()
        if unit != "currency"
    }
    assert 0.0057 not in rates and 0.39 not in rates
