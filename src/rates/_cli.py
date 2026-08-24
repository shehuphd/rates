"""The rates CLI: one filter primitive, presets over it, universes as
positional scope.

Flags mirror the Python API one-to-one (``--model-contains opus`` is
``.filter(model_contains="opus")``), so the two surfaces share a single
vocabulary. ``list``, ``search``, and ``show`` are presets over
``filter``, kept because they match how people think, not because they do
anything ``filter`` can't. See ARCHITECTURE.md § CLI shape.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import sys
from collections.abc import Callable, Iterator, Sequence
from typing import Any, NoReturn

from ._errors import RatesError
from ._trace import configure_cli_tracing, traced

# Per-universe spec, all data-driven: adding a universe means adding an
# entry here, not new parsing code. `filter_flags` maps CLI flag names to
# filter() keyword names; `core` marks the flags usable in an unscoped
# (cross-universe) query, per the core-vs-per-universe field split.
UNIVERSES: dict[str, dict[str, Any]] = {
    "ai": {
        "tagline": "AI model pricing, capabilities, and lifecycle",
        "description": (
            "What every model costs, what it accepts and produces, and "
            "whether it's still viable to build on, fused from four "
            "sources and cross-validated."
        ),
        "examples": [
            ("rates ai list", "the catalog, twenty rows at a time"),
            ('rates ai search "opus"', "find a model without knowing its exact id"),
            ("rates ai show anthropic/claude-opus-5", "everything about one model"),
            (
                (
                    "rates ai filter --provider anthropic --price-max 5 "
                    "--price-unit input_mtok"
                ),
                "narrow by any fields",
            ),
            ("rates ai info", "how fresh the data is, and where it came from"),
        ],
        # The core layer's neutral name for the identity field is "id";
        # each universe names its own criterion for it ("model" here —
        # quantum has no models, cloud sells services, so the AI word
        # never appears in unscoped vocabulary).
        "identity_kw": "model",
        "string_flags": {
            "model": {"core": False},
            "provider": {"core": True},
            "family": {"core": False},
            "type": {"core": False},
        },
        "value_flags": {
            "currency": {"core": True},
            "status": {"core": True},
            "modality-input": {"kw": "modality_input", "core": False},
            "modality-output": {"kw": "modality_output", "core": False},
            "price-unit": {"kw": "price_unit", "core": True},
        },
        "bool_flags": {
            "tool-call": {"kw": "tool_call"},
            "structured-output": {"kw": "structured_output"},
        },
        "number_flags": {
            "price-min": {"kw": "price_min", "core": True},
            "price-max": {"kw": "price_max", "core": True},
        },
        "search_fields": ["model_contains", "provider_contains", "family_contains"],
        "columns": [
            ("PROVIDER", lambda m: m.provider),
            ("MODEL", lambda m: m.id),
            ("TYPE", lambda m: m.type or ""),
            ("IN $/MTOK", lambda m: _fmt_rate(m.price.get("input_mtok"))),
            ("OUT $/MTOK", lambda m: _fmt_rate(m.price.get("output_mtok"))),
            ("STATUS", lambda m: m.lifecycle.status or ""),
        ],
    },
}

CORE_COLUMNS = [
    ("UNIVERSE", lambda u, m: u),
    ("PROVIDER", lambda u, m: m.provider),
    ("ID", lambda u, m: m.id),
    ("CURRENCY", lambda u, m: m.price.currency or ""),
    ("STATUS", lambda u, m: m.lifecycle.status or ""),
]

VERBS = ("list", "filter", "search", "show", "info")
DEFAULT_LIMIT = 20


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError(
            f"--limit takes 0 (show all) or a positive count, got {parsed}"
        )
    return parsed


def _loader(universe: str) -> Callable[..., Any]:
    if universe == "ai":
        from rates.ai import load

        return load
    raise KeyError(universe)


def _fmt_rate(rate: float | None) -> str:
    if rate is None:
        return ""
    return f"{rate:g}"


# Parser construction, generated from the universe spec


class _Parser(argparse.ArgumentParser):
    """argparse with a typo hint: the first unrecognized verb or flag gets
    fuzzy-matched (stdlib difflib) against what's valid in this scope, and
    one "Perhaps you meant ...?" line rides along with the error. Nothing
    is suggested when nothing is plausibly close."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.known_flags: list[str] = []

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        lines = [f"{self.prog}: error: {message}"]
        hint = _typo_hint(message, self.prog, self.known_flags)
        if hint:
            lines.append(hint)
        self.exit(2, "\n".join(lines) + "\n")


