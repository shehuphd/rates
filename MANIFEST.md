# Manifest

Last updated: 2026-10-08 23:36:39 UTC

One entry per current source file: what it defines and what it touches. A map for orienting in the codebase, not a second copy of the docstrings.

## Package (`src/rates/`)

| File | What it is |
|---|---|
| `__init__.py` | Public package surface: `__version__` and re-exports of the exception and warning classes from `_errors`. |
| `__main__.py` | `python -m rates` entry point; hands off to `_cli.main()`. |
| `_cache.py` | `cache_dir()`: the per-user cache directory (`~/.cache/rates`, created `0700`) the load tiers, freshness lookups, and tab completion write under. |
| `_cli.py` | The whole CLI: argument parsing, the `list`/`filter`/`search`/`show`/`info` presets, table and JSON rendering, tab completion, exit codes. |
| `_domains.py` | The domain registry: which domains exist and how the CLI and API address them. |
| `_errors.py` | Exception and warning hierarchy (`RatesError`, `LiveFusionError`, `AllSourcesUnreachableError`, `PreferredSourceUnavailableError`; `RatesWarning` and its four categories). |
| `_http.py` | Zero-dependency HTTP layer over `urllib`: `fetch_json` and `fetch_text` (Accept negotiation for origin pages), retry ladder with backoff, `FetchError`. Network only; no disk. |
| `_record.py` | The core `Record` protocol every domain's model satisfies (provider, id, type, price, lifecycle, observed_at). |
| `_resolution.py` | The resolution ladder: `SourceCard`, `Candidate`, the seven rungs, skip semantics, `resolve()`. Pure; no I/O. |
| `_trace.py` | Soft-dependency shim for traceact: `traced()` leaves the function's behaviour unchanged when traceact is absent, and `configure_cli_tracing()` sets the CLI's quiet file sink. |
| `ai/__init__.py` | AI domain surface: re-exports `load`, the model dataclasses except `Alias`, `Registry`, and `Source`. |
| `ai/_freshness.py` | Per-source commit-history freshness lookups for the ladder's freshness rung, with a one-hour on-disk cache. Network through `urllib` directly; every failure degrades to `None`. |
| `ai/_fusion.py` | `fetch_sources()` and `fuse()`: per-source degradation, admission criteria, origin-record merge, discrepancy notes, the envelope (`SCHEMA_VERSION`, sources, resolution scorecards). |
| `ai/_load.py` | The three fetch tiers (`bundled`/`stable`/`live`), ledger download and cache handling, and the warnings each tier emits (bundled-snapshot notice, staleness, stable fallback, skipped sources). |
| `ai/_model.py` | `Model`: typed view of one record, `price_for()` tier resolution, `to_dict()`. |
| `ai/_origins.py` | Origin page sources: `ORIGIN_URLS`/`ORIGIN_CARDS`, `UNIT_REGISTRY` with dated per-vendor unit verifications, one parser per vendor page (Deepgram JSON-LD, AssemblyAI markdown, ElevenLabs markdown, LiveKit HTML, TypeSafe markdown), shape qualification, `normalize_origins()`. Pure over fetched text; no I/O. |
| `ai/_registry.py` | `Registry`: `from_dict`, `filter()`, `sort_by()`, `price_units()`, envelope accessors. |
| `ai/_sources.py` | The four feed sources: `SOURCE_URLS`, `SOURCE_CARDS`, one normalizer per feed. Pure over fetched payloads. |
| `ai/ledger-ai.json.gz` | The bundled ledger snapshot, rebuilt weekly by the ledger workflow. Never hand-edited. |
| `py.typed` | PEP 561 marker; the package ships its type hints. |

## Scripts (maintainer/CI only, not part of the installed package)

| File | What it is |
|---|---|
| `scripts/build_ledger.py` | Builds `ledger-ai.json` and the bundled gz from the live sources; prints the degraded-sources and origin-delta reports; bakes KeyCall alias facts in (requires `keycall`). |
| `scripts/sync_docs.py` | Rewrites the sample output in README/USAGE that the weekly build moves (the `rates ai list` sample; model, provider, and typed counts; snapshot and source-check dates; schema version); `--check` reports drift. |
| `scripts/jev_check.py` | Advisory extraction-fidelity check: asks TypeSafe's Jev whether each origin row's rate is stated by its page. Requires `TYPESAFE_API_KEY` and `typesafe-sdk`; skips with a printed reason without them; always exits zero. Records spend to `jev-spend.jsonl` (gitignored). |

