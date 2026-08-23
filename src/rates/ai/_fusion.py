"""The fusion engine: one merge, two invocations.

``fuse()`` is a pure function over already-fetched payloads, the same
merge whether run on our weekly schedule to produce a published ledger or
by a caller's own process via ``load(live=True)``. ``fetch_sources()``
does the network half, degrading per source rather than failing whole.

Merge rules (see ERD.md): models.dev is the primary and wins outright for
any value it carries; genai-prices and LiteLLM fill units and fields it
lacks; genai-prices cross-validates prices, with disagreement past 2%
recorded on the model as ``price_discrepancies``; OpenRouter fills
modality gaps. A record exists in the output when the primary carries it.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .._errors import AllSourcesUnreachableError, PrimarySourceUnavailableError
from .._http import FetchError, fetch_json
from ._sources import (
    SOURCE_URLS,
    normalize_genai_prices,
    normalize_litellm,
    normalize_models_dev,
    normalize_openrouter,
)

SCHEMA_VERSION = "1.0.0"
DISCREPANCY_THRESHOLD_PCT = 2.0

_NORMALIZERS: dict[str, Callable[[Any], dict]] = {
    "models_dev": normalize_models_dev,
    "genai_prices": normalize_genai_prices,
    "litellm": normalize_litellm,
    "openrouter": normalize_openrouter,
}

_ROLES = {
    "models_dev": "primary",
    "genai_prices": "validation",
    "litellm": "fallback",
    "openrouter": "fallback",
}


def fetch_sources(
    timeout: Optional[float] = None,
    token: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Fetch every source, degrading per source: one being unreachable is
    the normal operating condition of an open-internet call, so it's
    skipped and marked in the returned statuses, never a hard failure —
    unless the primary is down, or all of them are, the two cases where
    no honest result exists.

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
        raise PrimarySourceUnavailableError(
            "models.dev, the primary source, couldn't be reached; a result "
            "built from the fallbacks alone would be missing most fields, "
            "so none is returned"
        )
    return payloads, statuses


def fuse(
    payloads: dict[str, Any],
    statuses: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Merge fetched payloads into one registry dict in ERD.md's shape.

    Pure: no network, no clock beyond stamping today's snapshot date.
    A payload of None (source skipped) contributes nothing and its status
    travels in the envelope.
    """
    statuses = statuses or {name: "ok" for name in payloads}
    today = datetime.now(timezone.utc).date().isoformat()

    normalized: dict[str, dict] = {}
    for name, normalize in _NORMALIZERS.items():
        payload = payloads.get(name)
        normalized[name] = normalize(payload) if payload is not None else {}

    models = []
    for key, base in normalized["models_dev"].items():
        models.append(
            _merge_one(
                base,
                genai=normalized["genai_prices"].get(key),
                litellm=normalized["litellm"].get(key),
                openrouter=normalized["openrouter"].get(key),
                today=today,
            )
        )
    models.extend(
        _admit_fallback_only(
            normalized, primary_keys=set(normalized["models_dev"]), today=today
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


def _merge_one(
    base: dict,
    genai: Optional[dict],
    litellm: Optional[dict],
    openrouter: Optional[dict],
    today: str,
) -> dict:
    price = dict(base["price"])
    contributed = {"models_dev"}
    discrepancies = []

    # Fallbacks fill units the primary lacks; the primary's own values are
    # never overwritten.
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

    # genai-prices cross-validates the units both sources carry.
    if genai:
        for unit, other in genai.get("price", {}).items():
            ours = base["price"].get(unit)
            if ours is None or other == ours:
                continue
            magnitude = max(abs(ours), abs(other))
            pct = abs(ours - other) / magnitude * 100 if magnitude else 0.0
            if pct > DISCREPANCY_THRESHOLD_PCT:
                contributed.add("genai_prices")
                discrepancies.append(
                    {
                        "field": unit,
                        "primary_source": "models_dev",
                        "primary_value": ours,
                        "conflicting_source": "genai_prices",
                        "conflicting_value": other,
                        "difference_pct": round(pct, 1),
                    }
                )

    model_type = None
    if litellm and litellm.get("type"):
        model_type = litellm["type"]
        contributed.add("litellm")

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
    if reasoning is not None and openrouter:
        # OpenRouter is the only source carrying whether the parameter is
        # required, and a per-model default effort; enrichment only, the
        # primary's own reasoning shape is never overwritten.
        if openrouter.get("reasoning_mandatory") is not None:
            reasoning = {
                **reasoning,
                "effort_parameter_required": openrouter["reasoning_mandatory"],
            }
            contributed.add("openrouter")
        if openrouter.get("reasoning_default") and not reasoning.get("default"):
            reasoning = {**reasoning, "default": openrouter["reasoning_default"]}
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
    normalized: dict[str, dict],
    primary_keys: set,
    today: str,
) -> list[dict]:
    """Models the primary doesn't catalog, admitted when they clear the
    bar in ARCHITECTURE.md § Admission criteria: identity, the units their
    type bills on, a type, and two independent fallbacks agreeing on price
    within the discrepancy threshold."""
    admitted = []
    genai_all = normalized["genai_prices"]
    litellm_all = normalized["litellm"]

    for key in sorted(set(genai_all) & set(litellm_all) - primary_keys):
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
        modalities = {"input": [], "output": []}
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
