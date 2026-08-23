"""Tests for the fusion merge and per-source normalizers, against fixtures
shaped like each source's live payload (verified 2026-08-22)."""

import pytest

from rates import AllSourcesUnreachableError, PrimarySourceUnavailableError
from rates.ai import Registry
from rates.ai._fusion import fetch_sources, fuse
from rates.ai._sources import normalize_litellm, normalize_models_dev

MODELS_DEV = {
    "anthropic": {
        "id": "anthropic",
        "models": {
            "claude-opus-5": {
                "id": "claude-opus-5",
                "family": "claude-opus",
                "cost": {"input": 5, "output": 25, "cache_read": 0.5},
                "limit": {"context": 1000000, "output": 128000},
                "modalities": {"input": ["text", "image"], "output": ["text"]},
                "reasoning": True,
                "reasoning_options": [
                    {"type": "effort", "values": ["low", "medium", "high"]}
                ],
                "tool_call": True,
                "structured_output": True,
                "release_date": "2026-07-24",
            },
            "claude-2": {
                "id": "claude-2",
                "cost": {"input": 8, "output": 24},
                "modalities": {"input": ["text"], "output": ["text"]},
                "reasoning": False,
                "status": "deprecated",
                "release_date": "2023-07",
            },
        },
    },
    "xai": {
        "id": "xai",
        "models": {
            "grok-4.3": {
                "id": "grok-4.3",
                "cost": {"input": 0.2, "output": 0.5},
                "modalities": {},
                "reasoning": True,
                "reasoning_options": [
                    {"type": "effort", "values": ["none", "low", "high"]}
                ],
            },
        },
    },
    "nvidia": {
        "id": "nvidia",
        "models": {
            "nemotron-nano": {
                "id": "nemotron-nano",
                "cost": {"input": 0.1, "output": 0.3},
                "modalities": {},
                "reasoning": True,
                "reasoning_options": [
                    {"type": "budget_tokens", "min": 1024, "max": 32768}
                ],
            },
            "kimi-style": {
                "id": "kimi-style",
                "cost": {"input": 0.1, "output": 0.3},
                "modalities": {},
                "reasoning": True,
                "reasoning_options": [{"type": "toggle"}],
            },
        },
    },
}

GENAI_PRICES = [
    {
        "id": "anthropic",
        "models": [
            {"id": "claude-opus-5", "prices": {"input_mtok": 5, "output_mtok": 25}},
            # Disagrees with the primary's 8 by well over 2%.
            {"id": "claude-2", "prices": {"input_mtok": 4}, "context_window": 100000},
        ],
    },
    {
        "id": "tiered-vendor",
        "models": [
            {
                "id": "tiered-model",
                "prices": [
                    {
                        "prices": {
                            "input_mtok": {
                                "base": 5,
                                "tiers": [{"start": 200000, "price": 10}],
                            },
                            "output_mtok": 25,
                        }
                    }
                ],
            },
        ],
    },
    {
        "id": "mistral",
        "models": [
            # Fallback-only, corroborated by LiteLLM at identical prices.
            {"id": "mistral-large", "prices": {"input_mtok": 2, "output_mtok": 6}},
            # Fallback-only, but LiteLLM disagrees on price.
            {"id": "command-x", "prices": {"input_mtok": 1.0, "output_mtok": 3.0}},
            # Fallback-only, agreeing, but no type anywhere.
            {"id": "typeless", "prices": {"input_mtok": 1.0, "output_mtok": 2.0}},
        ],
    },
]