def _typo_hint(message: str, prog: str, known_flags: list[str]) -> str | None:
    import difflib
    import re

    choice = re.search(r"invalid choice: '([^']+)' \(choose from (.+)\)", message)
    if choice:
        candidates = re.findall(r"'([^']+)'", choice.group(2))
        if prog == "rates":
            candidates += list(UNIVERSES)
        close = difflib.get_close_matches(choice.group(1), candidates, n=1, cutoff=0.5)
        return f"Perhaps you meant `{prog} {close[0]}`?" if close else None

    unrecognized = re.search(r"unrecognized arguments: (\S+)", message)
    if unrecognized:
        flag = unrecognized.group(1)
        candidates = known_flags + ["--help"]
        if flag in candidates:
            # Not a typo: a flag other verbs accept, aimed at one that
            # doesn't (show takes none of the query flags). Suggesting
            # the flag back at the user would read as a taunt.
            return f"{flag} doesn't apply to this command; --help lists what does."
        # Matched with leading dashes stripped, so a bare word ("help")
        # still finds its dashed flag ("--help").
        by_stem = {c.lstrip("-"): c for c in candidates}
        close = difflib.get_close_matches(flag.lstrip("-"), by_stem, n=1, cutoff=0.5)
        return f"Perhaps you meant `{by_stem[close[0]]}`?" if close else None
    return None


def _add_common_flags(parser: argparse.ArgumentParser, query: bool = True) -> None:
    if query:
        parser.add_argument("--limit", type=_nonnegative_int, default=DEFAULT_LIMIT,
                            help=f"rows to show (default {DEFAULT_LIMIT}; 0 shows all)")
        parser.add_argument("--sort-by", metavar="FIELD",
                            help='sort field, e.g. "id" or "price.input_mtok"')
        direction = parser.add_mutually_exclusive_group()
        direction.add_argument("--ascending", action="store_true")
        direction.add_argument("--descending", action="store_true")
        parser.add_argument("--no-header", action="store_true",
                            help="omit the header row (for awk/cut pipelines)")
    parser.add_argument("--json", action="store_true",
                        help="emit records as JSON in the ledger's own shape")
    tier = parser.add_mutually_exclusive_group()
    tier.add_argument("--sync", action="store_true",
                      help="check for our newer published ledger first")
    tier.add_argument("--live", action="store_true",
                      help="fuse the raw sources directly instead of the ledger")
    parser.add_argument("--timeout", type=float, metavar="SECONDS",
                        help="network timeout for --sync/--live (up to 300)")


def _add_filter_flags(
    parser: argparse.ArgumentParser, spec: dict[str, Any], core_only: bool
) -> None:
    if core_only:
        # The neutral identity flag; each universe maps it to its own
        # criterion via identity_kw.
        parser.add_argument("--id", metavar="VALUE",
                            help="exact identifier (case-insensitive)")
        parser.add_argument("--id-contains", metavar="TEXT",
                            help="substring match on the identifier")
    for name, opts in spec["string_flags"].items():
        if core_only and not opts["core"]:
            continue
        parser.add_argument(f"--{name}", metavar="VALUE",
                            help=f"exact {name} (case-insensitive)")
        parser.add_argument(f"--{name}-contains", metavar="TEXT",
                            help=f"substring match on {name}")
    for name, opts in spec["value_flags"].items():
        if core_only and not opts.get("core"):
            continue
        parser.add_argument(f"--{name}", metavar="VALUE")
    if not core_only:
        for name, opts in spec["bool_flags"].items():
            parser.add_argument(f"--{name}", choices=["true", "false"])
    for name, opts in spec["number_flags"].items():
        if core_only and not opts.get("core"):
            continue
        parser.add_argument(f"--{name}", type=float, metavar="N")


