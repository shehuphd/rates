"""Tests for the resolution ladder: each rung deciding, skip semantics
(unknown is never zero), intra-rung ties restricting the field, and the
registry-order determinism floor. Adversarial cases first, per house
testing rules: the ladder must never tie, never rank a source on
evidence nobody gathered, and never let a witness re-enter a contest an
origin source ended."""

from datetime import date

import pytest

from rates._resolution import LADDER, Candidate, SourceCard, resolve


def _cards(**overrides: SourceCard) -> dict[str, SourceCard]:
    base = {
        "alpha": SourceCard(name="alpha", registry_rank=0),
        "beta": SourceCard(name="beta", registry_rank=1),
        "gamma": SourceCard(name="gamma", registry_rank=2),
    }
    base.update(overrides)
    return base


NO_DATES: dict[str, date | None] = {}


def test_empty_field_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        resolve([], cards=_cards(), provider="acme", freshness=NO_DATES)


def test_single_candidate_wins_at_the_floor():
    r = resolve(
        [Candidate("beta", 2.0)], cards=_cards(), provider="acme", freshness=NO_DATES
    )
    assert r.winner.source == "beta"
    assert r.resolved_by == "registry_order"


def test_origin_ends_the_contest():
    cards = _cards(
        gamma=SourceCard(name="gamma", registry_rank=2, origin_providers=("acme",))
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("gamma", 3.0)],
        cards=cards,
        provider="acme",
        freshness={"alpha": date(2026, 6, 1), "gamma": date(2020, 1, 1)},
    )
    # gamma is the horse's mouth for acme rows: it wins even though it's
    # far staler and lower-ranked, and freshness is never consulted.
    assert r.winner.source == "gamma"
    assert r.resolved_by == "origin"


def test_origin_is_per_provider_not_per_source():
    cards = _cards(
        gamma=SourceCard(name="gamma", registry_rank=2, origin_providers=("acme",))
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("gamma", 3.0)],
        cards=cards,
        provider="other-co",  # gamma is a mere witness here
        freshness={"alpha": date(2026, 6, 1), "gamma": date(2020, 1, 1)},
    )
    assert r.winner.source == "alpha"
    assert r.resolved_by == "freshness"


def test_two_origins_restrict_the_field_and_freshness_separates_them():
    # A provider's page and its API disagreeing: both first-party, so
    # the field narrows to them and the rungs below decide, with the
    # non-origin witness locked out even if it's the freshest of all.
    cards = _cards(
        alpha=SourceCard(name="alpha", registry_rank=0, origin_providers=("acme",)),
        beta=SourceCard(name="beta", registry_rank=1, origin_providers=("acme",)),
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0), Candidate("gamma", 3.0)],
        cards=cards,
        provider="acme",
        freshness={
            "alpha": date(2026, 1, 1),
            "beta": date(2026, 6, 1),
            "gamma": date(2026, 8, 1),
        },
    )
    assert r.winner.source == "beta"
    assert r.resolved_by == "freshness"


def test_strictly_freshest_wins():
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0)],
        cards=_cards(),
        provider="acme",
        freshness={"alpha": date(2026, 1, 1), "beta": date(2026, 6, 1)},
    )
    assert r.winner.source == "beta"
    assert r.resolved_by == "freshness"


def test_one_unmeasured_candidate_skips_the_freshness_rung_whole():
    # unknown is never stale: a dated candidate must not beat one whose
    # freshness simply couldn't be checked.
    cards = _cards(
        alpha=SourceCard(name="alpha", registry_rank=0, coverage=0.9),
        beta=SourceCard(name="beta", registry_rank=1, coverage=0.5),
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0)],
        cards=cards,
        provider="acme",
        freshness={"beta": date(2026, 6, 1)},  # alpha unmeasured
    )
    assert r.resolved_by == "coverage"
    assert r.winner.source == "alpha"


def test_corroboration_counts_upstream_groups_not_raw_sources():
    # beta and gamma agree on 2.0 but share one upstream, so they cast
    # one vote between them; alpha's distinct upstream also casts one.
    # 1 vote vs 1 vote: corroboration can't separate, coverage does.
    cards = _cards(
        alpha=SourceCard(
            name="alpha", registry_rank=0, upstreams=("origin-a",), coverage=0.9
        ),
        beta=SourceCard(
            name="beta", registry_rank=1, upstreams=("shared-hub",), coverage=0.5
        ),
        gamma=SourceCard(
            name="gamma", registry_rank=2, upstreams=("shared-hub",), coverage=0.4
        ),
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0), Candidate("gamma", 2.0)],
        cards=cards,
        provider="acme",
        freshness=NO_DATES,
    )
    assert r.resolved_by == "coverage"
    assert r.winner.source == "alpha"


