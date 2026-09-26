# Manifest

Last updated: 2026-09-26 19:55:35 UTC

One entry per current source file: what it defines and what it touches. A map for orienting in the codebase, not a second copy of the docstrings.

## Package (`src/rates/`)

| File | What it is |
|---|---|
| `__init__.py` | Public package surface: `__version__`, re-exports of the domain modules, warning classes. |
| `__main__.py` | `python -m rates` entry point; hands off to `_cli.main()`. |
| `_cache.py` | On-disk cache for fetched ledgers and freshness lookups under the user cache dir; every write degrades to a no-op on failure. |
| `_cli.py` | The whole CLI: argument parsing, the `list`/`filter`/`search`/`show`/`info` presets, table and JSON rendering, tab completion, exit codes. |
| `_domains.py` | The domain registry: which domains exist and how the CLI and API address them. |
| `_errors.py` | Exception and warning hierarchy (`RatesError`, `FetchError`, `AllSourcesUnreachableError`, `StaleLedgerWarning`, ...). |
| `_http.py` | Zero-dependency HTTP layer over `urllib`: `fetch_json` and `fetch_text` (Accept negotiation for origin pages), retry ladder with backoff, `FetchError`. Network only; no disk. |
| `_record.py` | The core `Record` protocol every domain's model satisfies (provider, id, type, price, lifecycle, observed_at). |
| `_resolution.py` | The resolution ladder: `SourceCard`, the seven rungs, skip semantics, note construction. Pure; no I/O. |
| `_trace.py` | Opt-in tracing of query and resolution decisions for `--trace`. |
| `ai/__init__.py` | AI domain surface: `load()`, warning emission for bundled snapshots. |
| `ai/_freshness.py` | Per-source commit-history freshness lookups for the ladder's freshness rung, with the one-hour cache. Network via `_http`. |
| `ai/_fusion.py` | `fetch_sources()` and `fuse()`: per-source degradation, admission criteria, origin-record merge, discrepancy notes, the envelope (`SCHEMA_VERSION`, sources, resolution scorecards). |
| `ai/_load.py` | The three fetch tiers (`bundled`/`stable`/`live`), ledger download and cache handling. |
| `ai/_model.py` | `Model`: typed view of one record, `price_for()` tier resolution, `to_dict()`. |
| `ai/_origins.py` | Origin page sources: `ORIGIN_URLS`/`ORIGIN_CARDS`, `UNIT_REGISTRY` with dated per-vendor unit verifications, one parser per vendor page (Deepgram JSON-LD, AssemblyAI markdown, ElevenLabs markdown, LiveKit HTML), shape qualification, `normalize_origins()`. Pure over fetched text; no I/O. |
| `ai/_registry.py` | `Registry`: `from_dict`, `filter()`, `sort_by()`, `price_units()`, envelope accessors. |
| `ai/_sources.py` | The four feed sources: `SOURCE_URLS`, `SOURCE_CARDS`, one normalizer per feed. Pure over fetched payloads. |
| `ai/ledger-ai.json.gz` | The bundled ledger snapshot, rebuilt weekly by the ledger workflow. Never hand-edited. |
| `py.typed` | PEP 561 marker; the package ships its type hints. |

## Scripts (maintainer/CI only, not part of the installed package)

| File | What it is |
|---|---|
| `scripts/build_ledger.py` | Builds `ledger-ai.json` and the bundled gz from the live sources; prints the degraded-sources and origin-delta reports; bakes KeyCall alias facts in (requires `keycall`). |
| `scripts/sync_docs.py` | Rewrites exact model-count samples in README/USAGE to the freshly built ledger before the docs tests run. |
| `scripts/jev_check.py` | Advisory extraction-fidelity check: asks TypeSafe's Jev whether each origin row's rate is stated by its page. Requires `TYPESAFE_API_KEY` and `typesafe-sdk`; skips with a printed reason without them; always exits zero. Records spend to `project/jev-spend.jsonl`. |

## Tests

| File | What it covers |
|---|---|
| `tests/conftest.py` | Shared fixtures and the `live` marker gate (`RATES_LIVE_PROBES=1`). |
| `tests/test_cli.py` | Every CLI preset, flag, error path, and output format against fixture ledgers. |
| `tests/test_docs.py` | Docs-vs-code guards: README links absolute for PyPI, NOTICE covers every fetched source (feeds and origin pages), CHANGELOG ledger claims trail the bundled ledger. |
| `tests/test_domains.py` | Domain registry addressing. |
| `tests/test_edges.py` | Degenerate and hostile envelope and record inputs. |
| `tests/test_freshness.py` | Freshness lookups and their cache, mocked transport. |
| `tests/test_fusion.py` | Admission criteria, per-source degradation, discrepancy notes, envelope construction. |
| `tests/test_http.py` | Retry ladder, backoff, error taxonomy, Accept handling; mocked transport. |
| `tests/test_keycall_probe.py` | Live probe: KeyCall's alias-fact API still answers as the build expects. |
| `tests/test_load.py` | The three fetch tiers and their cache behavior. |
| `tests/test_model.py` | `Model` accessors and `price_for()` tier resolution. |
| `tests/test_origin_probes.py` | Live drift probes: each origin vendor's page still parses into records. |
| `tests/test_origins.py` | Origin parsers against recorded fixtures, hostile-input qualification, `normalize_origins` statuses, fusion admission of origin rows. |
| `tests/test_registry.py` | `Registry` filtering, sorting, and envelope accessors. |
| `tests/test_resolution.py` | The ladder rung by rung: partitioning, skip semantics, note contents. |
| `tests/test_source_probes.py` | Live drift probes: each feed still serves the shape its normalizer expects. |
| `tests/test_trace.py` | Trace output. |
| `tests/test_usage_examples.py` | Code samples in USAGE.md execute as written. |
| `tests/test_version.py` | `__version__` matches pyproject. |
| `tests/fixtures/origins/` | Trimmed captures of the four vendor pricing pages (2026-09-26), the recorded twins of the live probes. |

## Workflows and packaging

| File | What it is |
|---|---|
| `.github/workflows/ci.yml` | Full test suite on manual dispatch (push/PR triggers currently off). |
| `.github/workflows/ledger.yml` | Weekly Monday rebuild: drift probes, build, doc-count sync, full suite, advisory Jev check, ledger commit, dated release. |
| `.github/workflows/release-gate.yml` | Pre-release checks on release publication. |
| `.github/workflows/release.yml` | PyPI publish via trusted-publisher OIDC on a GitHub Release. |
| `.github/dependabot.yml` | Action-version update PRs. |
| `pyproject.toml` | Package metadata, version, zero runtime dependencies, pytest/ruff/mypy configuration. |
| `shiplock.toml` | ShipLock release-pipeline configuration. |
| `NOTICE` | Third-party attribution for the feeds and the origin pricing pages. |
