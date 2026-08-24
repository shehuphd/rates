"""The fusion engine: one merge, two invocations.

``fuse()`` is a pure function over already-fetched payloads and, when
supplied, already-fetched freshness data, the same merge whether run on
our weekly schedule to produce a published ledger or by a caller's own
process via ``load(live=True)``. ``fetch_sources()`` does the payload
network half, degrading per source rather than failing whole; freshness
data (see ``_freshness.py``) is gathered separately by the caller and
passed in, so ``fuse()`` itself never performs a network call on its own.

Merge rules (see ERD.md): models.dev is the preferred source and fills
any unit it carries; genai-prices and LiteLLM fill units and fields it
lacks, and each cross-validates the units it shares with the preferred
source. Where they disagree past 2%, which value ships is decided by
freshness first (whichever source's underlying data changed more
recently), falling back to a fixed per-field preference order when
freshness can't decide (see ``PRICE_PREFERENCE`` and ARCHITECTURE.md §
Resolving price disagreements); the disagreement itself is recorded on
the model as ``price_discrepancies`` either way. OpenRouter fills
modality gaps. A record exists in the output when the preferred source
carries it.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from .._errors import AllSourcesUnreachableError, PreferredSourceUnavailableError
from .._http import FetchError, fetch_json
from .._trace import traced
from ._sources import (
    SOURCE_URLS,
    normalize_genai_prices,
    normalize_litellm,
    normalize_models_dev,
    normalize_openrouter,
)

SCHEMA_VERSION = "1.0.0"
DISCREPANCY_THRESHOLD_PCT = 2.0

# Fallback order for a price disagreement freshness can't settle:
# whichever of the two disagreeing sources appears first here wins. A
# starting point, not a permanent ranking: revise as evidence
# accumulates about which source tends to be more current. See
# ARCHITECTURE.md § Resolving price disagreements for why this is a
# fallback and not the first check.
PRICE_PREFERENCE: tuple[str, ...] = ("models_dev", "litellm", "genai_prices")

_NORMALIZERS: dict[str, Callable[[Any], dict[tuple[str, str], dict[str, Any]]]] = {
    "models_dev": normalize_models_dev,
    "genai_prices": normalize_genai_prices,
    "litellm": normalize_litellm,
    "openrouter": normalize_openrouter,
}

_ROLES = {
    "models_dev": "preferred",
    "genai_prices": "validation",
    "litellm": "fallback",
    "openrouter": "fallback",
}


@traced("fusion.fetch_sources")
def fetch_sources(
    timeout: float | None = None,
    token: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Fetch every source, degrading per source: one being unreachable is
    the normal operating condition of an open-internet call, so it's
    skipped and marked in the returned statuses, never a hard failure,
    unless the preferred source is down, or all of them are, the two
    cases where no honest result exists.

    ``token`` (or a GITHUB_TOKEN env var) authenticates the GitHub-hosted
    fetches, lifting GitHub's per-IP unauthenticated rate limit.
    """
    github_token = token or os.environ.get("GITHUB_TOKEN")
    payloads: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    for name, url in SOURCE_URLS.items():
        use_token = github_token if "githubusercontent" in url else None
        try:
            payloads[name] = fetch_json(url, timeout=timeout, token=use_token)
            statuses[name] = "ok"
        except FetchError as exc:
            payloads[name] = None
            statuses[name] = "unreachable"
            last_error = exc

    if all(status != "ok" for status in statuses.values()):
        raise AllSourcesUnreachableError(
            f"none of the {len(SOURCE_URLS)} sources could be reached; "
            f"last failure: {last_error}"
        )
    if statuses["models_dev"] != "ok":
        raise PreferredSourceUnavailableError(
            "models.dev, rates' preferred source, couldn't be reached; a "
            "result built from the fallbacks alone would be missing most "
            "fields, so none is returned"
        )
    return payloads, statuses


