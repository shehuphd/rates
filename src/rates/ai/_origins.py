"""Origin page sources: vendors' own published pricing.

The fusion gets no records from the feeds for some vendors: the
standalone speech vendors (Deepgram, AssemblyAI, ElevenLabs, LiveKit),
which two feeds list nothing for and whose per-second and per-character
LiteLLM entries aren't consumed, and TypeSafe, which no feed lists. So
their records enter the fusion from the vendors' own pricing pages,
admitted whole after the feeds have merged. Each parser reads one
vendor's page (markdown where the vendor serves it, HTML otherwise) and
emits fully formed ledger records.

Shape qualification is built in: a row ships only with a recognized
model name, a registered unit, and a positive rate (or a zero the page
states in words, such as TypeSafe's free output tokens); a page that yields
no rows marks its source ``suspect`` in the envelope and contributes
nothing, never a hard failure. Page structure drift is watched by live
probes in the test suite (tests/test_origin_probes.py), and the weekly
build prints a per-source delta report against the previous snapshot.

Records stay within the signed scope: base model unit rates for
speech-to-text and text-to-speech, and TypeSafe's per-token
evaluation-model rates (the versioned model and the aliases the page
publishes beside it, an alias row at its target's price).
Add-on rates (diarization, medical
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
    "typesafe_pricing": "https://docs.typesafe.ai/models.md",
}

# The provider whose rows each source is first-party for.
ORIGIN_PROVIDERS = {
    "deepgram_pricing": "deepgram",
    "assemblyai_pricing": "assemblyai",
    "elevenlabs_pricing": "elevenlabs",
    "livekit_pricing": "livekit",
    "typesafe_pricing": "typesafe",
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
            "deepgram": "2026-10-09",  # pricing page: Pre-Recorded $/minute table
            "assemblyai": "2026-09-26",  # pricing page: per hour of audio submitted
            "elevenlabs": "2026-09-26",  # pricing page: Scribe v2 price per hour
        },
    },
    "streaming_audio_minute": {
        "counts": "one minute of audio processed over a streaming connection",
        "verified": {
            "deepgram": "2026-10-09",  # pricing page: Streaming $/minute table
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
            "deepgram": "2026-10-09",  # pricing page: $/1k characters
            "elevenlabs": "2026-09-26",  # pricing page: price per 1K characters
        },
    },
    "input_mtok": {
        "counts": "one million input tokens",
        "verified": {
            "typesafe": "2026-10-09",  # models page: charged per input token, per Mtok
        },
    },
    "output_mtok": {
        "counts": "one million output tokens",
        "verified": {
            "typesafe": "2026-10-09",  # models page: output tokens are free
        },
    },
}

_MODALITIES = {
    "audio_transcription": {"input": ["audio"], "output": ["text"]},
    "audio_speech": {"input": ["text"], "output": ["audio"]},
    # A decision model takes text state and questions and returns typed
    # judgments; "evaluation" is the mode word LiteLLM already uses for
    # the category.
    "evaluation": {"input": ["text"], "output": ["text"]},
}


def _slug(name: str) -> str:
    """A listed display name as a record id: lowercased, parentheses
    dropped, runs of anything but letters, digits, and dots collapsed
    to one hyphen."""
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
        "modalities": {
            kind: list(values)
            for kind, values in _MODALITIES[model_type].items()
        },
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


def _well_formed(
    price: dict[str, float], stated_free: frozenset[str] = frozenset()
) -> bool:
    """Shape qualification, step one: registered units only, every rate a
    positive finite number below 1,000. A zero is accepted only for a
    unit in ``stated_free``, the units the page itself says in words are
    free; any other zero is a parse artifact and the row is refused."""
    return bool(price) and all(
        unit in UNIT_REGISTRY
        and isinstance(rate, float)
        and (0 < rate < 1000 or (rate == 0.0 and unit in stated_free))
        for unit, rate in price.items()
    )


# --- Deepgram: the pricing page in either form it serves -------------------

# The page arrives as markdown on content negotiation (observed from
# 2026-10-09) or as HTML with schema.org offer data embedded (the form
# through 2026-10-05); the parser reads whichever came. Only these model
# families are transcription or synthesis models; other names in the
# same sections are feature add-on rates (redaction, diarization) or
# voice-agent bundles, out of scope by decision. A new family fails
# closed and silently: it stays unextracted until this pattern learns
# it, and no report names it, since the delta report and the fidelity
# check both work from extracted rows.
_DEEPGRAM_STT = re.compile(r"^(nova|whisper|flux)\b")
_DEEPGRAM_TTS = re.compile(r"^(aura|flux-tts)")
# A rate cell's current price; a struck-through "was" price (~~$x/min~~)
# is removed before this looks.
_DEEPGRAM_MIN = re.compile(r"\$([0-9.]+)/min\b")
_DEEPGRAM_KCHAR = re.compile(r"\$([0-9.]+)/1k characters\b")


def _parse_deepgram_markdown(text: str) -> dict[tuple[str, str], dict[str, float]]:
    """Model rates from the markdown form: $/minute tables under the
    Streaming and Pre-Recorded headings of Speech to Text, and the Text
    to Speech table. $/hour tables restate the same rates in another
    denominator, and add-on and voice-agent tables fail the model-name
    gate; none of those contribute."""
    prices: dict[tuple[str, str], dict[str, float]] = {}
    section = subsection = denominator = ""
    for line in text.splitlines():
        if line.startswith("## "):
            section, subsection, denominator = line[3:].strip().casefold(), "", ""
            continue
        if line.startswith("### "):
            subsection, denominator = line[4:].strip().casefold(), ""
            continue
        if line.startswith("#### "):
            denominator = line[5:].strip().casefold()
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        # The model name stands before any dash-joined description.
        name = re.split(r"\s+[—–-]\s+", cells[0], 1)[0]
        model = _slug(name)
        payg = re.sub(r"~~.*?~~", "", cells[1])
        if section == "speech to text" and denominator == "$/minute":
            if not _DEEPGRAM_STT.match(model):
                continue
            rate = _DEEPGRAM_MIN.search(payg)
            if not rate:
                continue
            unit = (
                "streaming_audio_minute"
                if subsection == "streaming"
                else "audio_minute"
            )
            prices.setdefault((model, "audio_transcription"), {})[unit] = float(
                rate.group(1)
            )
        elif section == "text to speech":
            if not _DEEPGRAM_TTS.match(model):
                continue
            rate = _DEEPGRAM_KCHAR.search(payg)
            if rate:
                prices.setdefault((model, "audio_speech"), {})["kchar"] = float(
                    rate.group(1)
                )
    return prices


def parse_deepgram(text: str, today: str) -> list[dict[str, Any]]:
    match = re.search(
        r'<script id="product-offer-schema" type="application/ld\+json">'
        r"(.*?)</script>",
        text,
        re.DOTALL,
    )
    if not match:
        markdown_prices = _parse_deepgram_markdown(text)
        return [
            _record("deepgram", model, model_type, price, "deepgram_pricing", today)
            for (model, model_type), price in sorted(markdown_prices.items())
            if _well_formed(price)
        ]
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


# --- TypeSafe: markdown model tables on the Models docs page ---------------

# Each current model is a two-column table headed "| Display name |
# `model-id` |", with a price row whose label names its denominators in
# the order its figures appear ("Price (per Btok / per Mtok) | $42 /
# $0.042"). The section's price note states which tokens are charged.
_TYPESAFE_MODEL = re.compile(r"^\|\s*([^|`]+?)\s*\|\s*`([a-z0-9._-]+)`\s*\|\s*$")
_TYPESAFE_PRICE = re.compile(r"^\|\s*Price \(([^)]*)\)\s*\|\s*(.+?)\s*\|\s*$")
_TYPESAFE_RATE = re.compile(r"\$([0-9]+(?:\.[0-9]+)?)")
# An aliases-table row: the alias id, then the versioned id it points to.
_TYPESAFE_ALIAS = re.compile(r"^\|\s*`([a-z0-9._-]+)`\s*\|\s*`([a-z0-9._-]+)`\s*\|")


def _markdown_section(text: str, heading: str) -> list[str] | None:
    """The lines under a ``## heading`` (case-insensitive) up to the next
    level-two heading, or None when the page has no such section."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## ") and line[3:].strip().casefold() == heading:
            body = []
            for rest in lines[i + 1 :]:
                if rest.startswith("## "):
                    break
                body.append(rest)
            return body
    return None