def test_corroboration_decides_when_independent_witnesses_agree():
    cards = _cards(
        alpha=SourceCard(name="alpha", registry_rank=0, upstreams=("origin-a",)),
        beta=SourceCard(name="beta", registry_rank=1, upstreams=("origin-b",)),
        gamma=SourceCard(name="gamma", registry_rank=2, upstreams=("origin-c",)),
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0), Candidate("gamma", 2.0)],
        cards=cards,
        provider="acme",
        freshness=NO_DATES,
    )
    # Two independent upstreams vouch for 2.0 against one for 1.0: the
    # rung restricts the field to the 2.0 class (alpha is out), and the
    # floor separates the two agreeing witnesses by rank.
    assert r.winner.value == 2.0
    assert r.winner.source == "beta"
    assert r.resolved_by == "registry_order"


def test_undeclared_upstreams_keep_corroboration_inert():
    cards = _cards(
        alpha=SourceCard(name="alpha", registry_rank=0, coverage=0.2),
        beta=SourceCard(name="beta", registry_rank=1, upstreams=("b",), coverage=0.9),
        gamma=SourceCard(name="gamma", registry_rank=2, upstreams=("c",), coverage=0.9),
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0), Candidate("gamma", 2.0)],
        cards=cards,
        provider="acme",
        freshness=NO_DATES,
    )
    # alpha's upstreams are undeclared, so majority agreement on 2.0 may
    # not vote (it could be one catalog counted twice); the tie falls to
    # coverage, where beta and gamma tie and registry order decides.
    assert r.resolved_by != "corroboration"


def test_promoted_override_outranks_the_default_class():
    cards = _cards(
        gamma=SourceCard(
            name="gamma",
            registry_rank=2,
            override="promote",
            override_reason="pinned for the test",
        )
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("gamma", 3.0)],
        cards=cards,
        provider="acme",
        freshness=NO_DATES,
    )
    assert r.winner.source == "gamma"
    assert r.resolved_by == "preferred"


def test_demoted_override_loses_to_the_default_class():
    cards = _cards(
        alpha=SourceCard(
            name="alpha",
            registry_rank=0,
            override="demote",
            override_reason="pinned for the test",
        )
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0)],
        cards=cards,
        provider="acme",
        freshness=NO_DATES,
    )
    assert r.winner.source == "beta"
    assert r.resolved_by == "preferred"


def test_accuracy_requires_every_candidate_measured():
    cards = _cards(
        alpha=SourceCard(name="alpha", registry_rank=0, wrongness=0.4, coverage=0.3),
        beta=SourceCard(name="beta", registry_rank=1, coverage=0.9),  # unmeasured
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0)],
        cards=cards,
        provider="acme",
        freshness=NO_DATES,
    )
    # beta's wrongness is unmeasured, not zero: alpha's bad measured
    # record may not lose to evidence nobody gathered about beta.
    assert r.resolved_by == "coverage"
    assert r.winner.source == "beta"


def test_lowest_measured_wrongness_wins():
    cards = _cards(
        alpha=SourceCard(name="alpha", registry_rank=0, wrongness=0.4),
        beta=SourceCard(name="beta", registry_rank=1, wrongness=0.1),
    )
    r = resolve(
        [Candidate("alpha", 1.0), Candidate("beta", 2.0)],
        cards=cards,
        provider="acme",
        freshness=NO_DATES,
    )
    assert r.winner.source == "beta"
    assert r.resolved_by == "accuracy"


def test_registry_order_is_the_floor_that_never_ties():
    r = resolve(
        [Candidate("gamma", 3.0), Candidate("beta", 2.0), Candidate("alpha", 1.0)],
        cards=_cards(),
        provider="acme",
        freshness=NO_DATES,
    )
    assert r.winner.source == "alpha"  # rank 0
    assert r.resolved_by == "registry_order"


def test_every_resolved_by_value_is_a_ladder_rung():
    # The vocabulary a note can carry is the ladder itself, nothing else.
    assert set(LADDER) == {
        "origin",
        "freshness",
        "corroboration",
        "preferred",
        "accuracy",
        "coverage",
        "registry_order",
    }
