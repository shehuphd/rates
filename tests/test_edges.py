"""Adversarial and edge coverage for everything the capability-focused
suites don't reach: malformed inputs first, boundary values, hostile
structures, then the remaining happy paths."""

import subprocess
import sys
import urllib.request

import pytest

from rates import _cli
from rates.ai import Model, Price, PriceTier, Reasoning, Registry
from rates.ai._model import _parse_date

# Parsing: malformed and partial input


def test_garbage_date_raises_rather_than_inventing_one():
    with pytest.raises(ValueError):
        _parse_date("not-a-date")


def test_impossible_month_raises():
    with pytest.raises(ValueError):
        _parse_date("2025-13")


def test_year_only_date_floors_to_january_first():
    assert _parse_date("2025").isoformat() == "2025-01-01"


def test_single_element_range_reads_as_absent_not_a_crash():
    r = Reasoning.from_dict(
        {"control": "effort", "can_disable_reasoning": False, "range": [1]}
    )
    assert r.range is None


def test_budget_with_missing_ends_keeps_what_exists():
    r = Reasoning.from_dict(
        {"control": "budget_tokens", "can_disable_reasoning": False,
         "budget": {"max": 32768}}
    )
    assert r.budget == (None, 32768)


def test_price_from_dict_with_currency_only_has_no_units():
    p = Price.from_dict({"currency": "USD"})
    assert p.units == {} and p.currency == "USD"


def test_price_tier_from_dict_with_missing_when_defaults_empty():
    t = PriceTier.from_dict({"price": {"input_mtok": 5}})
    assert t.when_dimension == "" and t.when_over == 0


def test_model_from_dict_requires_identity():
    with pytest.raises(KeyError):
        Model.from_dict({"provider": "x"})
    with pytest.raises(KeyError):
        Model.from_dict({"id": "y"})


def test_registry_from_dict_of_empty_dict_is_an_empty_registry():
    reg = Registry.from_dict({})
    assert len(reg) == 0
    assert reg.price_units() == []
    assert len(reg.filter(provider="anyone")) == 0


# Registry query semantics at the boundaries


REG = Registry.from_dict(
    {
        "schema_version": "1.0.0",
        "snapshot_date": "2026-08-22",
        "models": [
            {
                "provider": "anthropic",
                "id": "claude-opus-5",
                "type": "chat",
                "price": {"currency": "USD", "input_mtok": 5, "output_mtok": 25},
                "price_tiers": [
                    {"when": {"dimension": "context", "over": 200000},
                     "price": {"input_mtok": 10}},
                    {"when": {"dimension": "context", "over": 500000},
                     "price": {"input_mtok": 20}},
                ],
                "lifecycle": {"status": "active"},
            },
            {
                "provider": "google",
                "id": "veo-3.1",
                "type": "video_generation",
                "price": {"currency": "USD", "output_per_second": 0.15},
                "lifecycle": {"status": "beta"},
            },
        ],
    }
)


def test_contradictory_price_bounds_return_zero_matches_not_an_error():
    assert len(REG.filter(price_min=10, price_max=1, price_unit="input_mtok")) == 0


def test_empty_contains_matches_everything():
    # An empty substring is an empty constraint, the same as omitting the
    # flag: every string contains "".
    assert len(REG.filter(model_contains="")) == len(REG)


def test_filter_criteria_accept_non_string_values_by_coercion():
    assert len(REG.filter(model=123)) == 0  # coerced, compared, no crash


def test_sort_on_a_field_every_record_lacks_keeps_all_records():
    result = REG.sort_by("price.reasoning_mtok", descending=False)
    assert len(result) == len(REG)


def test_filter_then_price_units_reflects_the_narrowed_set():
    assert REG.filter(provider="google").price_units() == ["output_per_second"]


# price_for boundaries


def test_price_for_at_the_threshold_is_still_base():
    (opus,) = REG.filter(model="claude-opus-5")
    assert opus.price_for(context=200000).get("input_mtok") == 5


def test_price_for_between_tiers_uses_the_lower_tier():
    (opus,) = REG.filter(model="claude-opus-5")
    assert opus.price_for(context=300000).get("input_mtok") == 10


def test_price_for_past_every_tier_uses_the_highest():
    (opus,) = REG.filter(model="claude-opus-5")
    assert opus.price_for(context=900000).get("input_mtok") == 20


def test_price_for_with_an_unknown_dimension_is_base_unchanged():
    (opus,) = REG.filter(model="claude-opus-5")
    assert opus.price_for(altitude=99999).get("input_mtok") == 5


def test_price_for_on_a_tierless_model_is_base():
    (veo,) = REG.filter(model="veo-3.1")
    assert veo.price_for(context=10**9).get("output_per_second") == 0.15


# HTTP transport details