LITELLM = {
    "claude-opus-5": {
        "litellm_provider": "anthropic",
        "mode": "chat",
        "input_cost_per_token": 5e-06,
        "cache_creation_input_token_cost_above_1hr": 1e-05,
        "deprecation_date": "2027-07-24",
    },
    "xai/grok-4.3": {
        "litellm_provider": "xai",
        "mode": "chat",
    },
    "mistral/mistral-large": {
        "litellm_provider": "mistral",
        "mode": "chat",
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 6e-06,
    },
    "mistral/command-x": {
        "litellm_provider": "mistral",
        "mode": "chat",
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 3e-06,
    },
    "mistral/typeless": {
        "litellm_provider": "mistral",
        "input_cost_per_token": 1e-06,
        "output_cost_per_token": 2e-06,
    },
}

OPENROUTER = {
    "data": [
        {
            "id": "anthropic/claude-opus-5",
            "architecture": {
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            },
            "reasoning": {
                "mandatory": False,
                "supported_efforts": ["low", "medium", "high"],
                "default_effort": "high",
            },
        },
        {
            "id": "x-ai/grok-4.3",
            "architecture": {
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            },
        },
        {
            "id": "xai/grok-4.3",
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
        },
    ]
}


def _payloads(**overrides):
    payloads = {
        "models_dev": MODELS_DEV,
        "genai_prices": GENAI_PRICES,
        "litellm": LITELLM,
        "openrouter": OPENROUTER,
    }
    payloads.update(overrides)
    return payloads


@pytest.fixture
def fused():
    return fuse(_payloads())


def _model(fused, model_id):
    return next(m for m in fused["models"] if m["id"] == model_id)


# Merge rules


def test_primary_values_are_never_overwritten(fused):
    opus = _model(fused, "claude-opus-5")
    assert opus["price"]["input_mtok"] == 5
    assert opus["price"]["cache_read_mtok"] == 0.5


def test_fallback_fills_units_the_primary_lacks(fused):
    opus = _model(fused, "claude-opus-5")
    assert opus["price"]["cache_write_1h_mtok"] == pytest.approx(10.0)
    assert "litellm" in opus["sources"]


def test_type_and_deprecation_come_from_litellm(fused):
    opus = _model(fused, "claude-opus-5")
    assert opus["type"] == "chat"
    assert opus["lifecycle"]["deprecation_date"] == "2027-07-24"


def test_agreement_produces_no_discrepancy(fused):
    assert _model(fused, "claude-opus-5")["price_discrepancies"] == []


def test_disagreement_past_threshold_is_recorded_and_primary_still_wins(fused):
    claude2 = _model(fused, "claude-2")
    assert claude2["price"]["input_mtok"] == 8
    (d,) = claude2["price_discrepancies"]
    assert d["primary_value"] == 8
    assert d["conflicting_value"] == 4
    assert d["difference_pct"] == 50.0


def test_context_gap_filled_from_genai_prices(fused):
    assert _model(fused, "claude-2")["context"]["input"] == 100000


def test_openrouter_fills_modalities_only_when_the_primary_has_none(fused):
    grok = _model(fused, "grok-4.3")
    assert grok["modalities"] == {"input": ["text"], "output": ["text"]}
    opus = _model(fused, "claude-opus-5")
    assert opus["modalities"]["input"] == ["text", "image"]


def test_genai_tiered_list_form_parses_base_and_tiers():
    from rates.ai._sources import normalize_genai_prices

    record = normalize_genai_prices(GENAI_PRICES)[("tiered-vendor", "tiered-model")]
    assert record["price"] == {"input_mtok": 5, "output_mtok": 25}
    assert record["price_tiers"] == [
        {"when": {"dimension": "context", "over": 200000}, "price": {"input_mtok": 10}}
    ]


def test_missing_status_means_active(fused):
    assert _model(fused, "claude-opus-5")["lifecycle"]["status"] == "active"
    assert _model(fused, "claude-2")["lifecycle"]["status"] == "deprecated"


# Reasoning shapes


def test_effort_levels_rank_from_one_without_none(fused):
    r = _model(fused, "claude-opus-5")["reasoning"]
    assert r["control"] == "effort"
    assert [(l["label"], l["rank"]) for l in r["levels"]] == [
        ("low", 1), ("medium", 2), ("high", 3)
    ]
    assert r["range"] == [1, 3]
    assert r["can_disable_reasoning"] is False


