"""Tests for the CLI: dispatch, presets over filter, rendering, error
paths, and tab completion, all against a fixture registry."""

import pytest

from rates import _cli
from rates.ai import Registry

FIXTURE = {
    "schema_version": "1.0.0",
    "universe": "ai",
    "snapshot_date": "2026-08-22",
    "sources": [],
    "models": [
        {
            "provider": "anthropic",
            "id": "claude-opus-5",
            "family": "claude-opus",
            "type": "chat",
            "price": {"currency": "USD", "input_mtok": 5, "output_mtok": 25},
            "price_tiers": [
                {"when": {"dimension": "context", "over": 200000},
                 "price": {"input_mtok": 10}}
            ],
            "reasoning": {
                "control": "effort",
                "effort_parameter_required": False,
                "can_disable_reasoning": False,
                "levels": [{"label": "low", "rank": 1}, {"label": "high", "rank": 2}],
                "range": [1, 2],
                "default": "high",
            },
            "tool_call": True,
            "lifecycle": {"status": "active"},
            "sources": {"models_dev": "2026-08-22"},
        },
        {
            "provider": "anthropic",
            "id": "claude-haiku-4-5",
            "family": "claude-haiku",
            "type": "chat",
            "price": {"currency": "USD", "input_mtok": 1, "output_mtok": 5},
            "lifecycle": {"status": "active"},
        },
        {
            "provider": "openrouter",
            "id": "deepseek/deepseek-chat-v3.1",
            "type": "chat",
            "price": {"currency": "USD", "input_mtok": 0.55},
            "price_discrepancies": [
                {"field": "input_mtok", "chosen_source": "models_dev",
                 "chosen_value": 0.55, "other_source": "genai_prices",
                 "other_value": 0.21, "resolved_by": "preference",
                 "difference_pct": 61.8}
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


@pytest.fixture(autouse=True)
def fixture_loader(monkeypatch):
    registry = Registry.from_dict(FIXTURE)

    def loader(universe):
        assert universe in _cli.UNIVERSES
        return lambda sync=False, live=False, timeout=None: registry

    monkeypatch.setattr(_cli, "_loader", loader)

    def candidates(universe):
        return {
            "providers": sorted({m.provider for m in registry}),
            "models": sorted({m.id for m in registry}),
            "identities": sorted(f"{m.provider}/{m.id}" for m in registry),
            "types": sorted({m.type for m in registry if m.type}),
            "statuses": sorted(
                {m.lifecycle.status for m in registry if m.lifecycle.status}
            ),
            "currencies": ["USD"],
            "price_units": registry.price_units(),
        }

    monkeypatch.setattr(_cli, "_candidates", candidates)


def run(capsys, *argv):
    code = _cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# Dispatch and presets


def test_list_is_filter_with_no_constraints(capsys):
    code, out, _ = run(capsys, "ai", "list")
    assert code == 0
    assert "claude-opus-5" in out and "veo-3.1" in out


def test_filter_narrows_by_flags(capsys):
    code, out, _ = run(capsys, "ai", "filter", "--provider", "anthropic")
    assert code == 0
    assert "claude-opus-5" in out and "veo-3.1" not in out


def test_contains_flag_maps_to_the_contains_criterion(capsys):
    _, out, _ = run(capsys, "ai", "filter", "--model-contains", "OPUS")
    assert "claude-opus-5" in out and "claude-haiku-4-5" not in out


def test_bool_flag(capsys):
    _, out, _ = run(capsys, "ai", "filter", "--tool-call", "true")
    assert "claude-opus-5" in out and "claude-haiku-4-5" not in out


def test_price_flags(capsys):
    _, out, _ = run(
        capsys, "ai", "filter", "--price-max", "2", "--price-unit", "input_mtok"
    )
    assert "claude-haiku-4-5" in out and "claude-opus-5" not in out


def test_search_unions_across_name_fields(capsys):
    _, out, _ = run(capsys, "ai", "search", "claude")
    assert "claude-opus-5" in out and "claude-haiku-4-5" in out
    assert out.count("claude-opus-5") == 1  # no duplicate rows from the union


def test_search_respects_a_pinned_field(capsys):
    # provider pinned by flag; the phrase must not loosen it
    _, out, _ = run(capsys, "ai", "search", "deepseek", "--provider", "anthropic")
    assert "(no matches)" in out


def test_unscoped_query_renders_core_columns_with_universe(capsys):
    _, out, _ = run(capsys, "filter", "--provider", "anthropic")
    assert "UNIVERSE" in out and "ai" in out
    assert "IN $/MTOK" not in out  # per-universe column stays out of core view
    assert "MODEL" not in out  # the identity column goes by its neutral name
    assert "ID" in out


def test_unscoped_identity_flag_is_id_not_model(capsys):
    # --id maps to each universe's own identity criterion (model, for ai)
    _, out, _ = run(capsys, "filter", "--id-contains", "opus")
    assert "claude-opus-5" in out
    with pytest.raises(SystemExit):
        _cli.main(["filter", "--model", "claude-opus-5"])


def test_unscoped_sort_accepts_id_not_model(capsys):
    code, _, _ = run(capsys, "filter", "--sort-by", "id", "--ascending")
    assert code == 0
    code, _, err = run(capsys, "filter", "--sort-by", "model", "--ascending")
    assert code == 2
    assert "core fields only" in err


# show


def test_show_renders_full_detail(capsys):
    code, out, _ = run(capsys, "ai", "show", "anthropic/claude-opus-5")
    assert code == 0
    assert "reasoning: effort" in out
    assert "levels: low, high" in out
    assert "past 200,000 context: input_mtok: 10" in out
    assert "input_mtok: 5" in out


def test_show_surfaces_price_discrepancies(capsys):
    _, out, _ = run(capsys, "ai", "show", "openrouter/deepseek/deepseek-chat-v3.1")
    assert "genai_prices reports input_mtok as 0.21" in out


def test_show_falls_back_to_bare_id_when_the_id_contains_a_slash(capsys):
    code, out, _ = run(capsys, "ai", "show", "deepseek/deepseek-chat-v3.1")
    assert code == 0
    assert "openrouter/deepseek/deepseek-chat-v3.1" in out


def test_show_miss_points_at_search(capsys):
    code, _, err = run(capsys, "ai", "show", "gpt-99")
    assert code == 1
    assert "rates ai search" in err


# Ordering and limits


def test_sort_requires_explicit_direction(capsys):
    code, _, err = run(capsys, "ai", "list", "--sort-by", "id")
    assert code == 2
    assert "direction" in err and "Traceback" not in err


def test_unknown_sort_field_is_an_error_not_a_traceback(capsys):
    code, _, err = run(capsys, "ai", "list", "--sort-by", "colour", "--ascending")
    assert code == 2
    assert "can't sort by" in err and "price.<unit>" in err and "Traceback" not in err


def test_negative_limit_is_rejected():
    with pytest.raises(SystemExit):
        _cli.main(["ai", "list", "--limit", "-3"])


def test_flag_abbreviation_is_off():
    with pytest.raises(SystemExit):
        _cli.main(["ai", "list", "--lim", "5"])


def test_sort_descending(capsys):
    _, out, _ = run(
        capsys, "ai", "filter", "--provider", "anthropic",
        "--sort-by", "price.input_mtok", "--descending",
    )
    lines = [l for l in out.splitlines() if "claude" in l]
    assert "opus" in lines[0] and "haiku" in lines[1]


def test_limit_and_footer(capsys):
    _, out, _ = run(capsys, "ai", "list", "--limit", "2")
    assert "2 of 4 shown" in out


def test_limit_zero_shows_all(capsys):
    _, out, _ = run(capsys, "ai", "list", "--limit", "0")
    assert "shown" not in out


# Error paths


def test_price_bound_without_unit_is_an_actionable_error(capsys):
    code, _, err = run(capsys, "ai", "filter", "--price-max", "5")
    assert code == 2
    assert "price_unit" in err and "input_mtok" in err
    assert "Traceback" not in err


def test_sync_and_live_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        _cli.main(["ai", "list", "--sync", "--live"])


def test_unknown_flag_exits_via_argparse():
    with pytest.raises(SystemExit):
        _cli.main(["ai", "filter", "--colour", "blue"])


# Typo hints


def _typo_run(capsys, *argv):
    with pytest.raises(SystemExit) as excinfo:
        _cli.main(list(argv))
    return excinfo.value.code, capsys.readouterr().err


def test_verb_typo_suggests_the_verb(capsys):
    code, err = _typo_run(capsys, "ai", "searc")
    assert code == 2
    assert "Perhaps you meant `rates ai search`?" in err


def test_flag_typo_suggests_the_flag(capsys):
    code, err = _typo_run(capsys, "ai", "filter", "--provder", "anthropic")
    assert code == 2
    assert "Perhaps you meant `--provider`?" in err


def test_universe_typo_suggests_the_universe(capsys):
    code, err = _typo_run(capsys, "aii", "list")
    assert code == 2
    assert "Perhaps you meant `rates ai`?" in err


def test_hopeless_typo_gets_no_wild_guess(capsys):
    code, err = _typo_run(capsys, "ai", "filter", "--zzzqx", "5")
    assert code == 2
    assert "Perhaps" not in err


def test_contains_flag_typo_suggests_the_contains_spelling(capsys):
    code, err = _typo_run(capsys, "ai", "filter", "--model-contain", "opus")
    assert code == 2
    assert "Perhaps you meant `--model-contains`?" in err


def test_bare_word_typo_still_suggests_a_dashed_flag(capsys):
    code, err = _typo_run(capsys, "ai", "list", "help")
    assert code == 2
    assert "Perhaps you meant `--help`?" in err


# Welcome screen


def test_long_example_command_gets_its_own_line(capsys):
    code, out, _ = run(capsys, "ai")
    assert code == 0
    lines = out.splitlines()
    long_cmd = next(l for l in lines if "--price-unit input_mtok" in l)
    assert long_cmd.strip().startswith("rates ai filter")
    blurb_line = lines[lines.index(long_cmd) + 1]
    # Its own line, not sharing the long command's line, and not
    # indented out to the short commands' column (which would land
    # mid-flag on the line above it).
    assert "rates ai" not in blurb_line
    assert blurb_line.strip() == "narrow by any fields"
    assert not blurb_line.startswith(" " * 40)


# Cross-universe (unscoped) sorting


def test_unscoped_sort_by_core_field(capsys):
    code, out, _ = run(capsys, "filter", "--sort-by", "provider", "--descending")
    assert code == 0
    providers = [l.split()[1] for l in out.splitlines()[1:] if l and "shown" not in l]
    assert providers == sorted(providers, reverse=True)


def test_unscoped_sort_by_universe_field_errors_with_scoping_hint(capsys):
    code, _, err = run(
        capsys, "filter", "--sort-by", "price.input_mtok", "--ascending"
    )
    assert code == 2
    assert "core fields only" in err and "rates ai" in err


def test_unscoped_query_prints_the_core_fields_notice(capsys):
    _, _, err = run(capsys, "filter", "--provider", "anthropic")
    assert "scope to one" in err


def test_unscoped_sort_is_global_not_per_universe_blocks(capsys, monkeypatch):
    second = Registry.from_dict(
        {
            **FIXTURE,
            "models": [
                {
                    "provider": "bbbb",
                    "id": "mid-alphabet",
                    "price": {"currency": "USD"},
                    "lifecycle": {"status": "active"},
                }
            ],
        }
    )
    monkeypatch.setitem(_cli.UNIVERSES, "zz", _cli.UNIVERSES["ai"])
    first = Registry.from_dict(FIXTURE)

    def loader(universe):
        return lambda sync=False, live=False, timeout=None: (
            second if universe == "zz" else first
        )

    monkeypatch.setattr(_cli, "_loader", loader)
    code, out, _ = run(capsys, "filter", "--sort-by", "provider", "--ascending")
    assert code == 0
    providers = [l.split()[1] for l in out.splitlines()[1:] if l and "shown" not in l]
    # "bbbb" (from the zz universe) must interleave between anthropic and
    # google, not trail after them in a second block.
    assert providers == sorted(providers)
    assert "bbbb" in providers and providers.index("bbbb") > providers.index("anthropic")


def test_scoped_sort_accepts_model_as_the_id_alias(capsys):
    code, out, _ = run(capsys, "ai", "filter", "--provider", "anthropic",
                       "--sort-by", "model", "--descending")
    assert code == 0
    lines = [l for l in out.splitlines() if "claude" in l]
    assert "opus" in lines[0] and "haiku" in lines[1]


# JSON, info, and other output modes


def test_json_emits_ledger_shaped_records(capsys):
    import json

    _, out, _ = run(capsys, "ai", "filter", "--model", "claude-opus-5", "--json")
    (record,) = json.loads(out)
    assert record["provider"] == "anthropic"
    assert record["price"]["input_mtok"] == 5  # flat, ERD shape
    assert record["reasoning"]["control"] == "effort"
    assert record["price_tiers"][0]["when"] == {"dimension": "context", "over": 200000}


def test_json_show(capsys):
    import json

    _, out, _ = run(capsys, "ai", "show", "anthropic/claude-opus-5", "--json")
    (record,) = json.loads(out)
    assert record["id"] == "claude-opus-5"


def test_json_unscoped_carries_the_universe(capsys):
    import json

    _, out, _ = run(capsys, "filter", "--provider", "anthropic", "--json")
    records = json.loads(out)
    assert all(r["universe"] == "ai" for r in records)


def test_info_reports_snapshot_sources_and_counts(capsys):
    code, out, _ = run(capsys, "ai", "info")
    assert code == 0
    assert "snapshot: 2026-08-22" in out
    assert "models: 4" in out
    assert "aren't listed" in out  # coverage disclaimer
    assert "preferred" not in out and "fallback" not in out


def test_info_json_carries_the_coverage_note(capsys):
    import json

    _, out, _ = run(capsys, "ai", "info", "--json")
    (envelope,) = json.loads(out)
    assert "aren't listed" in envelope["coverage_note"]


def test_info_json(capsys):
    import json

    _, out, _ = run(capsys, "info", "--json")
    (envelope,) = json.loads(out)
    assert envelope["universe"] == "ai"
    assert envelope["models"] == 4
    assert envelope["schema_version"] == "1.0.0"


def test_no_header_omits_the_header_row(capsys):
    _, out, _ = run(capsys, "ai", "list", "--no-header")
    assert "PROVIDER" not in out and "claude-opus-5" in out


def test_version_flag(capsys):
    from rates import __version__

    with pytest.raises(SystemExit):
        _cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


# Completion


def test_complete_empty_offers_universes_and_verbs():
    candidates = _cli.complete(["--", ""])
    assert "ai" in candidates and "filter" in candidates and "completion" in candidates


def test_complete_after_universe_offers_verbs():
    candidates = _cli.complete(["--", "ai", ""])
    assert "filter" in candidates and "ai" not in candidates


def test_complete_partial_flag():
    candidates = _cli.complete(["--", "ai", "filter", "--pro"])
    assert candidates == ["--provider", "--provider-contains"]


def test_complete_provider_values_come_from_the_data():
    candidates = _cli.complete(["--", "ai", "filter", "--provider", ""])
    assert "anthropic" in candidates and "google" in candidates


def test_complete_show_offers_model_identities():
    candidates = _cli.complete(["--", "ai", "show", "anthropic/claude-o"])
    assert candidates == ["anthropic/claude-opus-5"]


def test_complete_after_the_show_id_offers_only_flags_show_accepts():
    candidates = _cli.complete(["--", "ai", "show", "anthropic/claude-opus-5", "--li"])
    assert candidates == ["--live"]
    candidates = _cli.complete(["--", "ai", "show", "anthropic/claude-opus-5", "--"])
    assert candidates == ["--json", "--live", "--sync", "--timeout"]


def test_complete_price_unit_values():
    candidates = _cli.complete(["--", "ai", "filter", "--price-unit", "out"])
    assert "output_mtok" in candidates and "output_per_second" in candidates


def test_complete_reads_the_bash_comp_line_protocol(monkeypatch):
    monkeypatch.setenv("COMP_LINE", "rates ai filter --provider anth")
    monkeypatch.setenv("COMP_POINT", str(len("rates ai filter --provider anth")))
    assert _cli.complete([]) == ["anthropic"]


def test_completion_scripts_register_the_hidden_command(capsys):
    for shell in ("bash", "zsh", "fish", "powershell"):
        code, out, _ = run(capsys, "completion", shell)
        assert code == 0
        assert "__complete" in out


def test_completion_unknown_shell_lists_the_known_ones(capsys):
    code, _, err = run(capsys, "completion", "tcsh")
    assert code == 2
    assert "bash" in err and "zsh" in err and "powershell" in err


# Flags that don't apply, and warning rendering


def test_show_rejects_the_query_flags(capsys):
    with pytest.raises(SystemExit):
        _cli.main(["ai", "show", "anthropic/claude-opus-5", "--limit", "5"])
    err = capsys.readouterr().err
    # A flag other verbs accept isn't a typo; the hint says it doesn't
    # apply here rather than suggesting the same flag back.
    assert "--limit doesn't apply to this command" in err
    assert "Perhaps you meant" not in err
    with pytest.raises(SystemExit):
        _cli.main(["ai", "show", "anthropic/claude-opus-5", "--sort-by", "id"])
    with pytest.raises(SystemExit):
        _cli.main(["ai", "show", "anthropic/claude-opus-5", "--no-header"])


def test_bare_timeout_without_a_network_tier_is_an_error(capsys):
    code, _, err = run(capsys, "ai", "list", "--timeout", "30")
    assert code == 2
    assert "--sync or --live" in err and "Traceback" not in err


def _warning_loader(monkeypatch, message, category):
    import warnings

    registry = Registry.from_dict(FIXTURE)

    def loader(universe):
        def load_fn(sync=False, live=False, timeout=None):
            warnings.warn(message, category, stacklevel=2)
            return registry

        return load_fn

    monkeypatch.setattr(_cli, "_loader", loader)


def test_rates_warnings_render_as_one_clean_stderr_line(capsys, monkeypatch):
    import warnings

    from rates._errors import StaleLedgerWarning

    _warning_loader(monkeypatch, "the ledger is 99 days old; refresh it",
                    StaleLedgerWarning)
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        code, out, err = run(capsys, "ai", "list")
    assert code == 0
    assert "warning: the ledger is 99 days old; refresh it" in err
    assert "StaleLedgerWarning" not in err and ".py" not in err
    assert "claude-opus-5" in out  # the result still renders


def test_foreign_warnings_pass_through_untouched(capsys, monkeypatch):
    # Warnings from other libraries keep the warnings module's own
    # rendering; only rates' warnings get the one-line CLI format.
    _warning_loader(monkeypatch, "somebody else's warning", UserWarning)
    with pytest.warns(UserWarning, match="somebody else's warning"):
        code, _, err = run(capsys, "ai", "list")
    assert code == 0
    assert "warning: somebody else's warning" not in err


def test_info_sources_collapse_to_one_line_without_roles(capsys):
    healthy = Registry.from_dict({
        "schema_version": "1.0.0", "universe": "ai", "snapshot_date": "2026-08-22",
        "sources": [
            {"name": "a", "role": "preferred", "status": "ok", "fetched_at": "2026-08-22"},
            {"name": "b", "role": "fallback", "status": "ok", "fetched_at": "2026-08-22"},
        ],
        "models": [],
    })
    _cli._render_info({"ai": healthy}, as_json=False)
    out = capsys.readouterr().out
    assert "sources: ok (checked 2026-08-22)" in out
    assert "preferred" not in out and "fallback" not in out and "role" not in out


def test_info_sources_count_failures_without_naming_them(capsys):
    degraded = Registry.from_dict({
        "schema_version": "1.0.0", "universe": "ai", "snapshot_date": "2026-08-22",
        "sources": [
            {"name": "a", "role": "preferred", "status": "ok", "fetched_at": "2026-08-22"},
            {"name": "b", "role": "fallback", "status": "unreachable"},
            {"name": "c", "role": "validation", "status": "unreachable"},
        ],
        "models": [],
    })
    _cli._render_info({"ai": degraded}, as_json=False)
    out = capsys.readouterr().out
    assert "sources: checked 2026-08-22; two sources inaccessible" in out
    assert "b" != out and "unreachable" not in out