def test_bearer_token_is_attached_and_absent_when_none(monkeypatch):
    from rates import _http

    captured = []

    class FakeResponse:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        captured.append(dict(request.header_items()))
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _http.fetch_json("https://example.test/a", token="tok123")
    _http.fetch_json("https://example.test/b")
    assert captured[0].get("Authorization") == "Bearer tok123"
    assert "Authorization" not in captured[1]
    assert all("rates" in h.get("User-agent", "") for h in captured)


# load() internals


def test_schema_compatibility_is_major_version_only():
    from rates.ai._load import _schema_compatible

    assert _schema_compatible("1.99.7") is True
    assert _schema_compatible("2.0.0") is False
    assert _schema_compatible(None) is False
    assert _schema_compatible("garbage") is False


def test_missing_snapshot_date_never_warns():
    import warnings

    from rates.ai._load import _warn_if_stale

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _warn_if_stale(None)


# Fusion with hostile payloads


def test_fuse_of_nothing_is_an_empty_registry_with_unreachable_sources():
    from rates.ai._fusion import fuse

    fused = fuse({})
    assert fused["models"] == []
    assert all(s["status"] == "unreachable" for s in fused["sources"])
    assert all(s["fetched_at"] is None for s in fused["sources"])
    assert Registry.from_dict(fused).models == ()


def test_litellm_entries_without_a_provider_are_skipped():
    from rates.ai._sources import normalize_litellm

    records = normalize_litellm(
        {"spec-note": "a string, not a model", "orphan": {"mode": "chat"}}
    )
    assert records == {}


def test_models_dev_provider_without_models_contributes_nothing():
    from rates.ai._sources import normalize_models_dev

    assert normalize_models_dev({"empty-provider": {"id": "x"}}) == {}


def test_admission_agreement_at_zero_rates_is_not_a_division_crash():
    from rates.ai._fusion import _admit_fallback_only

    normalized = {
        "models_dev": {},
        "genai_prices": {
            ("f", "m"): {
                "provider": "f", "id": "m",
                "price": {"input_mtok": 0, "output_mtok": 0},
                "price_tiers": [], "context": {"input": None, "output": None},
            }
        },
        "litellm": {
            ("f", "m"): {
                "provider": "f", "id": "m", "type": "chat",
                "price": {"input_mtok": 0, "output_mtok": 0},
                "deprecation_date": None,
            }
        },
        "openrouter": {},
    }
    admitted = _admit_fallback_only(normalized, preferred_keys=set(), today="t")
    assert len(admitted) == 1  # zero equals zero: agreement, no crash


def test_admission_requires_the_units_the_type_bills_on():
    from rates.ai._fusion import _admit_fallback_only

    normalized = {
        "models_dev": {},
        "genai_prices": {
            ("f", "v"): {
                "provider": "f", "id": "v",
                "price": {"input_mtok": 1},  # a video model with no per-second rate
                "price_tiers": [], "context": {"input": None, "output": None},
            }
        },
        "litellm": {
            ("f", "v"): {
                "provider": "f", "id": "v", "type": "video_generation",
                "price": {"input_mtok": 1}, "deprecation_date": None,
            }
        },
        "openrouter": {},
    }
    assert _admit_fallback_only(normalized, preferred_keys=set(), today="t") == []


# CLI edges


def run(capsys, *argv):
    code = _cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def cli_fixture(monkeypatch):
    monkeypatch.setattr(
        _cli, "_loader",
        lambda universe: lambda sync=False, live=False, timeout=None: REG,
    )


def test_bare_rates_greets_instead_of_erroring(capsys):
    from rates import __version__

    assert _cli.main([]) == 0
    out = capsys.readouterr().out
    assert __version__ in out
    assert "rates ai list" in out  # commands to kick the tyres
    assert "https://github.com/shehuphd/rates" in out
    assert "--help" in out


def test_bare_universe_introduces_it_with_examples(capsys):
    assert _cli.main(["ai"]) == 0
    out = capsys.readouterr().out
    assert "capabilities, and lifecycle" in out
    assert "rates ai search" in out
    assert "rates ai info" in out


def test_cli_timeout_past_the_ceiling_is_a_clean_error(capsys):
    from rates.ai import load as real_load

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            _cli, "_loader",
            lambda universe: lambda sync=False, live=False, timeout=None: real_load(
                sync=sync, live=live, timeout=timeout
            ),
        )
        code = _cli.main(["ai", "list", "--live", "--timeout", "9999"])
    err = capsys.readouterr().err
    assert code == 2
    assert "300" in err and "Traceback" not in err


def test_empty_search_phrase_matches_everything(capsys, cli_fixture):
    code, out, _ = run(capsys, "ai", "search", "")
    assert code == 0
    assert "claude-opus-5" in out and "veo-3.1" in out


