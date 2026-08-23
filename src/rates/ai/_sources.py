"""Per-source normalizers for the AI universe's fusion.

Each normalizer takes one upstream source's raw payload, in the shape that
source serves today, and returns partial model records keyed by
``(provider, model_id)``, both casefolded, in ERD.md's vocabulary. The
merge itself lives in ``_fusion``; nothing here decides which source wins.

Schema drift in these payloads is watched by live probes in the test
suite, per CODING.md's capability-drift pattern; the shapes assumed here
were verified against each source directly (2026-08-22).
"""

from __future__ import annotations

from typing import Any, Optional

SOURCE_URLS = {
    "models_dev": "https://models.dev/api.json",
    "genai_prices": "https://raw.githubusercontent.com/pydantic/genai-prices/main/prices/data.json",
    "litellm": "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
    "openrouter": "https://openrouter.ai/api/v1/models",
}

# models.dev cost keys -> ERD price units, all $/mtok already.
_MODELS_DEV_UNITS = {
    "input": "input_mtok",
    "output": "output_mtok",
    "cache_read": "cache_read_mtok",
    "cache_write": "cache_write_mtok",
    "input_audio": "input_audio_mtok",
    "output_audio": "output_audio_mtok",
    "reasoning": "reasoning_mtok",
}

# LiteLLM per-token costs -> ERD units, scaled to $/mtok.
_LITELLM_MTOK_UNITS = {
    "input_cost_per_token": "input_mtok",
    "output_cost_per_token": "output_mtok",
    "cache_read_input_token_cost": "cache_read_mtok",
    "cache_creation_input_token_cost": "cache_write_mtok",
    "cache_creation_input_token_cost_above_1hr": "cache_write_1h_mtok",
}

_MTOK = 1_000_000


def _key(provider: str, model_id: str) -> tuple[str, str]:
    return provider.casefold(), model_id.casefold()


def normalize_models_dev(data: dict[str, Any]) -> dict[tuple[str, str], dict]:
    """models.dev: the primary. Providers keyed at the top level, models
    nested under each; prices already $/mtok."""
    records: dict[tuple[str, str], dict] = {}
    for provider_id, provider in data.items():
        for model_id, m in provider.get("models", {}).items():
            cost = m.get("cost") or {}
            price = {
                unit: cost[src]
                for src, unit in _MODELS_DEV_UNITS.items()
                if isinstance(cost.get(src), (int, float))
            }
            price_tiers = _tiers_from_models_dev(cost)

            limit = m.get("limit") or {}
            modalities = m.get("modalities") or {}
            records[_key(provider_id, model_id)] = {
                "provider": provider_id,
                "id": model_id,
                "family": m.get("family"),
                "modalities": {
                    "input": list(modalities.get("input") or []),
                    "output": list(modalities.get("output") or []),
                },
                "context": {
                    "input": limit.get("context"),
                    "output": limit.get("output"),
                },
                "tool_call": m.get("tool_call"),
                "structured_output": m.get("structured_output"),
                "price": price,
                "price_tiers": price_tiers,
                "reasoning": _reasoning_from_models_dev(m),
                "lifecycle_status": m.get("status") or "active",
                "release_date": m.get("release_date"),
            }
    return records


def _reasoning_from_models_dev(m: dict[str, Any]) -> Optional[dict]:
    if not m.get("reasoning"):
        return None
    # effort_parameter_required has no models.dev source; it stays unknown
    # here and OpenRouter's per-model reasoning.mandatory fills it in the
    # merge where that coverage exists.
    record: dict[str, Any] = {
        "control": None,
        "can_disable_reasoning": False,
        "levels": [],
        "range": None,
        "budget": None,
    }
    for option in m.get("reasoning_options") or []:
        kind = option.get("type")
        if kind == "effort":
            record["control"] = "effort"
            values = option.get("values") or []
            start = 0 if values and values[0] == "none" else 1
            record["levels"] = [
                {"label": label, "rank": start + i}
                for i, label in enumerate(values)
            ]
            if values:
                record["range"] = [start, start + len(values) - 1]
            if "none" in values:
                record["can_disable_reasoning"] = True
        elif kind == "budget_tokens" and record["control"] != "effort":
            record["control"] = "budget_tokens"
            record["budget"] = {"min": option.get("min"), "max": option.get("max")}
        elif kind == "toggle":
            if record["control"] is None:
                record["control"] = "toggle"
            record["can_disable_reasoning"] = True
    return record