def parse_typesafe(text: str, today: str) -> list[dict[str, Any]]:
    # "## Current models" carries each versioned model and its price;
    # "## Aliases" names the rolling ids the provider also publishes,
    # each pointing at a versioned id. One row ships per published id,
    # an alias row at its target's price (the provider's own model
    # listing serves the aliases, so a caller pricing by listed id
    # needs them resolvable). The per-Mtok figure is taken by its
    # label's position, never by its place in the row, and the price
    # note must say input tokens are charged, or no row ships. Jev
    # models judge text against supplied options, the ledger's
    # evaluation type.
    section = _markdown_section(text, "current models")
    if section is None:
        return []
    note = " ".join(
        line.casefold() for line in section if line.startswith("* **Price:**")
    )
    if "charged per input token" not in note:
        return []
    output_free = "output tokens are free" in note

    models: list[dict[str, str]] = []
    for line in section:
        if header := _TYPESAFE_MODEL.match(line):
            models.append({"id": header.group(2)})
        elif models and (price_row := _TYPESAFE_PRICE.match(line)):
            models[-1]["labels"], models[-1]["figures"] = price_row.groups()

    records = []
    for model in models:
        labels = [part.strip().casefold() for part in model.get("labels", "").split("/")]
        figures = model.get("figures", "").replace("\\", "").split("/")
        if len(labels) != len(figures) or "per mtok" not in labels:
            continue
        rate = _TYPESAFE_RATE.fullmatch(figures[labels.index("per mtok")].strip())
        if not rate:
            continue
        price = {"input_mtok": float(rate.group(1))}
        if output_free:
            price["output_mtok"] = 0.0
        if not _well_formed(price, stated_free=frozenset({"output_mtok"})):
            continue
        records.append(
            _record(
                "typesafe",
                model["id"],
                "evaluation",
                price,
                "typesafe_pricing",
                today,
            )
        )

    priced = {record["id"]: record["price"] for record in records}
    for line in _markdown_section(text, "aliases") or []:
        alias_row = _TYPESAFE_ALIAS.match(line)
        if not alias_row:
            continue
        alias, target = alias_row.groups()
        if alias not in priced and target in priced:
            price = {
                unit: rate
                for unit, rate in priced[target].items()
                if unit != "currency"
            }
            records.append(
                _record(
                    "typesafe",
                    alias,
                    "evaluation",
                    price,
                    "typesafe_pricing",
                    today,
                )
            )
    return records


_PARSERS: dict[str, Callable[[str, str], list[dict[str, Any]]]] = {
    "deepgram_pricing": parse_deepgram,
    "assemblyai_pricing": parse_assemblyai,
    "elevenlabs_pricing": parse_elevenlabs,
    "livekit_pricing": parse_livekit,
    "typesafe_pricing": parse_typesafe,
}


def normalize_origins(
    payloads: dict[str, Any], today: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Parse every fetched origin page into ledger records.

    Returns the records plus a per-source parse status: ``ok`` when the
    page yielded rows, ``suspect`` when a fetched page yielded none (the
    page changed shape, or its content no longer matches the parser,
    including a page whose content makes its parser raise), in which
    case the source contributes nothing this run.
    """
    records: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    for name, parse in _PARSERS.items():
        page = payloads.get(name)
        if not isinstance(page, str):
            continue
        try:
            parsed = parse(page, today)
        except (AttributeError, KeyError, TypeError, ValueError):
            parsed = []
        if parsed:
            statuses[name] = "ok"
            records.extend(parsed)
        else:
            statuses[name] = "suspect"
    return records, statuses
