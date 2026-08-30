"""Tests for the AI domain's model dataclasses, built from the worked
examples in ERD.md verbatim."""

from datetime import date

from rates.ai import Model

# The claude-opus-5 worked example from ERD.md.
OPUS_5 = {
    "provider": "anthropic",
    "id": "claude-opus-5",
    "family": "claude-opus",
    "type": "chat",
    "modalities": {"input": ["text", "image", "file"], "output": ["text"]},
    "context": {"input": 1000000, "output": 128000},
    "price": {
        "currency": "USD",
        "input_mtok": 5,
        "output_mtok": 25,
        "cache_read_mtok": 0.5,
        "cache_write_mtok": 6.25,
        "cache_write_1h_mtok": 10,
        "web_search_per_kcount": 10,
    },
    "price_discrepancies": [],
    "reasoning": {
        "control": "effort",
        "effort_parameter_required": False,
        "can_disable_reasoning": False,
        "levels": [
            {"label": "low", "rank": 1},
            {"label": "medium", "rank": 2},
            {"label": "high", "rank": 3},
            {"label": "xhigh", "rank": 4},
            {"label": "max", "rank": 5},
        ],
        "range": [1, 5],
        "default": "high",
    },
    "tool_call": True,
    "structured_output": True,
    "lifecycle": {
        "status": "active",
        "release_date": "2026-07-24",
        "deprecation_date": None,
    },
    "sources": {
        "models_dev": "2026-08-16",
        "genai_prices": "2026-08-16",
        "openrouter": "2026-08-16",
    },
}

# The deepseek discrepancy example from ERD.md: a partial record whose
# sources disagree on input price.
DEEPSEEK = {
    "provider": "openrouter",
    "id": "deepseek/deepseek-chat-v3.1",
    "price": {"currency": "USD", "input_mtok": 0.55},
    "price_discrepancies": [
        {
            "field": "input_mtok",
            "chosen_source": "models_dev",
            "chosen_value": 0.55,
            "other_source": "genai_prices",
            "other_value": 0.21,
            "resolved_by": "preference",
            "difference_pct": 61.8,
        }
    ],
}


def test_worked_example_parses_field_by_field():
    m = Model.from_dict(OPUS_5)
    assert m.provider == "anthropic"
    assert m.id == "claude-opus-5"
    assert m.family == "claude-opus"
    assert m.type == "chat"
    assert m.modalities.input == ("text", "image", "file")
    assert m.modalities.output == ("text",)
    assert m.context.input == 1000000
    assert m.context.output == 128000
    assert m.tool_call is True
    assert m.structured_output is True
    assert m.price.currency == "USD"
    assert m.price.get("input_mtok") == 5
    assert m.price.get("web_search_per_kcount") == 10
    assert m.price_discrepancies == ()
    assert m.lifecycle.status == "active"
    assert m.lifecycle.release_date == date(2026, 7, 24)
    assert m.lifecycle.deprecation_date is None
    assert m.sources["models_dev"] == "2026-08-16"


def test_reasoning_shape_from_worked_example():
    r = Model.from_dict(OPUS_5).reasoning
    assert r is not None
    assert r.control == "effort"
    assert r.effort_parameter_required is False
    assert r.can_disable_reasoning is False
    assert [lv.label for lv in r.levels] == ["low", "medium", "high", "xhigh", "max"]
    assert [lv.rank for lv in r.levels] == [1, 2, 3, 4, 5]
    assert r.range == (1, 5)
    assert r.default == "high"


def test_no_reasoning_capability_is_absent_not_empty():
    m = Model.from_dict({"provider": "x", "id": "y"})
    assert m.reasoning is None


def test_partial_record_defaults_missing_fields_to_none():
    m = Model.from_dict(DEEPSEEK)
    assert m.family is None
    assert m.type is None
    assert m.tool_call is None
    assert m.structured_output is None
    assert m.reasoning is None
    assert m.lifecycle.status is None
    assert m.modalities.input == ()


def test_discrepancy_record_parses():
    d = Model.from_dict(DEEPSEEK).price_discrepancies[0]
    assert d.field == "input_mtok"
    assert d.chosen_source == "models_dev"
    assert d.chosen_value == 0.55
    assert d.other_source == "genai_prices"
    assert d.other_value == 0.21
    assert d.resolved_by == "preference"
    assert d.difference_pct == 61.8


def test_price_get_returns_none_for_unit_the_model_does_not_bill_on():
    m = Model.from_dict(DEEPSEEK)
    assert m.price.get("output_per_second") is None


def test_currency_is_not_a_price_unit():
    m = Model.from_dict(OPUS_5)
    assert "currency" not in m.price.units


def test_to_dict_round_trips_through_from_dict():
    for record in (OPUS_5, DEEPSEEK):
        m = Model.from_dict(record)
        assert Model.from_dict(m.to_dict()) == m


# Alias: baked in at ledger-build time from KeyCall's convention catalog,
# never computed at runtime. See scripts/build_ledger.py and ERD.md § ALIAS.


def test_dated_id_has_no_alias_by_default():
    # claude-opus-5 is a pinned snapshot, not a rolling reference; the
    # worked example carries no "alias" key at all.
    m = Model.from_dict(OPUS_5)
    assert m.alias is None
    assert "alias" not in m.to_dict()


def test_alias_fact_parses_every_field():
    record = {
        **OPUS_5,
        "alias": {
            "convention": "-latest suffix",
            "maintained": True,
            "verified": "2026-08-10",
            "note": "Gemini keeps this aimed at a live model.",
        },
    }
    m = Model.from_dict(record)
    assert m.alias is not None
    assert m.alias.convention == "-latest suffix"
    assert m.alias.maintained is True
    assert m.alias.verified == date(2026, 8, 10)
    assert m.alias.note == "Gemini keeps this aimed at a live model."


def test_alias_maintained_is_tristate_not_defaulted_to_false():
    # A convention can be recorded with liveness unverified: None, never a
    # guessed False (same tri-state rule as tool_call/structured_output).
    record = {
        **OPUS_5,
        "alias": {
            "convention": "-preview suffix",
            "verified": "2026-08-10",
            "note": "Convention recorded; liveness not yet checked.",
        },
    }
    assert Model.from_dict(record).alias.maintained is None


def test_alias_stale_family_reports_maintained_false_not_omitted():
    # OpenAI's -chat-latest family went stale/dead on 2026-08-10; that's a
    # fact to carry, not a reason to hide the alias entry.
    record = {
        **OPUS_5,
        "alias": {
            "convention": "-chat-latest suffix",
            "maintained": False,
            "verified": "2026-08-10",
            "note": "Observed dead; dated models stayed healthy.",
        },
    }
    assert Model.from_dict(record).alias.maintained is False


def test_alias_round_trips_through_to_dict():
    record = {
        **OPUS_5,
        "alias": {
            "convention": "-latest suffix",
            "maintained": True,
            "verified": "2026-08-10",
            "note": "Gemini keeps this aimed at a live model.",
        },
    }
    m = Model.from_dict(record)
    assert Model.from_dict(m.to_dict()) == m
    assert m.to_dict()["alias"] == record["alias"]