def _build_parser(universe: str | None) -> argparse.ArgumentParser:
    prog = "rates" if universe is None else f"rates {universe}"
    spec = UNIVERSES[universe] if universe else UNIVERSES["ai"]
    core_only = universe is None

    from rates import __version__

    parser = _Parser(
        prog=prog,
        description="Rates pricing registry.",
        allow_abbrev=False,
    )
    parser.known_flags = _flag_names(spec, core_only)
    parser.add_argument("--version", action="version", version=f"rates {__version__}")
    subparsers = parser.add_subparsers(dest="verb", required=True, parser_class=_Parser)

    for verb in ("list", "filter"):
        sub = subparsers.add_parser(
            verb,
            help="show models" if verb == "list" else "narrow by any schema fields",
            allow_abbrev=False,
        )
        _add_filter_flags(sub, spec, core_only)
        _add_common_flags(sub)

    search = subparsers.add_parser(
        "search", help="substring match across name fields", allow_abbrev=False
    )
    search.add_argument("phrase")
    _add_filter_flags(search, spec, core_only)
    _add_common_flags(search)

    show = subparsers.add_parser(
        "show", help="everything about one model", allow_abbrev=False
    )
    show.add_argument("id", metavar="[PROVIDER/]MODEL")
    # A point lookup has no rows to limit, sort, or strip headers from;
    # accepting those flags and ignoring them would be a silent no-op.
    _add_common_flags(show, query=False)

    info = subparsers.add_parser(
        "info",
        help="the registry itself: snapshot date, sources, freshness",
        allow_abbrev=False,
    )
    _add_common_flags(info, query=False)

    return parser


def _criteria_from_args(
    args: argparse.Namespace, spec: dict[str, Any], core_only: bool
) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    if core_only:
        identity = spec["identity_kw"]
        if getattr(args, "id", None) is not None:
            criteria[identity] = args.id
        if getattr(args, "id_contains", None) is not None:
            criteria[f"{identity}_contains"] = args.id_contains
    for name, opts in spec["string_flags"].items():
        if core_only and not opts["core"]:
            continue
        attr = name.replace("-", "_")
        if getattr(args, attr, None) is not None:
            criteria[attr] = getattr(args, attr)
        if getattr(args, f"{attr}_contains", None) is not None:
            criteria[f"{attr}_contains"] = getattr(args, f"{attr}_contains")
    for group in ("value_flags", "number_flags"):
        for name, opts in spec[group].items():
            if core_only and not opts.get("core"):
                continue
            attr = name.replace("-", "_")
            kw = opts.get("kw", attr)
            if getattr(args, attr, None) is not None:
                criteria[kw] = getattr(args, attr)
    if not core_only:
        for name, opts in spec["bool_flags"].items():
            attr = name.replace("-", "_")
            value = getattr(args, attr, None)
            if value is not None:
                criteria[opts["kw"]] = value == "true"
    return criteria


# Execution


def _run_query(universe: str, args: argparse.Namespace) -> int:
    spec = UNIVERSES[universe]
    registry = _loader(universe)(
        sync=args.sync, live=args.live, timeout=args.timeout
    )

    if args.verb == "info":
        return _render_info({universe: registry}, args.json)

    if args.verb == "show":
        return _render_show(registry, args.id, as_json=args.json)

    criteria = _criteria_from_args(args, spec, core_only=False)
    if args.verb == "search":
        result = _search(registry, spec, args.phrase, criteria)
    else:
        result = registry.filter(**criteria)

    result = _apply_sort(result, args)
    rows = list(result)
    if args.json:
        _print_json([m.to_dict() for m in _limited(rows, args.limit)])
        return 0
    columns = [(header, _bind(getter)) for header, getter in spec["columns"]]
    _render_table(columns, rows, args.limit, no_header=args.no_header)
    return 0


def _bind(getter: Callable[[Any], str]) -> Callable[[Any], str]:
    """A per-iteration closure, so comprehension-built columns don't all
    share the loop's last getter."""
    return lambda m: getter(m)


def _bind_pair(getter: Callable[[Any, Any], str]) -> Callable[[Any], str]:
    return lambda pair: getter(pair[0], pair[1])


def _limited(rows: list[Any], limit: int) -> list[Any]:
    return rows if limit == 0 else rows[:limit]


def _print_json(payload: Any) -> None:
    import json

    print(json.dumps(payload, indent=2))