def test_none_level_ranks_from_zero_and_means_disableable(fused):
    r = _model(fused, "grok-4.3")["reasoning"]
    assert r["levels"][0] == {"label": "none", "rank": 0}
    assert r["range"] == [0, 2]
    assert r["can_disable_reasoning"] is True


def test_no_reasoning_capability_is_absent(fused):
    assert _model(fused, "claude-2")["reasoning"] is None


def test_budget_tokens_control_carries_the_budget(fused):
    r = _model(fused, "nemotron-nano")["reasoning"]
    assert r["control"] == "budget_tokens"
    assert r["budget"] == {"min": 1024, "max": 32768}
    assert r["levels"] == []


def test_toggle_control_means_disableable_with_no_dial(fused):
    r = _model(fused, "kimi-style")["reasoning"]
    assert r["control"] == "toggle"
    assert r["can_disable_reasoning"] is True
    assert r["levels"] == [] and r["budget"] is None


def test_openrouter_fills_mandatory_and_default(fused):
    r = _model(fused, "claude-opus-5")["reasoning"]
    assert r["effort_parameter_required"] is False
    assert r["default"] == "high"
    assert "openrouter" in _model(fused, "claude-opus-5")["sources"]


def test_mandatory_stays_unknown_without_openrouter_coverage(fused):
    r = _model(fused, "nemotron-nano")["reasoning"]
    assert r.get("effort_parameter_required") is None


# Price tiers


def test_tiers_travel_and_base_price_is_unchanged():
    fused = fuse(
        _payloads(
            models_dev={
                "openai": {
                    "models": {
                        "gpt-x": {
                            "id": "gpt-x",
                            "cost": {
                                "input": 5,
                                "output": 30,
                                "tiers": [
                                    {
                                        "input": 10,
                                        "tier": {"type": "context", "size": 272000},
                                    }
                                ],
                            },
                            "reasoning": False,
                        }
                    }
                }
            }
        )
    )
    m = _model(fused, "gpt-x")
    assert m["price"]["input_mtok"] == 5
    assert m["price_tiers"][0]["when"] == {"dimension": "context", "over": 272000}

    (model,) = Registry.from_dict(fused).filter(model="gpt-x")
    assert model.price.get("input_mtok") == 5
    assert model.price_for(context=500000).get("input_mtok") == 10
    assert model.price_for(context=100000).get("input_mtok") == 5
    assert model.price_for(context=500000).get("output_mtok") == 30


# Admission of fallback-only models


def test_two_agreeing_fallbacks_admit_a_model_the_primary_lacks(fused):
    m = _model(fused, "mistral-large")
    assert m["type"] == "chat"
    assert m["price"]["input_mtok"] == 2
    assert set(m["sources"]) == {"genai_prices", "litellm"}


def test_disagreeing_fallbacks_do_not_admit(fused):
    assert all(m["id"] != "command-x" for m in fused["models"])


def test_agreement_without_a_type_does_not_admit(fused):
    assert all(m["id"] != "typeless" for m in fused["models"])


def test_single_source_knowledge_does_not_admit(fused):
    assert all(m["id"] != "tiered-model" for m in fused["models"])


# Degradation and envelope


def test_skipped_source_contributes_nothing_and_status_travels():
    fused = fuse(
        _payloads(litellm=None),
        statuses={
            "models_dev": "ok",
            "genai_prices": "ok",
            "litellm": "unreachable",
            "openrouter": "ok",
        },
    )
    opus = _model(fused, "claude-opus-5")
    assert opus["type"] is None
    assert "cache_write_1h_mtok" not in opus["price"]
    litellm = next(s for s in fused["sources"] if s["name"] == "litellm")
    assert litellm["status"] == "unreachable"
    assert litellm["fetched_at"] is None