def test_info_passes_the_tier_flags_through(capsys, monkeypatch):
    received = []

    def loader(universe):
        def load(sync=False, live=False, timeout=None):
            received.append((sync, live, timeout))
            return REG

        return load

    monkeypatch.setattr(_cli, "_loader", loader)
    code, _, _ = run(capsys, "ai", "info", "--live", "--timeout", "120")
    assert code == 0
    assert received == [(False, True, 120.0)]


def test_python_dash_m_rates_runs_the_same_cli():
    result = subprocess.run(
        [sys.executable, "-m", "rates", "--version"],
        capture_output=True, text=True, check=False,
        cwd="/Users/mo/Dropbox/Dev/rates",
    )
    assert result.returncode == 0
    assert "rates" in result.stdout


# Completion protocol edges


def test_completion_words_with_unbalanced_quotes_degrades_to_plain_split(monkeypatch):
    monkeypatch.setenv("COMP_LINE", 'rates ai search "unclosed')
    monkeypatch.setenv("COMP_POINT", str(len('rates ai search "unclosed')))
    assert _cli._completion_words([]) == ["ai", "search", '"unclosed']


def test_completion_point_mid_line_ignores_the_tail(monkeypatch):
    line = "rates ai filter --provider anth --limit 5"
    monkeypatch.setenv("COMP_LINE", line)
    monkeypatch.setenv("COMP_POINT", str(line.index(" --limit")))
    assert _cli.complete([]) == ["anthropic"]


def test_corrupt_candidate_cache_rebuilds(isolated_rates_cache):
    first = _cli._candidates("ai")
    cache_files = list(isolated_rates_cache.glob("complete-*.json"))
    assert len(cache_files) == 1
    cache_files[0].write_text("{corrupt")
    rebuilt = _cli._candidates("ai")
    assert rebuilt == first


# Table rendering internals


def test_cell_truncates_with_an_ellipsis():
    assert _cli._cell("abcdefghij", 5) == "abcd…"
    assert _cli._cell("ab", 5) == "ab   "


def test_fit_terminal_never_touches_piped_output(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert _cli._fit_terminal([500, 500]) == [500, 500]


def test_fit_terminal_shrinks_the_widest_column_on_a_tty(monkeypatch):
    import shutil

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda: type("S", (), {"columns": 40})()
    )
    widths = _cli._fit_terminal([10, 60])
    assert sum(widths) + 2 <= 40
    assert widths[0] == 10  # the narrow column is untouched


# traceact configuration behavior (traceact is installed in the dev venv)


@pytest.fixture
def clean_traceact():
    import traceact
    from traceact.config import get_package_sinks

    # The suite-wide sink (conftest) must survive this test's resets, or
    # every later test inherits traceact's console fallback.
    prior = list(get_package_sinks())
    traceact.reset_config()
    yield
    traceact.reset_config()
    if prior:
        traceact.configure(sinks=prior)


def test_cli_tracing_respects_an_apps_own_sinks(clean_traceact, monkeypatch, tmp_path):
    from pathlib import Path

    import traceact
    from traceact.config import get_package_sinks

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    own_sink = traceact.ConsoleSink()
    traceact.configure(sinks=[own_sink])
    from rates._trace import configure_cli_tracing

    configure_cli_tracing()
    assert get_package_sinks() == [own_sink]


def test_cli_tracing_sets_a_quiet_file_sink_when_unconfigured(
    clean_traceact, monkeypatch, tmp_path
):
    from pathlib import Path

    import traceact
    from traceact.config import get_package_sinks

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from rates._trace import configure_cli_tracing

    configure_cli_tracing()
    sinks = get_package_sinks()
    assert len(sinks) == 1
    assert isinstance(sinks[0], traceact.JsonlSink)
    assert (tmp_path / ".traceact").is_dir()


# The ledger build script


def test_build_ledger_writes_both_artifacts(tmp_path, monkeypatch, capsys):
    import gzip
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location(
        "build_ledger", "/Users/mo/Dropbox/Dev/rates/scripts/build_ledger.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    (tmp_path / "src" / "rates" / "ai").mkdir(parents=True)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        module, "fetch_sources",
        lambda: ({"models_dev": {}}, {"models_dev": "ok", "litellm": "unreachable"}),
    )
    monkeypatch.setattr(
        module, "gather_source_freshness", lambda statuses, timeout=None: {}
    )
    monkeypatch.setattr(module, "record_freshness_lookup", lambda timeout=None: None)
    monkeypatch.setattr(
        module, "fuse",
        lambda payloads, statuses, **kwargs: {"snapshot_date": "2026-08-23", "models": []},
    )
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "litellm" in out  # the unreachable source is named, not hidden

    plain = json.loads((tmp_path / "ledger-ai.json").read_bytes())
    packed = json.loads(
        gzip.decompress(
            (tmp_path / "src" / "rates" / "ai" / "ledger-ai.json.gz").read_bytes()
        )
    )
    assert plain == packed
