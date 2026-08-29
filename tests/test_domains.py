"""The cross-domain contract: the neutral Record protocol and the domain
registry the CLI and loader read instead of hardcoding one domain."""

import pytest

from rates import _cli
from rates._domains import DOMAINS, DomainSpec
from rates._record import Record, SupportsLifecycle, SupportsPrice
from rates.ai import Model


def _a_model():
    return Model.from_dict(
        {
            "provider": "anthropic",
            "id": "claude-opus-5",
            "type": "chat",
            "price": {"currency": "USD", "input_mtok": 5},
            "lifecycle": {"status": "active"},
        }
    )


def test_ai_model_satisfies_the_core_record_contract():
    model = _a_model()
    assert isinstance(model, Record)
    assert isinstance(model.price, SupportsPrice)
    assert isinstance(model.lifecycle, SupportsLifecycle)


def test_record_reads_only_neutral_members():
    # The contract exposes the cross-domain vocabulary; a domain's own word
    # (Model.family) is not part of it.
    members = {name for name in dir(Record) if not name.startswith("_")}
    assert members == {"provider", "id", "type", "price", "lifecycle", "observed_at"}


def test_presentation_and_semantic_registries_list_the_same_domains():
    # Two registries (CLI presentation, core semantics) must never disagree on
    # which domains exist, or an unscoped query loads a domain it can't render.
    assert set(_cli.DOMAINS) == set(DOMAINS)


def test_ai_domain_declares_all_three_tiers():
    assert DOMAINS["ai"].fetch_tiers == ("bundled", "stable", "live")
    assert DOMAINS["ai"].staleness_days == 28


def test_ai_staleness_literal_matches_the_domains_own_constant():
    # _domains keeps staleness_days as a literal so reading the registry stays
    # import-light (no domain import); this guards the literal against drift
    # from rates.ai._load's own threshold.
    from rates.ai._load import STALENESS_THRESHOLD_DAYS

    assert DOMAINS["ai"].staleness_days == STALENESS_THRESHOLD_DAYS


def test_reading_the_registry_does_not_import_the_ai_domain():
    # The completion hot path imports the registry but never loads a domain, so
    # importing _domains must not drag in rates.ai's fusion machinery.
    import subprocess
    import sys

    probe = "import rates._domains, sys; print('rates.ai._fusion' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "False", result.stdout


def test_a_live_only_domain_refuses_a_bundled_read_with_a_clear_message(monkeypatch):
    # The durability case: a domain whose data can't ship as a snapshot offers
    # live only, and a bare read says so instead of serving one that shouldn't
    # exist.
    monkeypatch.setitem(
        _cli.DOMAIN_SPECS,
        "market",
        DomainSpec(
            name="market",
            load_target=lambda **_: None,
            fetch_tiers=("live",),
            staleness_days=None,
            rate_units=(),
            identity_kw="pair",
        ),
    )
    import argparse

    args = argparse.Namespace(fetch=None, timeout=None, force=False)
    with pytest.raises(Exception, match="no 'bundled' tier.*supports live"):
        _cli._timed_load("market", args)
