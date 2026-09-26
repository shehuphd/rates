"""Origin page sources: speech vendors' own published pricing.

Standalone speech vendors (Deepgram, AssemblyAI, ElevenLabs, LiveKit)
appear in none of the four aggregator feeds, so their records enter the
fusion from the vendors' own pricing pages, the resolution ladder's
origin rung speaking about its own provider. Each parser reads one
vendor's page (markdown where the vendor serves it on content
negotiation, HTML otherwise) and emits fully formed ledger records.

Shape qualification is built in: a row ships only with a recognized
model name, a positive rate, and a registered unit; a page that yields
no rows marks its source ``suspect`` in the envelope and contributes
nothing, never a hard failure. Page structure drift is watched by live
probes in the test suite (tests/test_origin_probes.py), and the weekly
build prints a per-source delta report against the previous snapshot.

Records stay within the signed v1 scope: base model unit rates for
speech-to-text and text-to-speech. Add-on rates (diarization, medical
mode, redaction), voice-agent bundles, subscription plans, and rows
whose billed event the page doesn't state (ElevenLabs' Scribe Realtime;
LiveKit's per-minute displays of token- and character-billed models)
are not extracted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .._resolution import SourceCard

ORIGIN_URLS = {
    "deepgram_pricing": "https://deepgram.com/pricing",
    "assemblyai_pricing": "https://www.assemblyai.com/pricing",
    "elevenlabs_pricing": "https://elevenlabs.io/pricing/api",
    "livekit_pricing": "https://livekit.com/pricing",
}

# The provider whose rows each source is first-party for.
ORIGIN_PROVIDERS = {
    "deepgram_pricing": "deepgram",
    "assemblyai_pricing": "assemblyai",
    "elevenlabs_pricing": "elevenlabs",
    "livekit_pricing": "livekit",
}

# Each origin source's ladder standing: first-party for its own
# provider's rows, ranked after the feeds in the declared order (the
# determinism floor; an origin row is its provider's only source today,
# so the rank is a floor that never decides a contest).
ORIGIN_CARDS: dict[str, SourceCard] = {
    name: SourceCard(
        name=name,
        registry_rank=4 + i,
        origin_providers=(ORIGIN_PROVIDERS[name],),
    )
    for i, name in enumerate(ORIGIN_URLS)
}

# The units origin records bill on, each with the counted event's
# definition and the dated per-vendor verification of that definition.
# A parser may only emit units registered here; the suite asserts it.
UNIT_REGISTRY: dict[str, dict[str, Any]] = {
    "audio_minute": {
        "counts": "one minute of audio submitted for batch transcription",
        "verified": {
            "deepgram": "2026-09-26",  # pricing page: pre-recorded $/min
            "assemblyai": "2026-09-26",  # pricing page: per hour of audio submitted
            "elevenlabs": "2026-09-26",  # pricing page: Scribe v2 price per hour
        },
    },
    "streaming_audio_minute": {
        "counts": "one minute of audio processed over a streaming connection",
        "verified": {
            "deepgram": "2026-09-26",  # pricing page: streaming $/min
            "livekit": "2026-09-26",  # vendor docs: STT billed by audio duration
        },
    },
    "session_minute": {
        "counts": "one minute a streaming session's connection stays open, "
        "whatever audio it carries",
        "verified": {
            "assemblyai": "2026-09-26",  # pricing page: session duration, not audio duration
        },
    },
    "kchar": {
        "counts": "1,000 characters of input text synthesized to speech",
        "verified": {
            "deepgram": "2026-09-26",  # pricing page: $/1k characters
            "elevenlabs": "2026-09-26",  # pricing page: price per 1K characters
        },
    },
}

_MODALITIES = {
    "audio_transcription": {"input": ["audio"], "output": ["text"]},
    "audio_speech": {"input": ["text"], "output": ["audio"]},
}


def _slug(name: str) -> str:
    """A listed display name as a record id: lowercased, parentheses
    dropped, runs of non-alphanumerics collapsed to one hyphen."""
    cleaned = re.sub(r"[()]", " ", name.lower())
    return re.sub(r"[^a-z0-9.]+", "-", cleaned).strip("-")


def _record(
    provider: str,
    model_id: str,
    model_type: str,
    price: dict[str, float],
    source: str,
    today: str,
) -> dict[str, Any]:
    """One origin row in the ledger's model shape. Fields no pricing page
    publishes (context, tool_call, reasoning) stay at their absent
    values, the same reading as everywhere else: unpublished, not zero."""
    return {
        "provider": provider,
        "id": model_id,
        "family": None,
        "type": model_type,
        "modalities": dict(_MODALITIES[model_type]),
        "context": {"input": None, "output": None},
        "tool_call": None,
        "structured_output": None,
        "price": {"currency": "USD", **price},
        "price_tiers": [],
        "price_discrepancies": [],
        "reasoning": None,
        "lifecycle": {
            "status": "active",
            "release_date": None,
            "deprecation_date": None,
        },
        "sources": {source: today},
    }


def _well_formed(price: dict[str, float]) -> bool:
    """Shape qualification, step one: registered units only, every rate a
    positive finite number (a published zero rate would be a carried
    value, but no speech vendor lists one, so a zero here is a parse
    artifact and the row is refused)."""
    return bool(price) and all(
        unit in UNIT_REGISTRY and isinstance(rate, float) and 0 < rate < 1000
        for unit, rate in price.items()
    )


# --- Deepgram: schema.org offer data embedded in the page -----------------

# Offer names read "... - <Section> - <Model> - <Plan>". Only these model
# families are transcription or synthesis models; other names in the
# same sections are feature add-on rates (redaction, diarization) or
# voice-agent bundles, out of scope by decision. A new family fails
# closed: it stays unextracted until this pattern learns it, and the
# build's delta report is where it shows up.
_DEEPGRAM_STT = re.compile(r"^(nova|whisper|flux)\b")
_DEEPGRAM_TTS = re.compile(r"^(aura|flux-tts)")


def parse_deepgram(text: str, today: str) -> list[dict[str, Any]]:
    match = re.search(
        r'<script id="product-offer-schema" type="application/ld\+json">'
        r"(.*?)</script>",
        text,
        re.DOTALL,
    )
    if not match:
        return []
    try:
        product = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    offers = product.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]

    prices: dict[tuple[str, str], dict[str, float]] = {}
    for offer in offers:
        name, rate = offer.get("name"), offer.get("price")
        if not isinstance(name, str) or offer.get("priceCurrency") != "USD":
            continue
        try:
            value = float(rate)
        except (TypeError, ValueError):
            continue
        parts = [p.strip() for p in name.split(" - ")]
        if parts[-1] != "Pay As You Go":
            continue
        if len(parts) == 4 and parts[1] in ("Streaming", "Pre-Recorded"):
            model = _slug(parts[2])
            if not _DEEPGRAM_STT.match(model):
                continue
            unit = (
                "streaming_audio_minute"
                if parts[1] == "Streaming"
                else "audio_minute"
            )
            prices.setdefault((model, "audio_transcription"), {})[unit] = value
        elif len(parts) == 3:
            model = _slug(parts[1])
            if not _DEEPGRAM_TTS.match(model):
                continue
            prices.setdefault((model, "audio_speech"), {})["kchar"] = value

    return [
        _record("deepgram", model, model_type, price, "deepgram_pricing", today)
        for (model, model_type), price in sorted(prices.items())
        if _well_formed(price)
    ]


# --- AssemblyAI: markdown rate-card tables --------------------------------

# A rate-card row: display name, the API value in backticks, a bare
# hourly price (an add-on's "+$" prefix and SLAM-1's priceless row both
# fall outside this shape on purpose).
_ASSEMBLYAI_ROW = re.compile(
    r"^\|\s*([^|`]+?)\s*\|[^|]*?`([a-z0-9._-]+)`[^|]*\|\s*"
    r"\*\*\$([0-9.]+)/hr\*\*"
)


def parse_assemblyai(text: str, today: str) -> list[dict[str, Any]]:
    records = []
    section_unit: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line.casefold()
            if "pre-recorded" in heading:
                section_unit = "audio_minute"
            elif "streaming" in heading:
                # The page's own billing note: session duration, not
                # audio duration.
                section_unit = "session_minute"
            else:
                section_unit = None
            continue
        if section_unit is None:
            continue
        row = _ASSEMBLYAI_ROW.match(line)
        if not row:
            continue
        price = {section_unit: round(float(row.group(3)) / 60, 10)}
        if _well_formed(price):
            records.append(
                _record(
                    "assemblyai",
                    row.group(2),
                    "audio_transcription",
                    price,
                    "assemblyai_pricing",
                    today,
                )
            )
    return records


# --- ElevenLabs: markdown model entries -----------------------------------

_ELEVENLABS_TYPES = {
    "Text to Speech": "audio_speech",
    "Speech to Text": "audio_transcription",
}
_ELEVENLABS_UNITS = {
    "Price per 1K characters": ("kchar", 1.0),
    "Price per hour": ("audio_minute", 1 / 60),
}


def parse_elevenlabs(text: str, today: str) -> list[dict[str, Any]]:
    # Entries read: "### Name" / category line / "$X.XX" / unit line.
    # Categories beyond speech (Agents, Music Generation, Dubbing) and
    # the Realtime model, whose page doesn't state what its hour counts,
    # stay unextracted.
    records = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        name = line[4:].strip()
        window = lines[i + 1 : i + 5]
        model_type = next(
            (t for c, t in _ELEVENLABS_TYPES.items() if c in window), None
        )
        unit_scale = next(
            (u for text_form, u in _ELEVENLABS_UNITS.items() if text_form in window),
            None,
        )
        rate_line = next(
            (w for w in window if re.fullmatch(r"\$[0-9.]+", w)), None
        )
        if not (model_type and unit_scale and rate_line):
            continue
        if "realtime" in name.casefold():
            continue
        unit, scale = unit_scale
        price = {unit: round(float(rate_line[1:]) * scale, 10)}
        if _well_formed(price):
            records.append(
                _record(
                    "elevenlabs",
                    _slug(name),
                    model_type,
                    price,
                    "elevenlabs_pricing",
                    today,
                )
            )
    return records


# --- LiveKit: inference resale rates in semantic HTML lists ----------------

_LIVEKIT_ITEM = re.compile(r"<li>([^<:]{1,80}):\s*\$([0-9.]+)/min</li>")


def parse_livekit(text: str, today: str) -> list[dict[str, Any]]:
    # Sections read "<h3>STT model prices ... plan (per minute)</h3><ul>...".
    # Only the STT sections on the entry plan are extracted: LiveKit's own
    # docs say STT bills by audio duration while its LLM (tokens) and TTS
    # (characters) per-minute figures are display equivalents, not the
    # billed unit, and the Scale-plan rates are a committed plan's.
    clean = text.replace("<!-- -->", "")
    records = []
    for match in re.finditer(
        r"<h3[^>]*>([^<]{1,120})</h3><ul>(.*?)</ul>", clean, re.DOTALL
    ):
        heading, body = match.group(1), match.group(2)
        if "STT model prices" not in heading or "Build/Ship" not in heading:
            continue
        for name, rate in _LIVEKIT_ITEM.findall(body):
            price = {"streaming_audio_minute": float(rate)}
            if _well_formed(price):
                records.append(
                    _record(
                        "livekit",
                        _slug(name),
                        "audio_transcription",
                        price,
                        "livekit_pricing",
                        today,
                    )
                )
    return records


_PARSERS: dict[str, Callable[[str, str], list[dict[str, Any]]]] = {
    "deepgram_pricing": parse_deepgram,
    "assemblyai_pricing": parse_assemblyai,
    "elevenlabs_pricing": parse_elevenlabs,
    "livekit_pricing": parse_livekit,
}


def normalize_origins(
    payloads: dict[str, Any], today: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Parse every fetched origin page into ledger records.

    Returns the records plus a per-source parse status: ``ok`` when the
    page yielded rows, ``suspect`` when a fetched page yielded none (the
    page changed shape, or its content no longer matches the parser),
    in which case the source contributes nothing this run.
    """
    records: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    for name, parse in _PARSERS.items():
        page = payloads.get(name)
        if not isinstance(page, str):
            continue
        parsed = parse(page, today)
        if parsed:
            statuses[name] = "ok"
            records.extend(parsed)
        else:
            statuses[name] = "suspect"
    return records, statuses