def test_fused_output_loads_into_the_registry(fused):
    reg = Registry.from_dict(fused)
    assert len(reg.filter(provider="anthropic")) == 2
    assert len(reg.filter(status="deprecated")) == 1


def test_partial_upstream_dates_floor_to_the_period_start(fused):
    reg = Registry.from_dict(fused)
    (claude2,) = reg.filter(model="claude-2")
    assert claude2.lifecycle.release_date.isoformat() == "2023-07-01"


# Normalizer specifics


def test_litellm_per_token_costs_scale_to_mtok():
    records = normalize_litellm(LITELLM)
    assert records[("anthropic", "claude-opus-5")]["price"]["input_mtok"] == pytest.approx(5.0)


def test_litellm_provider_prefixed_keys_resolve_to_bare_ids():
    assert ("xai", "grok-4.3") in normalize_litellm(LITELLM)


def _models_dev_entry(cost, **extra):
    return {
        "openai": {
            "models": {"gpt-x": {"id": "gpt-x", "cost": cost, "reasoning": False, **extra}}
        }
    }


def test_models_dev_tiers_list_becomes_price_tiers():
    record = normalize_models_dev(
        _models_dev_entry(
            {
                "input": 5,
                "output": 30,
                "tiers": [
                    {"input": 10, "output": 45, "tier": {"type": "context", "size": 272000}}
                ],
            }
        )
    )[("openai", "gpt-x")]
    assert record["price"] == {"input_mtok": 5, "output_mtok": 30}
    assert record["price_tiers"] == [
        {
            "when": {"dimension": "context", "over": 272000},
            "price": {"input_mtok": 10, "output_mtok": 45},
        }
    ]


def test_over_200k_shorthand_fills_in_only_without_a_tiers_list():
    shorthand_only = normalize_models_dev(
        _models_dev_entry({"input": 5, "context_over_200k": {"input": 10}})
    )[("openai", "gpt-x")]
    assert shorthand_only["price_tiers"] == [
        {"when": {"dimension": "context", "over": 200000}, "price": {"input_mtok": 10}}
    ]

    # When both forms disagree on the boundary, the explicit list wins.
    both = normalize_models_dev(
        _models_dev_entry(
            {
                "input": 5,
                "context_over_200k": {"input": 10},
                "tiers": [{"input": 10, "tier": {"type": "context", "size": 272000}}],
            }
        )
    )[("openai", "gpt-x")]
    assert [t["when"]["over"] for t in both["price_tiers"]] == [272000]


# fetch_sources failure modes, driven through a patched fetcher


def _patch_fetch(monkeypatch, failing):
    from rates._http import FetchError

    def fake(url, timeout=None, token=None):
        for name, source_url in __import__("rates.ai._sources", fromlist=["SOURCE_URLS"]).SOURCE_URLS.items():
            if url == source_url and name in failing:
                raise FetchError(f"{url}: unreachable")
        return {}

    monkeypatch.setattr("rates.ai._fusion.fetch_json", fake)


def test_one_fallback_down_degrades_not_fails(monkeypatch):
    _patch_fetch(monkeypatch, failing={"openrouter"})
    payloads, statuses = fetch_sources()
    assert statuses["openrouter"] == "unreachable"
    assert payloads["openrouter"] is None
    assert statuses["models_dev"] == "ok"


def test_primary_down_refuses_a_hollow_result(monkeypatch):
    _patch_fetch(monkeypatch, failing={"models_dev"})
    with pytest.raises(PrimarySourceUnavailableError):
        fetch_sources()


def test_everything_down_raises_all_sources_unreachable(monkeypatch):
    _patch_fetch(
        monkeypatch,
        failing={"models_dev", "genai_prices", "litellm", "openrouter"},
    )
    with pytest.raises(AllSourcesUnreachableError):
        fetch_sources()
