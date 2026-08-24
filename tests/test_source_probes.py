"""Capability-drift probes: each upstream source's live payload still has
the shape the normalizers in rates/ai/_sources.py rely on.

These hit the sources raw (not through the fusion), so a failure means the
source changed, not that the merge has a bug. A failing probe is the
notification to update the matching normalizer, ERD.md's source table, and
this probe. Skipped unless RATES_LIVE_PROBES=1; run on the weekly ledger
cycle.
"""

import os

import pytest

from rates._http import fetch_json
from rates.ai._sources import SOURCE_URLS

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def token():
    return os.environ.get("GITHUB_TOKEN")


def test_models_dev_shape_still_holds(token):
    data = fetch_json(SOURCE_URLS["models_dev"])
    assert isinstance(data, dict) and data, "expected a provider-keyed dict"
    provider = data.get("anthropic")
    assert provider and "models" in provider, (
        "anthropic provider with a 'models' dict is what "
        "normalize_models_dev walks"
    )
    model = next(iter(provider["models"].values()))
    for field in ("id", "cost", "modalities", "reasoning"):
        assert field in model, (
            f"models.dev model entries no longer carry {field!r}; update "
            "normalize_models_dev, ERD.md's source table, and this probe"
        )
    cost = model["cost"]
    assert isinstance(cost, dict) and "input" in cost, (
        "models.dev cost is read as a dict with an 'input' key ($/mtok)"
    )


def test_genai_prices_shape_still_holds(token):
    data = fetch_json(SOURCE_URLS["genai_prices"], token=token)
    assert isinstance(data, list) and data, "expected a list of providers"
    provider = next(p for p in data if p.get("id") == "anthropic")
    model = provider["models"][0]
    assert "id" in model and "prices" in model, (
        "genai-prices models no longer carry id/prices; update "
        "normalize_genai_prices and this probe"
    )
    flat = [m for m in provider["models"] if isinstance(m.get("prices"), dict)]
    assert flat, (
        "genai-prices no longer serves the flat dict price form the "
        "normalizer reads (only the tiered list form?); update "
        "normalize_genai_prices"
    )
    assert any("input_mtok" in m["prices"] for m in flat), (
        "genai-prices prices no longer keyed by input_mtok-style units"
    )


def test_litellm_shape_still_holds(token):
    data = fetch_json(SOURCE_URLS["litellm"], token=token)
    assert isinstance(data, dict), "expected a flat model-keyed dict"
    entries = [
        v for v in data.values()
        if isinstance(v, dict) and v.get("litellm_provider")
    ]
    assert entries, "no entries carrying litellm_provider"
    assert any(e.get("mode") for e in entries), (
        "LiteLLM 'mode' (our type field's source) is gone; update "
        "normalize_litellm and ERD.md"
    )
    assert any(
        isinstance(e.get("input_cost_per_token"), (int, float)) for e in entries
    ), "LiteLLM per-token cost fields are gone or renamed"
    assert any(e.get("deprecation_date") for e in entries), (
        "LiteLLM deprecation_date (LIFECYCLE's only date source) is gone"
    )


def test_openrouter_reasoning_object_still_carries_mandatory(token):
    data = fetch_json(SOURCE_URLS["openrouter"])
    with_reasoning = [
        r for r in data.get("data", []) if isinstance(r.get("reasoning"), dict)
    ]
    assert with_reasoning, (
        "OpenRouter's per-model reasoning object is gone; it's the only "
        "source for effort_parameter_required and default effort — update "
        "normalize_openrouter and ERD.md's REASONING section"
    )
    assert any("mandatory" in r["reasoning"] for r in with_reasoning), (
        "OpenRouter reasoning.mandatory is gone or renamed"
    )


def test_fallback_only_admissions_stay_consistent(token):
    """Every fallback-only model clearing the admission bar appears in the
    fused output — a live consistency check on ARCHITECTURE.md's admission
    criteria, run on the weekly cycle."""
    from rates.ai._fusion import _admit_fallback_only, fuse
    from rates.ai._sources import (
        normalize_genai_prices,
        normalize_litellm,
        normalize_models_dev,
        normalize_openrouter,
    )

    payloads = {
        name: fetch_json(
            url, token=token if "githubusercontent" in url else None
        )
        for name, url in SOURCE_URLS.items()
    }
    fused = fuse(payloads)
    in_registry = {(m["provider"].casefold(), m["id"].casefold()) for m in fused["models"]}

    normalized = {
        "models_dev": normalize_models_dev(payloads["models_dev"]),
        "genai_prices": normalize_genai_prices(payloads["genai_prices"]),
        "litellm": normalize_litellm(payloads["litellm"]),
        "openrouter": normalize_openrouter(payloads["openrouter"]),
    }
    admitted = _admit_fallback_only(
        normalized, preferred_keys=set(normalized["models_dev"]), today="probe"
    )
    missing = [
        (m["provider"], m["id"])
        for m in admitted
        if (m["provider"].casefold(), m["id"].casefold()) not in in_registry
    ]
    assert not missing, (
        f"{len(missing)} fallback-only models clear the admission bar but "
        f"aren't in the fused output, e.g. {missing[:5]}; the fusion owes "
        "them entries"
    )


def test_no_source_carries_a_currency_field(token):
    """The fusion stamps every price as USD because no source publishes a
    per-model currency today; that's an absence claim, so it gets a probe.
    If a source starts carrying currency, this fails and the merge must
    read it instead of assuming."""
    models_dev = fetch_json(SOURCE_URLS["models_dev"])
    sample = list(models_dev.get("anthropic", {}).get("models", {}).values())[:20]
    assert sample and all(
        "currency" not in (m.get("cost") or {}) for m in sample
    ), "models.dev cost entries now carry a currency field; stop assuming USD"

    genai = fetch_json(SOURCE_URLS["genai_prices"], token=token)
    provider = next(p for p in genai if p.get("id") == "anthropic")
    flat = [m for m in provider["models"] if isinstance(m.get("prices"), dict)]
    assert flat and all(
        "currency" not in m["prices"] for m in flat
    ), "genai-prices now carries a currency field; stop assuming USD"

    litellm = fetch_json(SOURCE_URLS["litellm"], token=token)
    entries = [
        v for v in litellm.values()
        if isinstance(v, dict) and v.get("litellm_provider")
    ][:50]
    assert entries and all("currency" not in e for e in entries), (
        "LiteLLM entries now carry a currency field; stop assuming USD"
    )


def test_openrouter_shape_still_holds(token):
    data = fetch_json(SOURCE_URLS["openrouter"])
    rows = data.get("data")
    assert isinstance(rows, list) and rows, "expected {'data': [models]}"
    row = next(r for r in rows if "/" in r.get("id", ""))
    arch = row.get("architecture") or {}
    assert "input_modalities" in arch and "output_modalities" in arch, (
        "OpenRouter architecture modality arrays are gone; update "
        "normalize_openrouter"
    )