def _tiers_from_models_dev(cost: dict[str, Any]) -> list[dict]:
    """Both of models.dev's tier forms, as ERD.md price_tiers entries.

    The explicit ``tiers`` list wins when present; the ``context_over_200k``
    shorthand fills in only without one, since the two can disagree on the
    boundary (272k vs. 200k on the same model) and the list is the more
    specific claim.
    """
    tiers: dict[int, dict[str, float]] = {}

    for entry in cost.get("tiers") or []:
        condition = entry.get("tier") or {}
        if condition.get("type") != "context":
            continue
        threshold = condition.get("size")
        if not isinstance(threshold, int):
            continue
        for src, unit in _MODELS_DEV_UNITS.items():
            if isinstance(entry.get(src), (int, float)):
                tiers.setdefault(threshold, {})[unit] = entry[src]

    if not tiers:
        over_200k = cost.get("context_over_200k") or {}
        for src, unit in _MODELS_DEV_UNITS.items():
            if isinstance(over_200k.get(src), (int, float)):
                tiers.setdefault(200_000, {})[unit] = over_200k[src]

    return [
        {"when": {"dimension": "context", "over": over}, "price": price}
        for over, price in sorted(tiers.items())
    ]


def normalize_genai_prices(data: list[dict]) -> dict[tuple[str, str], dict]:
    """genai-prices: prices already keyed in ERD.md's own unit vocabulary."""
    records: dict[tuple[str, str], dict] = {}
    for provider in data:
        for m in provider.get("models", []):
            prices = m.get("prices")
            if isinstance(prices, list):
                # The conditional list form wraps one prices dict per
                # condition set; the first is the current/base one.
                prices = (prices[0].get("prices") or {}) if prices else {}
            if not isinstance(prices, dict):
                prices = {}

            price: dict[str, float] = {}
            tiers: dict[int, dict[str, float]] = {}
            for unit, rate in prices.items():
                if isinstance(rate, (int, float)):
                    price[unit] = rate
                elif isinstance(rate, dict):
                    # Per-unit tiered form: {"base": r, "tiers":
                    # [{"start": threshold, "price": r2}]}.
                    if isinstance(rate.get("base"), (int, float)):
                        price[unit] = rate["base"]
                    for tier in rate.get("tiers") or []:
                        start, tier_rate = tier.get("start"), tier.get("price")
                        if isinstance(start, int) and isinstance(tier_rate, (int, float)):
                            tiers.setdefault(start, {})[unit] = tier_rate

            records[_key(provider["id"], m["id"])] = {
                "provider": provider["id"],
                "id": m["id"],
                "price": price,
                "price_tiers": [
                    {"when": {"dimension": "context", "over": over}, "price": p}
                    for over, p in sorted(tiers.items())
                ],
                "context": {"input": m.get("context_window"), "output": None},
            }
    return records


def normalize_litellm(data: dict[str, Any]) -> dict[tuple[str, str], dict]:
    """LiteLLM: flat entries, sometimes provider-prefixed, priced per
    single token (scaled here to $/mtok). Carries the ``mode`` field ERD.md
    maps to ``type``, and the only deprecation dates any source has."""
    records: dict[tuple[str, str], dict] = {}
    for entry_key, m in data.items():
        if not isinstance(m, dict) or not m.get("litellm_provider"):
            continue
        model_id = entry_key.split("/", 1)[1] if "/" in entry_key else entry_key
        price = {
            unit: m[src] * _MTOK
            for src, unit in _LITELLM_MTOK_UNITS.items()
            if isinstance(m.get(src), (int, float))
        }
        if isinstance(m.get("output_cost_per_second"), (int, float)):
            price["output_per_second"] = m["output_cost_per_second"]
        records[_key(m["litellm_provider"], model_id)] = {
            "provider": m["litellm_provider"],
            "id": model_id,
            "type": m.get("mode"),
            "price": price,
            "deprecation_date": m.get("deprecation_date"),
        }
    return records


def normalize_openrouter(data: dict[str, Any]) -> dict[tuple[str, str], dict]:
    """OpenRouter: chat-completion models only, ids as provider/model.
    Used for modality cross-checks and reasoning enrichment: its per-model
    ``reasoning`` object is the only source carrying ``mandatory`` (does
    the API error without the parameter?) and ``default_effort``."""
    records: dict[tuple[str, str], dict] = {}
    for m in data.get("data", []):
        if "/" not in m.get("id", ""):
            continue
        provider, model_id = m["id"].split("/", 1)
        arch = m.get("architecture") or {}
        reasoning = m.get("reasoning") or {}
        records[_key(provider, model_id)] = {
            "provider": provider,
            "id": model_id,
            "modalities": {
                "input": list(arch.get("input_modalities") or []),
                "output": list(arch.get("output_modalities") or []),
            },
            "reasoning_mandatory": reasoning.get("mandatory"),
            "reasoning_default": reasoning.get("default_effort"),
        }
    return records