def fuse(
    payloads: dict[str, Any],
    statuses: dict[str, str] | None = None,
    *,
    source_freshness: dict[str, date | None] | None = None,
    record_freshness: Callable[[str, str], date | None] | None = None,
) -> dict[str, Any]:
    """Merge fetched payloads into one registry dict in ERD.md's shape.

    Pure: no network of its own, no clock beyond stamping today's
    snapshot date. ``source_freshness`` (a plain dict) and
    ``record_freshness`` (a callable) are optional pre-fetched inputs
    used only to resolve a price disagreement between the preferred
    source and one fallback; the network call each represents, when
    there is one, already happened before ``fuse()`` was called. Neither
    supplied means every disagreement falls back to ``PRICE_PREFERENCE``.
    A payload of None (source skipped) contributes nothing and its status
    travels in the envelope.
    """
    statuses = statuses or {name: "ok" for name in payloads}
    today = datetime.now(timezone.utc).date().isoformat()

    normalized: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for name, normalize in _NORMALIZERS.items():
        payload = payloads.get(name)
        normalized[name] = normalize(payload) if payload is not None else {}

    litellm_by_id = _types_by_bare_id(normalized["litellm"])
    openrouter_ids, openrouter_reasoning = _openrouter_by_bare_id(
        normalized["openrouter"]
    )

    models = []
    for key, base in normalized["models_dev"].items():
        merged = _merge_one(
            base,
            genai=normalized["genai_prices"].get(key),
            litellm=normalized["litellm"].get(key),
            openrouter=normalized["openrouter"].get(key),
            today=today,
            litellm_by_id=litellm_by_id,
            openrouter_ids=openrouter_ids,
            openrouter_reasoning=openrouter_reasoning,
            source_freshness=source_freshness,
            record_freshness=record_freshness,
        )
        # Admission criterion 2: a record in a pricing registry that can't
        # answer any price question serves nobody. An explicit zero rate
        # is a carried value and survives; an empty price map doesn't.
        if merged["price"]:
            models.append(merged)
    models.extend(
        _admit_fallback_only(
            normalized, preferred_keys=set(normalized["models_dev"]), today=today
        )
    )
    models.sort(key=lambda m: (m["provider"], m["id"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "universe": "ai",
        "snapshot_date": today,
        "sources": [
            {
                "name": name,
                "fetched_at": today if statuses.get(name) == "ok" else None,
                "role": _ROLES[name],
                "status": statuses.get(name, "unreachable"),
            }
            for name in SOURCE_URLS
        ],
        "models": models,
    }


def _types_by_bare_id(
    litellm: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, str]:
    """Model type by bare model id, kept only where every LiteLLM entry
    for that id agrees on one mode: an id whose listings disagree stays
    untyped rather than guessed. Exact id matching, not fuzzy: a
    reseller lists the same model under the same id, and type is a
    property of the model, not of the listing."""
    seen: dict[str, set[str]] = {}
    for (_, model_id), record in litellm.items():
        if record.get("type"):
            seen.setdefault(model_id, set()).add(record["type"])
    return {
        model_id: modes.pop()
        for model_id, modes in seen.items()
        if len(modes) == 1
    }


def _openrouter_by_bare_id(
    openrouter: dict[tuple[str, str], dict[str, Any]],
) -> tuple[set[str], dict[str, dict[str, Any]]]:
    """OpenRouter's catalog by bare model id: membership (its catalog is
    chat-completion models only, so presence implies type chat) and the
    reasoning fields only it carries, kept only where every listing of
    that id agrees."""
    ids: set[str] = set()
    reasoning_seen: dict[str, set[tuple[Any, Any]]] = {}
    for (_, model_id), record in openrouter.items():
        ids.add(model_id)
        signal = (record.get("reasoning_mandatory"), record.get("reasoning_default"))
        if signal != (None, None):
            reasoning_seen.setdefault(model_id, set()).add(signal)
    reasoning = {
        model_id: {"reasoning_mandatory": mand, "reasoning_default": default}
        for model_id, signals in reasoning_seen.items()
        for mand, default in [next(iter(signals))]
        if len(signals) == 1
    }
    return ids, reasoning


def _resolve_price_winner(
    other_name: str,
    model_key: tuple[str, str],
    source_freshness: dict[str, date | None] | None,
    record_freshness: Callable[[str, str], date | None] | None,
) -> tuple[str, str]:
    """Which of models_dev and ``other_name`` wins a price disagreement:
    whichever source's underlying data changed more recently, falling
    back to PRICE_PREFERENCE when freshness can't decide (a lookup
    unreachable, or the two dates tied). Returns (winner, "freshness" |
    "preference")."""
    preferred_date = record_freshness(*model_key) if record_freshness else None
    other_date = (source_freshness or {}).get(other_name)
    if (
        preferred_date is not None
        and other_date is not None
        and preferred_date != other_date
    ):
        winner = "models_dev" if preferred_date > other_date else other_name
        return winner, "freshness"
    for candidate in PRICE_PREFERENCE:
        if candidate in ("models_dev", other_name):
            return candidate, "preference"
    return "models_dev", "preference"  # unreachable: PRICE_PREFERENCE covers both


def _merge_one(
    base: dict[str, Any],
    genai: dict[str, Any] | None,
    litellm: dict[str, Any] | None,
    openrouter: dict[str, Any] | None,
    today: str,
    litellm_by_id: dict[str, str] | None = None,
    openrouter_ids: set[str] | None = None,
    openrouter_reasoning: dict[str, dict[str, Any]] | None = None,
    source_freshness: dict[str, date | None] | None = None,
    record_freshness: Callable[[str, str], date | None] | None = None,
) -> dict[str, Any]:
    price = dict(base["price"])
    contributed = {"models_dev"}
    discrepancies = []
    model_key = (base["provider"], base["id"])

    # Fallbacks fill units the preferred source lacks entirely; there's
    # only one value, so nothing to resolve.
    for name, partial in (("litellm", litellm), ("genai_prices", genai)):
        if not partial:
            continue
        filled = False
        for unit, rate in partial.get("price", {}).items():
            if unit not in price:
                price[unit] = rate
                filled = True
        if filled:
            contributed.add(name)

    # Every fallback cross-validates the units it shares with the
    # preferred source. Which value ships is resolved by freshness first,
    # falling back to PRICE_PREFERENCE, see _resolve_price_winner.
    for name, partial in (("genai_prices", genai), ("litellm", litellm)):
        if not partial:
            continue
        for unit, other in partial.get("price", {}).items():
            ours = base["price"].get(unit)
            if ours is None or other == ours:
                continue
            magnitude = max(abs(ours), abs(other))
            pct = abs(ours - other) / magnitude * 100 if magnitude else 0.0
            if pct > DISCREPANCY_THRESHOLD_PCT:
                contributed.add(name)
                winner, resolved_by = _resolve_price_winner(
                    name, model_key, source_freshness, record_freshness
                )
                if winner == "models_dev":
                    chosen_source, chosen_value = "models_dev", ours
                    other_source, other_value = name, other
                else:
                    chosen_source, chosen_value = name, other
                    other_source, other_value = "models_dev", ours
                    price[unit] = other
                discrepancies.append(
                    {
                        "field": unit,
                        "chosen_source": chosen_source,
                        "chosen_value": chosen_value,
                        "other_source": other_source,
                        "other_value": other_value,
                        "resolved_by": resolved_by,
                        "difference_pct": round(pct, 1),
                    }
                )

    bare_id = base["id"].casefold()
    model_type = None
    if litellm and litellm.get("type"):
        model_type = litellm["type"]
        contributed.add("litellm")
    elif litellm_by_id and bare_id in litellm_by_id:
        model_type = litellm_by_id[bare_id]
        contributed.add("litellm")
    elif openrouter_ids and bare_id in openrouter_ids:
        model_type = "chat"
        contributed.add("openrouter")

    deprecation_date = None
    if litellm and litellm.get("deprecation_date"):
        deprecation_date = litellm["deprecation_date"]
        contributed.add("litellm")

    modalities = base["modalities"]
    if not (modalities["input"] or modalities["output"]) and openrouter:
        modalities = openrouter["modalities"]
        contributed.add("openrouter")

    context = dict(base["context"])
    if context["input"] is None and genai and genai["context"]["input"]:
        context["input"] = genai["context"]["input"]
        contributed.add("genai_prices")

    price_tiers = base.get("price_tiers") or []
    if not price_tiers and genai and genai.get("price_tiers"):
        price_tiers = genai["price_tiers"]
        contributed.add("genai_prices")

    reasoning = base["reasoning"]
    if reasoning is not None:
        # OpenRouter is the only source carrying whether the parameter is
        # required, and a per-model default effort; enrichment only, the
        # preferred source's own reasoning shape is never overwritten. The direct
        # provider+id match wins; the bare-id match (all OpenRouter
        # listings of the id agreeing) fills in behind it.
        enrichment = openrouter
        if not (enrichment and enrichment.get("reasoning_mandatory") is not None):
            fallback = (openrouter_reasoning or {}).get(bare_id)
            if fallback is not None:
                enrichment = fallback
        if enrichment and enrichment.get("reasoning_mandatory") is not None:
            reasoning = {
                **reasoning,
                "effort_parameter_required": enrichment["reasoning_mandatory"],
            }
            contributed.add("openrouter")
        if (
            enrichment
            and enrichment.get("reasoning_default")
            and not reasoning.get("default")
        ):
            reasoning = {**reasoning, "default": enrichment["reasoning_default"]}
            contributed.add("openrouter")

    if price:
        price = {"currency": "USD", **price}

    return {
        "provider": base["provider"],
        "id": base["id"],
        "family": base["family"],
        "type": model_type,
        "modalities": modalities,
        "context": context,
        "tool_call": base["tool_call"],
        "structured_output": base["structured_output"],
        "price": price,
        "price_tiers": price_tiers,
        "price_discrepancies": discrepancies,
        "reasoning": reasoning,
        "lifecycle": {
            "status": base["lifecycle_status"],
            "release_date": base["release_date"],
            "deprecation_date": deprecation_date,
        },
        "sources": {name: today for name in sorted(contributed)},
    }


# The units a type must be priced on to clear admission criterion 2; any
# type not listed needs at least one unit of any kind.
_REQUIRED_UNITS = {
    "chat": {"input_mtok", "output_mtok"},
    "completion": {"input_mtok", "output_mtok"},
    "responses": {"input_mtok", "output_mtok"},
    "embedding": {"input_mtok"},
    "video_generation": {"output_per_second"},
}


def _admit_fallback_only(
    normalized: dict[str, dict[tuple[str, str], dict[str, Any]]],
    preferred_keys: set[tuple[str, str]],
    today: str,
) -> list[dict[str, Any]]:
    """Models the preferred source doesn't catalog, admitted when they clear the
    bar in ARCHITECTURE.md § Admission criteria: identity, the units their
    type bills on, a type, and two independent fallbacks agreeing on price
    within the discrepancy threshold."""
    admitted = []
    genai_all = normalized["genai_prices"]
    litellm_all = normalized["litellm"]

    for key in sorted(set(genai_all) & set(litellm_all) - preferred_keys):
        genai, litellm = genai_all[key], litellm_all[key]
        model_type = litellm.get("type")
        if not model_type:
            continue

        shared = set(genai["price"]) & set(litellm["price"])
        if not shared:
            continue
        agreement = True
        for unit in shared:
            a, b = genai["price"][unit], litellm["price"][unit]
            magnitude = max(abs(a), abs(b))
            if magnitude and abs(a - b) / magnitude * 100 > DISCREPANCY_THRESHOLD_PCT:
                agreement = False
                break
        if not agreement:
            continue

        price = {**litellm["price"], **genai["price"]}
        required = _REQUIRED_UNITS.get(model_type)
        if required and not required <= set(price):
            continue

        contributed = {"genai_prices", "litellm"}
        openrouter = normalized["openrouter"].get(key)
        modalities: dict[str, list[str]] = {"input": [], "output": []}
        if openrouter and (
            openrouter["modalities"]["input"] or openrouter["modalities"]["output"]
        ):
            modalities = openrouter["modalities"]
            contributed.add("openrouter")

        admitted.append(
            {
                "provider": genai["provider"],
                "id": genai["id"],
                "family": None,
                "type": model_type,
                "modalities": modalities,
                "context": genai["context"],
                "tool_call": None,
                "structured_output": None,
                "price": {"currency": "USD", **price},
                "price_tiers": genai.get("price_tiers") or [],
                "price_discrepancies": [],
                "reasoning": None,
                "lifecycle": {
                    "status": "active",
                    "release_date": None,
                    "deprecation_date": litellm.get("deprecation_date"),
                },
                "sources": {name: today for name in sorted(contributed)},
            }
        )
    return admitted
