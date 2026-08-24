"""The code recipes documented in USAGE.md, verbatim, so the manual can't
drift from what the library does."""

import pytest


def test_readme_recipe():
    import rates.ai

    registry = rates.ai.load()
    under_a_dollar = registry.filter(price_max=1, price_unit="input_mtok")
    cheapest_first = under_a_dollar.sort_by("price.input_mtok", descending=False)
    assert len(cheapest_first) > 0


def test_loading_recipe():
    import rates.ai

    registry = rates.ai.load()
    assert len(registry) > 0


def test_warning_escalation_recipe():
    import warnings

    import rates

    warnings.simplefilter("error", rates.StaleLedgerWarning)  # stale data becomes a hard failure
    warnings.resetwarnings()  # not part of the recipe; keeps this test isolated


def test_querying_recipe(capsys):
    import rates.ai

    registry = rates.ai.load()
    affordable = registry.filter(
        provider="anthropic", type="chat", price_max=5, price_unit="input_mtok"
    )
    cheapest_first = affordable.sort_by("price.input_mtok", descending=False)
    for model in cheapest_first:
        print(model.id, model.price.get("input_mtok"))

    out = capsys.readouterr().out
    assert out
    rates_shown = [float(line.rsplit(" ", 1)[1]) for line in out.splitlines()]
    assert rates_shown == sorted(rates_shown)


def test_price_units_recipe():
    import rates.ai

    registry = rates.ai.load()
    units = registry.price_units()
    assert "input_mtok" in units and "output_mtok" in units


def test_working_with_a_model_recipe():
    import rates.ai

    registry = rates.ai.load()
    (opus,) = registry.filter(provider="anthropic", model="claude-opus-5")

    assert opus.price.get("input_mtok") == 5
    assert [lv.label for lv in opus.reasoning.levels] == [
        "low", "medium", "high", "xhigh", "max"
    ]
    assert opus.to_dict()["id"] == "claude-opus-5"


def test_price_for_recipe():
    import rates.ai

    registry = rates.ai.load()
    tiered = [m for m in registry if m.price_tiers and m.price.get("input_mtok")]
    if not tiered:
        pytest.skip("no tiered model with an input rate in this snapshot")
    model = tiered[0]
    base = model.price.get("input_mtok")
    at_500k = model.price_for(context=500_000).get("input_mtok")
    assert base is not None and at_500k is not None


def test_discrepancy_recipe(capsys):
    import rates.ai

    registry = rates.ai.load()
    flagged = [m for m in registry if m.price_discrepancies]
    if not flagged:
        pytest.skip("no price disagreements in this snapshot")
    model = flagged[0]
    for d in model.price_discrepancies:
        print(d.field, d.chosen_value, "vs", d.other_source, d.other_value)
    assert "vs" in capsys.readouterr().out