def _run_unscoped(args: argparse.Namespace) -> int:
    if args.verb == "info":
        registries = {
            u: _loader(u)(sync=args.sync, live=args.live, timeout=args.timeout)
            for u in UNIVERSES
        }
        return _render_info(registries, args.json)

    if args.verb == "show":
        # A show is a point lookup; run it against every universe that
        # knows the id.
        for universe in UNIVERSES:
            registry = _loader(universe)(
                sync=args.sync, live=args.live, timeout=args.timeout
            )
            code = _render_show(registry, args.id, missing_ok=True, as_json=args.json)
            if code == 0:
                return 0
        print(f"no model matching {args.id!r} in any universe", file=sys.stderr)
        return 1

    rows: list[tuple[str, Any]] = []
    for universe, spec in UNIVERSES.items():
        registry = _loader(universe)(
            sync=args.sync, live=args.live, timeout=args.timeout
        )
        criteria = _criteria_from_args(args, spec, core_only=True)
        if args.verb == "search":
            result = _search(registry, spec, args.phrase, criteria)
        else:
            result = registry.filter(**criteria)
        rows.extend((universe, m) for m in result)

    rows = _apply_unscoped_sort(rows, args)
    print(
        f"core fields shown across universes; scope to one "
        f"(e.g. rates ai {args.verb} ...) for its full fields",
        file=sys.stderr,
    )

    if args.json:
        _print_json(
            [
                {"universe": u, **m.to_dict()}
                for u, m in _limited(rows, args.limit)
            ]
        )
        return 0
    columns = [(h, _bind_pair(getter)) for h, getter in CORE_COLUMNS]
    _render_table(columns, rows, args.limit, no_header=args.no_header)
    return 0


def _search(
    registry: Any, spec: dict[str, Any], phrase: str, criteria: dict[str, Any]
) -> Any:
    """The search preset: the phrase contains-matched across each name
    field in turn, results unioned in catalog order."""
    seen: set[tuple[str, str]] = set()
    matched = []
    for field in spec["search_fields"]:
        if field not in _search_fields_available(criteria):
            continue
        for m in registry.filter(**{**criteria, field: phrase}):
            key = (m.provider, m.id)
            if key not in seen:
                seen.add(key)
                matched.append(m)
    matched.sort(key=lambda m: (m.provider, m.id))
    from dataclasses import replace

    return replace(registry, models=tuple(matched))


def _search_fields_available(criteria: dict[str, Any]) -> set[str]:
    # A field already constrained by an explicit flag stays constrained;
    # search never overrides what the caller pinned.
    return {
        f
        for f in ("model_contains", "provider_contains", "family_contains")
        if f not in criteria and f.removesuffix("_contains") not in criteria
    }


def _apply_sort(result: Any, args: argparse.Namespace) -> Any:
    if not args.sort_by:
        return result
    _require_direction(args)
    # The CLI's caller-facing name for the id field is "model".
    field = "id" if args.sort_by == "model" else args.sort_by
    return result.sort_by(field, descending=args.descending)


def _require_direction(args: argparse.Namespace) -> None:
    if not (args.ascending or args.descending):
        raise ValueError(
            "--sort-by needs a direction: pass --ascending or "
            "--descending (no field has a default direction)"
        )


# What all universes share, so what an unscoped query can sort on. The
# identity field goes by its neutral name "id" here; "model" is one
# universe's word for it, not the core layer's.
_CORE_SORT_KEYS: dict[str, Callable[[tuple[str, Any]], Any]] = {
    "universe": lambda row: row[0],
    "provider": lambda row: row[1].provider,
    "id": lambda row: row[1].id,
    "currency": lambda row: row[1].price.currency or "",
    "status": lambda row: row[1].lifecycle.status or "",
}


def _apply_unscoped_sort(
    rows: list[tuple[str, Any]], args: argparse.Namespace
) -> list[tuple[str, Any]]:
    """One global sort over the pooled rows, never per-universe blocks
    concatenated: a $0.90 row from one universe must not rank below a
    $0.99 row from another just because of iteration order."""
    if not args.sort_by:
        return rows
    _require_direction(args)
    key = _CORE_SORT_KEYS.get(args.sort_by)
    if key is None:
        raise ValueError(
            f"unscoped queries sort by core fields only "
            f"({', '.join(sorted(_CORE_SORT_KEYS))}); scope to a universe "
            f"(rates ai {args.verb} ...) to sort by its own fields, "
            f"{args.sort_by!r} included if it has one"
        )
    return sorted(rows, key=key, reverse=args.descending)


