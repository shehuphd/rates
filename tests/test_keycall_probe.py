"""Capability-drift probe: keycall's real, installed alias_fact contract
still has the shape scripts/build_ledger.py._resolve_alias relies on.

Unlike tests/test_source_probes.py this doesn't hit the network: keycall's
alias_fact is keyless and reads its own bundled catalog, so "live" here
means "the currently installed keycall release," not a live HTTP call.
Gated the same way regardless, because the thing being probed still
drifts independently of rates' own code: a keycall release can change
which providers/conventions it recognizes, or what a specific model's
fact says. A failing probe is the notification to update
scripts/build_ledger.py, ERD.md's ALIAS section, and this probe, not a
rates regression. Skipped unless RATES_LIVE_PROBES=1; run on the weekly
ledger cycle.
"""

import pytest

keycall = pytest.importorskip("keycall")

pytestmark = pytest.mark.live


def test_alias_fact_contract_shape_still_holds():
    fact = keycall.alias_fact("openai", "gpt-5.6-chat-latest")
    assert fact is not None, (
        "keycall no longer recognizes OpenAI's -chat-latest convention "
        "for this id; update the probe with a current example or "
        "confirm the convention was retired upstream"
    )
    for attr in ("provider", "model_id", "convention", "maintained", "verified", "note"):
        assert hasattr(fact, attr), (
            f"keycall's AliasFact no longer carries {attr!r}; update "
            "scripts/build_ledger.py._resolve_alias and ERD.md's ALIAS section"
        )
    assert isinstance(fact.maintained, bool) or fact.maintained is None, (
        "AliasFact.maintained is no longer the documented tri-state bool | None"
    )


def test_unsupported_provider_still_raises_the_documented_error():
    with pytest.raises(keycall.KeyCallError) as excinfo:
        keycall.alias_fact("some-provider-keycall-has-never-heard-of", "whatever-v1")
    assert excinfo.value.code == keycall.ErrorCode.UNSUPPORTED_PROVIDER, (
        "keycall no longer raises UNSUPPORTED_PROVIDER for an unrecognized "
        "provider; update _resolve_alias's except clause"
    )


def test_dated_id_with_no_convention_is_none_not_a_guess():
    assert keycall.alias_fact("anthropic", "claude-opus-5") is None, (
        "keycall returned a fact for a dated, pinned id; alias_fact should "
        "only match an established rolling-alias convention"
    )
