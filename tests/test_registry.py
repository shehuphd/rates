"""Tests for Registry.filter and Registry.sort_by: failure paths and match
semantics first, happy path after."""

import pytest

from rates.ai import Registry

REGISTRY = {
    "schema_version": "1.0.0",
    "domain": "ai",
    "snapshot_date": "2026-08-22",
    "sources": [
        {
            "name": "models_dev",
            "fetched_at": "2026-08-22",
            "role": "preferred",
            "status": "ok",
        }
    ],
    "models": [
        {
            "provider": "anthropic",
            "id": "claude-opus-5",
            "family": "claude-opus",
            "type": "chat",
            "modalities": {"input": ["text", "image"], "output": ["text"]},
            "tool_call": True,
            "structured_output": True,
            "price": {"currency": "USD", "input_mtok": 5, "output_mtok": 25},
            "lifecycle": {"status": "active"},
        },
        {
            "provider": "anthropic",
            "id": "claude-opus-4-8",
            "family": "claude-opus",
            "type": "chat",
            "tool_call": True,
            "price": {"currency": "USD", "input_mtok": 5, "output_mtok": 25},
            "lifecycle": {"status": "active"},
        },
        {
            "provider": "anthropic",
            "id": "claude-haiku-4-5",
            "family": "claude-haiku",
            "type": "chat",
            "tool_call": True,
            "price": {"currency": "USD", "input_mtok": 1, "output_mtok": 5},
            "lifecycle": {"status": "active"},
        },
        {
            "provider": "google",
            "id": "veo-3.1-fast-generate-preview",
            "type": "video_generation",
            "price": {"currency": "USD", "output_per_second": 0.15},
            "lifecycle": {"status": "beta"},
        },
        {
            "provider": "openai",
            "id": "gpt-4-32k",
            "type": "chat",
            "price": {"currency": "USD", "input_mtok": 60, "output_mtok": 120},
            "lifecycle": {"status": "deprecated"},
        },
    ],
}


@pytest.fixture
def registry():
    return Registry.from_dict(REGISTRY)


# Failure paths


def test_unknown_criterion_raises_and_names_it(registry):
    with pytest.raises(TypeError, match="unknown filter criterion 'colour'"):
        registry.filter(colour="blue")


def test_unknown_criterion_error_lists_valid_names(registry):
    with pytest.raises(TypeError, match="model_contains"):
        registry.filter(colour="blue")


def test_price_unit_alone_raises(registry):
    with pytest.raises(ValueError, match="price_unit"):
        registry.filter(price_unit="output_mtok")


def test_price_bound_without_unit_raises_listing_this_registrys_units(registry):
    with pytest.raises(ValueError, match="input_mtok.*output_per_second"):
        registry.filter(price_max=5.00)


def test_price_bound_with_an_unknown_unit_raises_not_silently_zero(registry):
    with pytest.raises(ValueError, match="'input_mt' isn't a price unit"):
        registry.filter(price_max=1, price_unit="input_mt")


def test_sort_by_requires_explicit_direction(registry):
    with pytest.raises(TypeError):
        registry.sort_by("id")


def test_sort_by_rejects_positional_direction(registry):
    with pytest.raises(TypeError):
        registry.sort_by("id", True)


def test_sort_by_unknown_field_raises(registry):
    with pytest.raises(TypeError, match="can't sort by 'colour'"):
        registry.sort_by("colour", descending=False)


def test_zero_matches_returns_empty_registry_with_envelope_intact(registry):
    result = registry.filter(provider="mistral")
    assert len(result) == 0
    assert result.schema_version == "1.0.0"
    assert result.snapshot_date == registry.snapshot_date
    assert result.sources == registry.sources


# Match semantics


def test_exact_match_is_case_insensitive_both_ways(registry):
    assert len(registry.filter(provider="ANTHROPIC")) == 3
    assert len(registry.filter(model="CLAUDE-OPUS-5")) == 1


def test_exact_match_never_broadens_to_substring(registry):
    assert len(registry.filter(model="opus")) == 0


def test_contains_matches_substring_case_insensitively(registry):
    assert len(registry.filter(model_contains="OPUS")) == 2


def test_bool_criterion_excludes_records_where_the_fact_is_unknown(registry):
    # gpt-4-32k and veo carry no tool_call value; unknown isn't False,
    # and it isn't True either.
    assert len(registry.filter(tool_call=True)) == 3
    assert len(registry.filter(tool_call=False)) == 0


def test_price_max_compares_against_the_named_unit(registry):
    result = registry.filter(price_max=5.00, price_unit="input_mtok")
    assert {m.id for m in result} == {
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-haiku-4-5",
    }


def test_price_constraint_excludes_models_not_billing_on_that_unit(registry):
    # veo has no input_mtok at all: a constraint on a unit the model
    # doesn't bill on can't be satisfied.
    assert "veo-3.1-fast-generate-preview" not in {
        m.id for m in registry.filter(price_max=1000, price_unit="input_mtok")
    }


def test_price_unit_targets_non_token_billing(registry):
    result = registry.filter(price_max=0.20, price_unit="output_per_second")
    assert {m.id for m in result} == {"veo-3.1-fast-generate-preview"}


def test_price_min_and_max_bound_a_range(registry):
    result = registry.filter(price_min=2, price_max=10, price_unit="input_mtok")
    assert {m.id for m in result} == {"claude-opus-5", "claude-opus-4-8"}


def test_price_units_enumerates_what_this_registry_bills_on(registry):
    assert registry.price_units() == [
        "input_mtok",
        "output_mtok",
        "output_per_second",
    ]


def test_currency_matches_case_insensitively(registry):
    assert len(registry.filter(currency="usd")) == 5


def test_status_filter(registry):
    assert len(registry.filter(status="deprecated")) == 1
    assert len(registry.filter(status="active")) == 3


def test_modality_membership_is_case_insensitive(registry):
    result = registry.filter(modality_input="IMAGE")
    assert {m.id for m in result} == {"claude-opus-5"}


# Composition and ordering


def test_criteria_combine_with_and_semantics(registry):
    result = registry.filter(
        provider="anthropic", family="claude-opus", price_max=5, price_unit="input_mtok"
    )
    assert {m.id for m in result} == {"claude-opus-5", "claude-opus-4-8"}


def test_filter_then_sort_chains_and_keeps_envelope(registry):
    result = registry.filter(type="chat").sort_by("price.input_mtok", descending=False)
    ids = [m.id for m in result]
    assert ids[0] == "claude-haiku-4-5"
    assert ids[-1] == "gpt-4-32k"
    assert result.schema_version == "1.0.0"


def test_sort_descending(registry):
    result = registry.filter(type="chat").sort_by("price.input_mtok", descending=True)
    assert next(m.id for m in result) == "gpt-4-32k"


def test_sort_puts_models_without_the_field_last_in_both_directions(registry):
    ascending = registry.sort_by("price.input_mtok", descending=False)
    descending = registry.sort_by("price.input_mtok", descending=True)
    assert list(ascending)[-1].id == "veo-3.1-fast-generate-preview"
    assert list(descending)[-1].id == "veo-3.1-fast-generate-preview"


def test_sort_by_model_attribute(registry):
    result = registry.sort_by("id", descending=False)
    ids = [m.id for m in result]
    assert ids == sorted(ids)


def test_registry_is_iterable_and_sized(registry):
    assert len(registry) == 5
    assert len(list(registry)) == 5


def test_filter_does_not_mutate_the_original(registry):
    registry.filter(provider="anthropic")
    assert len(registry) == 5