# Rendering


def _render_table(
    columns: Sequence[tuple[str, Callable[[Any], str]]],
    rows: list[Any],
    limit: int,
    no_header: bool = False,
) -> None:
    shown = _limited(rows, limit)
    grid = [[str(getter(row)) for _, getter in columns] for row in shown]
    headers = [header for header, _ in columns]
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in grid)) if grid else len(headers[i])
        for i in range(len(headers))
    ]
    widths = _fit_terminal(widths)
    if not no_header:
        print("  ".join(_cell(h, w) for h, w in zip(headers, widths)).rstrip())
    for r in grid:
        print("  ".join(_cell(cell, w) for cell, w in zip(r, widths)).rstrip())
    if len(shown) < len(rows):
        print(f"\n{len(shown)} of {len(rows)} shown (--limit 0 shows all)")
    elif not rows:
        print("(no matches)")


def _cell(text: str, width: int) -> str:
    if len(text) > width:
        return text[: max(width - 1, 1)] + "…"
    return text.ljust(width)


def _fit_terminal(widths: list[int]) -> list[int]:
    """On a terminal, shrink the widest column until the table fits the
    window; piped output is never truncated."""
    if not sys.stdout.isatty():
        return widths
    import shutil

    available = shutil.get_terminal_size().columns
    widths = list(widths)
    while sum(widths) + 2 * (len(widths) - 1) > available:
        widest = widths.index(max(widths))
        if widths[widest] <= 8:
            break
        widths[widest] -= 1
    return widths


def _render_show(
    registry: Any,
    identity: str,
    missing_ok: bool = False,
    as_json: bool = False,
) -> int:
    matches: list[Any] = []
    if "/" in identity:
        provider, model_id = identity.split("/", 1)
        matches = list(registry.filter(provider=provider, model=model_id))
    if not matches:
        # Either the bare form, or a model id that itself contains a
        # slash (openrouter-style ids) and mislead the provider split.
        matches = list(registry.filter(model=identity))
    if not matches:
        if not missing_ok:
            print(
                f"no model matching {identity!r}; try rates ai search "
                f"{shlex.quote(identity)}",
                file=sys.stderr,
            )
        return 1

    if as_json:
        _print_json([m.to_dict() for m in matches])
        return 0

    for m in matches:
        print(f"{m.provider}/{m.id}")
        _kv("family", m.family)
        _kv("type", m.type)
        _kv("status", m.lifecycle.status)
        _kv("released", m.lifecycle.release_date)
        _kv("deprecated", m.lifecycle.deprecation_date)
        _kv("modalities in", ", ".join(m.modalities.input))
        _kv("modalities out", ", ".join(m.modalities.output))
        _kv("context in", m.context.input)
        _kv("context out", m.context.output)
        _kv("tool call", m.tool_call)
        _kv("structured out", m.structured_output)
        if m.price.units:
            print(f"  price ({m.price.currency}):")
            for unit, rate in sorted(m.price.units.items()):
                print(f"    {unit}: {_fmt_rate(rate)}")
        for tier in m.price_tiers:
            overrides = ", ".join(
                f"{u}: {_fmt_rate(r)}" for u, r in sorted(tier.price.items())
            )
            print(f"  past {tier.when_over:,} {tier.when_dimension}: {overrides}")
        if m.reasoning:
            r = m.reasoning
            _kv("reasoning", r.control or "yes")
            if r.levels:
                _kv("  levels", ", ".join(lv.label for lv in r.levels))
            if r.budget:
                _kv("  budget", _budget_text(*r.budget))
            _kv("  default", r.default)
            _kv("  parameter required", r.effort_parameter_required)
            _kv("  can disable", r.can_disable_reasoning)
        for d in m.price_discrepancies:
            print(
                f"  note: {d.other_source} reports {d.field} as "
                f"{_fmt_rate(d.other_value)} ({d.difference_pct:g}% off "
                f"the {_fmt_rate(d.chosen_value)} used here)"
            )
        _kv("sources", ", ".join(sorted(m.sources)))
        print()
    return 0