## Tests

| File | What it covers |
|---|---|
| `tests/conftest.py` | Autouse fixtures (snapshot-notice flag, traceact file sink, isolated cache dir), the `live` marker gate (`RATES_LIVE_PROBES=1`), and the per-run `TEST_INDEX.CSV` writer. |
| `tests/test_cli.py` | Every CLI preset, flag, error path, and output format against fixture ledgers. |
| `tests/test_docs.py` | Docs-vs-code guards: README links absolute for PyPI, no internal-file references in public text, NOTICE covers every fetched source (feeds and origin pages), CHANGELOG ledger claims trail the bundled ledger, and the doc samples `sync_docs` maintains are in sync. |
| `tests/test_domains.py` | Domain registry addressing. |
| `tests/test_edges.py` | Degenerate and hostile envelope and record inputs, the CLI as a subprocess, and `scripts/build_ledger.py` (artifacts, alias facts, the origin delta report). |
| `tests/test_freshness.py` | Freshness lookups and their cache, mocked transport. |
| `tests/test_fusion.py` | Admission criteria, per-source degradation, discrepancy notes, envelope construction. |
| `tests/test_http.py` | Retry ladder, backoff, error taxonomy for `fetch_json` and `fetch_text`, and the Accept header each sends; scripted transport, no network. |
| `tests/test_jev_check.py` | `scripts/jev_check.py` against a scripted fake SDK: every skip path, the questions asked, disagreement reporting, spend rows, and the content-block split. No network. |
| `tests/test_keycall_probe.py` | Drift probe: the installed KeyCall release's `alias_fact` contract still matches what the ledger build relies on. Keyless and offline. |
| `tests/test_load.py` | The three fetch tiers, their caches, every warning they emit (including skipped origin pages), and `GITHUB_TOKEN` pickup on the stable check. |
| `tests/test_model.py` | The model dataclasses, over fixed ledger-shaped records. |
| `tests/test_origin_probes.py` | Live drift probes: each origin vendor's page still parses into records. |
| `tests/test_origins.py` | Origin parsers against recorded fixtures, hostile-input qualification, `normalize_origins` statuses, fusion admission of origin rows. |
| `tests/test_registry.py` | `Registry` filtering, sorting, and envelope accessors. |
| `tests/test_resolution.py` | The ladder rung by rung: which candidate wins, the deciding rung, skip semantics, intra-rung ties, and the registry-order floor. |
| `tests/test_source_probes.py` | Live drift probes: each feed still serves the shape its normalizer expects. |
| `tests/test_trace.py` | The traceact shim: identity when traceact is absent, and a decorated function's result left alone either way. |
| `tests/test_usage_examples.py` | Python API recipes from USAGE.md, restated as tests against the bundled ledger. |
| `tests/test_version.py` | `__version__` matches pyproject. |
| `tests/fixtures/origins/` | Reduced extracts of the five vendor pricing pages (the speech vendors 2026-09-26, TypeSafe 2026-10-09): model and plan names, rates, and unit labels in each page's own structure, the recorded twins of the live probes. |

## Workflows and packaging

| File | What it is |
|---|---|
| `.github/workflows/ci.yml` | Lint (ruff), types (mypy), ShipLock's deterministic checks, and the full test suite, on manual dispatch (push/PR triggers currently off). |
| `.github/workflows/ledger.yml` | Weekly Monday rebuild: drift probes, build, advisory Jev check, doc-sample sync, full suite, ledger commit, dated release. |
| `.github/workflows/release-gate.yml` | ShipLock's docs-vs-code gate (deterministic checks plus the optional agent audit), on manual dispatch. |
| `.github/workflows/release.yml` | PyPI publish via trusted-publisher OIDC on a GitHub Release. |
| `.github/dependabot.yml` | Update PRs for the `pip` and `github-actions` ecosystems. |
| `pyproject.toml` | Package metadata, version, zero runtime dependencies, pytest/ruff/mypy configuration. |
| `shiplock.toml` | ShipLock release-pipeline configuration. |
| `NOTICE` | Third-party attribution for the feeds and the origin pricing pages. |