def _budget_text(low: int | None, high: int | None) -> str:
    if low is not None and high is not None:
        return f"{low:,}-{high:,} tokens"
    if low is not None:
        return f"at least {low:,} tokens"
    if high is not None:
        return f"up to {high:,} tokens"
    return "token budget"


def _kv(label: str, value: Any) -> None:
    if value is None or value == "":
        return
    print(f"  {label}: {value}")


def _render_info(registries: dict[str, Any], as_json: bool) -> int:
    from datetime import datetime, timezone

    from rates.ai._load import STALENESS_THRESHOLD_DAYS

    today = datetime.now(timezone.utc).date()
    if as_json:
        _print_json(
            [
                {
                    "universe": universe,
                    "schema_version": reg.schema_version,
                    "snapshot_date": (
                        reg.snapshot_date.isoformat() if reg.snapshot_date else None
                    ),
                    "age_days": (
                        (today - reg.snapshot_date).days if reg.snapshot_date else None
                    ),
                    "models": len(reg),
                    "providers": len({m.provider for m in reg}),
                    "type_known": sum(1 for m in reg if m.type),
                    "coverage_note": _COVERAGE_NOTE,
                    "sources": _source_summary(reg.sources),
                }
                for universe, reg in registries.items()
            ]
        )
        return 0

    for universe, reg in registries.items():
        print(f"universe: {universe}")
        _kv("schema version", reg.schema_version)
        if reg.snapshot_date:
            age = (today - reg.snapshot_date).days
            age_text = f"{age} day{'s' if age != 1 else ''} old"
            if age > STALENESS_THRESHOLD_DAYS:
                age_text += (
                    f", past the {STALENESS_THRESHOLD_DAYS}-day threshold; "
                    "refresh with --sync or --live"
                )
            _kv("snapshot", f"{reg.snapshot_date} ({age_text})")
        _kv("models", len(reg))
        _kv("providers", len({m.provider for m in reg}))
        typed = sum(1 for m in reg if m.type)
        _kv("type known", f"{typed} of {len(reg)} (untyped models never match --type)")
        _kv("sources", _source_summary(reg.sources))
        print(f"  Note: {_COVERAGE_NOTE}")
        print()
    return 0


def _source_summary(sources: Any) -> str | None:
    """One line on the sources as a whole; which source plays which role
    is the registry's business, not something a reader needs."""
    if not sources:
        return None
    reachable = [s for s in sources if s.status == "ok"]
    checked = max((s.fetched_at for s in reachable if s.fetched_at), default=None)
    failed = len(sources) - len(reachable)
    if failed == 0:
        return f"ok (checked {checked})" if checked else "ok"
    count = {1: "one", 2: "two", 3: "three", 4: "four"}.get(failed, str(failed))
    plural = "source" if failed == 1 else "sources"
    prefix = f"checked {checked}; " if checked else ""
    return f"{prefix}{count} {plural} inaccessible"


_COVERAGE_NOTE = (
    "Services with unpublished, inaccessible, or non-unit pricing "
    "(subscriptions, platform bundles) aren't listed."
)


def _print_welcome(universe: str | None) -> None:
    from rates import __version__

    if universe is None:
        print(f"Rates pricing registry v{__version__}\n")
        print("Worlds:")
        for name, spec in UNIVERSES.items():
            print(f"  {name}  {spec['tagline']}")
        examples = UNIVERSES["ai"]["examples"][:3]
    else:
        spec = UNIVERSES[universe]
        print(f"rates {universe}: {spec['tagline']}.")
        print(spec["description"])
        examples = spec["examples"]

    print("\nTry:")
    short = [cmd for cmd, _ in examples if len(cmd) <= 42]
    width = max(map(len, short)) if short else 0
    for cmd, blurb in examples:
        if len(cmd) <= 42:
            print(f"  {cmd.ljust(width)}  {blurb}")
        else:
            # Too long to share the short commands' column without
            # crowding it: the command gets its own line, the blurb a
            # plainly-indented one below rather than a false alignment.
            print(f"  {cmd}")
            print(f"      {blurb}")
    print(
        "\nUse --help on any command for its full options.\n"
        "More: https://github.com/shehuphd/rates"
    )


# Entry point


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    configure_cli_tracing()

    if argv and argv[0] == "__complete":
        for candidate in complete(argv[1:]):
            print(candidate)
        return 0
    if argv and argv[0] == "completion":
        return _print_completion_script(argv[1:])

    universe: str | None = None
    if argv and argv[0] in UNIVERSES:
        universe = argv.pop(0)

    if not argv:
        # No verb is a newcomer kicking the tyres, not a mistake: greet,
        # point somewhere useful, succeed.
        _print_welcome(universe)
        return 0

    parser = _build_parser(universe)
    args = parser.parse_args(argv)

    try:
        if args.timeout is not None and not (args.sync or args.live):
            raise ValueError(
                "--timeout has no effect without --sync or --live; the "
                "default tier reads the bundled ledger and makes no "
                "network requests"
            )
        with _clean_cli_warnings():
            return _run(universe, args)
    except (ValueError, TypeError) as exc:
        # The registry raises these for a bad criterion or sort field;
        # both are usage errors here, never tracebacks.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RatesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


@contextlib.contextmanager
def _clean_cli_warnings() -> Iterator[None]:
    """On the CLI, rates' own warnings render as one plain stderr line
    instead of the warnings module's file-and-line format; that format is
    right for the Python API, where the location helps, and noise here.
    Other libraries' warnings pass through untouched."""
    import warnings

    from ._errors import RatesWarning

    original = warnings.showwarning

    def show(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any = None,
        line: str | None = None,
    ) -> None:
        if issubclass(category, RatesWarning):
            print(f"warning: {message}", file=sys.stderr)
        else:
            original(message, category, filename, lineno, file, line)

    warnings.showwarning = show
    try:
        yield
    finally:
        warnings.showwarning = original


@traced("cli.run")
def _run(universe: str | None, args: argparse.Namespace) -> int:
    if universe is not None:
        return _run_query(universe, args)
    return _run_unscoped(args)


# Tab completion: the shell calls `rates __complete -- <words>` (or the
# bash -C protocol via COMP_LINE) and we print candidates, pulled from the
# data itself for value positions.

_VALUE_COMPLETERS = {
    "--provider": "providers",
    "--status": "statuses",
    "--type": "types",
    "--currency": "currencies",
    "--price-unit": "price_units",
    "--model": "models",
}


def _candidates(universe: str) -> dict[str, list[str]]:
    """Completion candidates from the data itself, kept fast enough for
    TAB: read from the raw bundled ledger (no dataclass construction) and
    cached to a temp file keyed by the bundled file's stat, so a package
    upgrade invalidates it and a repeat TAB costs a stat plus a small
    read."""
    import json
    from importlib import resources

    from rates.ai._load import cache_dir

    with resources.as_file(
        resources.files("rates.ai").joinpath("ledger-ai.json.gz")
    ) as bundled:
        stat = bundled.stat()
        key = f"{bundled}:{stat.st_size}:{stat.st_mtime_ns}"
        # The path is part of the cache filename too, so two environments
        # with different installs never fight over one cache file.
        import hashlib

        digest = hashlib.sha256(str(bundled).encode()).hexdigest()[:8]
    cache = cache_dir() / f"complete-{universe}-{digest}.json"

    try:
        with open(cache) as f:
            cached = json.load(f)
        if cached.get("key") == key:
            candidates: dict[str, list[str]] = cached["candidates"]
            return candidates
    except (OSError, ValueError, KeyError, TypeError):
        pass

    from rates.ai import _load

    data = _load._read_bundled()
    models = data.get("models", [])
    built = {
        "providers": sorted({m.get("provider", "") for m in models} - {""}),
        "models": sorted({m.get("id", "") for m in models} - {""}),
        "identities": sorted(
            f"{m['provider']}/{m['id']}"
            for m in models
            if m.get("provider") and m.get("id")
        ),
        "types": sorted({m.get("type") or "" for m in models} - {""}),
        "statuses": sorted(
            {(m.get("lifecycle") or {}).get("status") or "" for m in models} - {""}
        ),
        "currencies": sorted(
            {(m.get("price") or {}).get("currency") or "" for m in models} - {""}
        ),
        "price_units": sorted(
            {
                unit
                for m in models
                for unit in (m.get("price") or {})
                if unit != "currency"
            }
        ),
    }
    try:
        with open(cache, "w") as f:
            json.dump({"key": key, "candidates": built}, f)
    except OSError:
        pass
    return built


def complete(argv: list[str]) -> list[str]:
    words = _completion_words(argv)
    partial = words[-1] if words else ""
    before = words[:-1]

    universe = None
    if before and before[0] in UNIVERSES:
        universe = before[0]
        before = before[1:]

    if before and before[-1] in _VALUE_COMPLETERS:
        store = _candidates(universe or "ai")
        candidates = store[_VALUE_COMPLETERS[before[-1]]]
    elif not before:
        candidates = list(UNIVERSES) + list(VERBS) + ["completion"]
        if universe:
            candidates = list(VERBS)
    elif before[-1] == "show":
        # The id position itself; once it's filled, flags complete below.
        candidates = _candidates(universe or "ai")["identities"]
    elif before[0] == "show":
        # show renders one record whole, so the query flags don't apply
        # and don't complete; only the tier and output flags do.
        candidates = ["--json", "--live", "--sync", "--timeout"]
    elif before == ["completion"]:
        candidates = sorted(_COMPLETION_SCRIPTS)
    else:
        spec = UNIVERSES[universe or "ai"]
        candidates = _flag_names(spec, core_only=universe is None)

    lowered = partial.casefold()
    return [c for c in candidates if c.casefold().startswith(lowered)]


def _completion_words(argv: list[str]) -> list[str]:
    if argv and argv[0] == "--":
        return argv[1:]
    line = os.environ.get("COMP_LINE")
    if line is not None:
        point = int(os.environ.get("COMP_POINT", len(line)))
        line = line[:point]
        try:
            words = shlex.split(line)
        except ValueError:
            words = line.split()
        if line.endswith(" "):
            words.append("")
        return words[1:]  # drop the program name
    return argv


def _flag_names(spec: dict[str, Any], core_only: bool) -> list[str]:
    names = []
    if core_only:
        names += ["--id", "--id-contains"]
    for name, opts in spec["string_flags"].items():
        if core_only and not opts["core"]:
            continue
        names += [f"--{name}", f"--{name}-contains"]
    for group in ("value_flags", "number_flags"):
        for name, opts in spec[group].items():
            if core_only and not opts.get("core"):
                continue
            names.append(f"--{name}")
    if not core_only:
        names += [f"--{name}" for name in spec["bool_flags"]]
    names += ["--limit", "--sort-by", "--ascending", "--descending",
              "--sync", "--live", "--timeout", "--json", "--no-header"]
    return sorted(names)


_COMPLETION_SCRIPTS = {
    "bash": "complete -o default -C 'rates __complete' rates\n",
    "zsh": (
        "_rates() {\n"
        '  local -a candidates\n'
        '  candidates=("${(@f)$(command rates __complete -- '
        '"${(@)words[2,CURRENT]}" 2>/dev/null)}")\n'
        '  (( ${#candidates} )) && compadd -- "${candidates[@]}"\n'
        "}\n"
        "compdef _rates rates\n"
    ),
    "fish": (
        "complete -c rates -f -a "
        '\'(rates __complete -- (commandline -opc)[2..] (commandline -ct))\'\n'
    ),
    "powershell": (
        "Register-ArgumentCompleter -Native -CommandName rates -ScriptBlock {\n"
        "    param($wordToComplete, $commandAst, $cursorPosition)\n"
        "    $words = @($commandAst.CommandElements |"
        " Select-Object -Skip 1 | ForEach-Object { $_.ToString() })\n"
        "    if (-not $wordToComplete) { $words += '' }\n"
        "    rates __complete -- @words | ForEach-Object {\n"
        "        [System.Management.Automation.CompletionResult]::new("
        "$_, $_, 'ParameterValue', $_)\n"
        "    }\n"
        "}\n"
    ),
}


def _print_completion_script(argv: list[str]) -> int:
    shell = argv[0] if argv else ""
    script = _COMPLETION_SCRIPTS.get(shell)
    if script is None:
        shells = ", ".join(sorted(_COMPLETION_SCRIPTS))
        print(
            f"usage: rates completion {{{shells}}}\n"
            "Add the output to your shell config, e.g.:\n"
            '  eval "$(rates completion bash)"              # in ~/.bashrc\n'
            "  rates completion powershell | Out-String | "
            "Invoke-Expression   # in $PROFILE",
            file=sys.stderr,
        )
        return 2
    print(script, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
