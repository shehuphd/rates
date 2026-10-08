# Changelog

## 1.2.0 (2026-10-09)

### Added

- TypeSafe as a fifth origin page source: Jev's per-token rates read from TypeSafe's own models page (`docs.typesafe.ai/models`). Three records ship, the versioned `jev-1.13.0` and the published aliases `jev-latest` and `jev-preview`, each alias row at its target's price, so a caller pricing by either the versioned id or the id TypeSafe's model listing serves can resolve it.
- An `evaluation` model type for decision models that return typed judgments over supplied options, the mode word LiteLLM uses for the category.
- A published zero rate is carried when the page states it in words: Jev's records price `output_mtok` at 0 because the models page says output tokens are free. A bare zero figure with no such statement is still refused as a parse artifact.

## 1.1.0 (2026-10-02)

### Added

- Origin page sources: speech-model rates read directly from the publishing vendor's own pricing page, in the per-minute and per-character units the vendors bill in. Four vendors ship in this release, Deepgram, AssemblyAI, ElevenLabs, and LiveKit, contributing 39 transcription and speech-synthesis records. An origin record has the same schema as a feed record and names its page as its only source; where a page and a feed carry the same provider-and-id row, the page's record replaces the feed's whole. See ARCHITECTURE.md § Origin pages.
- Four price units for duration- and character-billed models: `audio_minute` (pre-recorded audio), `streaming_audio_minute` (streamed audio), `session_minute` (streaming-connection time, AssemblyAI's own meter), and `kchar` (per 1,000 characters). Each unit's counted event is defined, with a dated per-vendor verification, in the unit registry (`rates/ai/_origins.py`), and the three duration units never convert into one another. USAGE.md § Speech models covers querying them.
- An `origin` source role and a `suspect` source status in the registry envelope. A pricing page that was fetched but yielded no records reports `suspect` and contributes nothing, so a vendor's page redesign degrades one source instead of shipping wrong rows.
- On `fetch="live"`, a `SourceUnreachableWarning` naming any pricing page that was skipped (unreachable or `suspect`) and saying its records are absent from the result.
- Origin-page drift probes in the weekly ledger workflow, an advisory per-provider delta report in each build (vanished, new, and moved rows against the previous snapshot), and an optional advisory extraction-fidelity check (`scripts/jev_check.py`, maintainer/CI tooling) that asks TypeSafe's Jev whether each extracted rate is stated by its page.
- `MANIFEST.md`, a per-file map of the source tree.

### Changed

- Ledger `schema_version` moves to 1.1.0 for the additive vocabulary above (the `origin` role, the `suspect` status, the speech units). A reader of 1.0.0 ledgers still loads it; no field changed shape.
- The bundled ledger refreshed to ~8,000 models (2026-09-28 snapshot), the speech records included.
- The package's trove classifier now reads Production/Stable.

### Fixed

- Tab completion offers `--force` wherever the flag is accepted.
- `fetch="stable"` falls back to the local ledger with a `SyncFallbackWarning` when GitHub's release listing or the published ledger is malformed, or a connection drops mid-response; it previously raised on some of these.
- A dropped or truncated connection, or a feed response body that isn't UTF-8, now counts as an unreachable source on `fetch="live"` instead of raising out of the fusion.
- The skipped-source warnings on `fetch="live"` say the thinner result is cached for 24 hours and name `force=True` as the way to refetch sooner.
- `--sort-by` help on an unscoped query names fields an unscoped query can sort on.
- Documentation corrected against the code and the shipped ledger: source counts, admission routes, the resolution ladder's origin rung (inert while no price-carrying feed is first-party for a provider), warning categories, and the worked examples in ERD.md and USAGE.md.

## 1.0.2 (2026-09-10)

### Added

- A once-per-process notice (`BundledSnapshotWarning`) on the first default `load()` in a process, naming the snapshot date and the `stable`/`live` tiers, so a caller pricing against the registry (a spend cap, say) knows it's reading a dated snapshot rather than live data. It's a `RatesWarning`, so a caller that has chosen offline data on purpose can silence it with a filter; when the snapshot is also past the staleness threshold, `StaleLedgerWarning` carries the signal instead.

## 1.0.1 (2026-09-10)

### Fixed

- `load(fetch="live")` no longer raises when the cache directory can't be written (for example a container running as a user whose home directory is unwritable). The source-freshness cache write degrades to a no-op the way the ledger caches already do, so a successful live fusion returns its result instead of being lost to a `PermissionError`.

### Changed

- The bundled ledger refreshed to the current 2026-09-10 snapshot (7,270 models).

## 1.0.0 (2026-09-10)

The first stable release. The CLI, the Python API, and the fused ledger carry forward from the 0.0.x pre-releases that proved the publishing pipeline, with the reasoning query surface added and the bundled data refreshed.

### Added

- `--reasoning-level` and `--reasoning-control` filters, with the matching `reasoning_level`/`reasoning_control` criteria in `Registry.filter()`: narrow to models exposing a named thinking level (`low`, `medium`, `high`, ...) or a reasoning dial shape (`effort`, `budget_tokens`, `toggle`). A value the data doesn't carry is refused with the queryable set listed, and tab completion offers the data's own levels and controls.
- A `REASONING` column in `list`/`filter`/`search` tables: the named levels joined with `/` (`low/medium/high`), or the control's own word where a model has no named levels (`toggle`, `budget_tokens`), in the same vocabulary the `--reasoning-*` flags take.

### Changed

- The bundled ledger refreshed to ~7,000 models (2026-09-10 snapshot).

## 0.0.4 (2026-09-01)

First functional release, shipped under a pre-1.0 version number so the PyPI publishing pipeline (branch protection, the GitHub Release trigger, the trusted-publisher OIDC flow) proves out on a disposable version before v1.0.0 claims it.

### Added

- The AI pricing domain: ~7,000 models (2026-09-01 snapshot) fused from four sources (models.dev preferred; genai-prices, LiteLLM, and OpenRouter filling and cross-validating), with per-record source attribution and price disagreements past 2% stored on the record.
- Three access tiers, picked by one `fetch` parameter: a bundled offline snapshot (`rates.ai.load()`, the default), a cheap published-ledger check (`load(fetch="stable")`), and a full independent fusion of the raw sources (`load(fetch="live")`), all returning the same `Registry`.
- The query API: `Registry.filter()` with case-insensitive exact matching, explicit `*_contains` substring matching, and unit-explicit price bounds; `sort_by()` with a required direction; `price_units()`; `Model.price_for()` resolving tiered prices; `Model.to_dict()` emitting ledger-shaped JSON.
- The schema: flat per-unit prices (never blended), price tiers with open-shaped conditions, three reasoning control forms (`effort`, `budget_tokens`, `toggle`), split context limits, and lifecycle with deprecation dates.
- The CLI: `rates [domain] list|filter|search|show|info`, `--json` output in the ledger's own shape, `--no-header` (rows only, no footer, for pipelines), welcome screens, typo suggestions, and data-driven tab completion for bash, zsh, fish, and PowerShell.
- Staleness self-reporting (28-day threshold), typed exceptions and warning categories, generous escalating network timeouts, and optional traceact instrumentation.
- Per-user caching under `~/.cache/rates`: `live` results for 24 hours, `stable`-fetched ledgers reused across later checks (and by the bundled tier's own staleness clock), and completion candidates.
- Third-party attribution in `NOTICE`, shipped inside the wheel alongside the license: each upstream source's copyright notice and license, what it contributes to a record, and the endpoint or file consumed.
- `SourceUnreachableWarning`: a `fetch="live"` fusion that succeeds while a non-preferred source was unreachable now says so, naming the skipped source, instead of returning a silently thinner result.
- Provenance timestamps are UTC instants: a source's `fetched_at` and a new per-record `observed_at` (null in the AI domain, for a later domain whose values are observed continuously). A release's `snapshot_date` and announced `release_date`/`deprecation_date` stay dates. An older ledger's day-only timestamp still reads, floored to midnight UTC.
- A neutral cross-domain record contract (`Record`) and a single domain registry that the CLI and loader read, so adding a domain is a spec entry rather than an edit to the loader or query code, and each domain declares which access tiers it supports.
- The zsh completion script works both ways: sourced from `.zshrc` or dropped into an `$fpath` directory as `_rates` and autoloaded (a `#compdef` tag plus a sourced-vs-autoloaded guard). USAGE documents install and uninstall for every shell, and that wiring it up is opt-in and a persistent change to shell startup.
- Faster startup and tab completion: traceact is imported on the first traced call rather than at import, completion is dispatched before tracing is set up, and the completion path no longer imports a domain to find its cache or stat a bundled file. `rates --version` roughly halved; a warm completion no longer imports the AI domain or traceact at all.
- `Model.alias`: whether a record's own `id` is a rolling reference (`gemini-pro-latest`) rather than a dated snapshot, per KeyCall's per-provider convention catalog. Baked into the ledger at build time only; the installed package stays zero-dependency, and `fetch="live"` carries no `alias` field.
- The resolution ladder for price disagreements. Sources reporting one record's price are witnesses to a single fact, not competing offers, so the shipped value is picked by a fixed ladder rather than any per-pair contest: a first-party source (a reseller's own API describing its own rows) ends the contest outright; freshest update evidence ranks the witnesses next, since a price change is an event only fresh data has seen; then corroboration among independent witnesses, pinned overrides (each with its reason recorded), measured accuracy, coverage, and a declared strict order that can never tie. A rung with incomplete evidence skips whole rather than ranking anyone on evidence nobody gathered, and unmeasured is never treated as zero. Price itself is never a rung: picking the most flattering report would understate bills, and comparing competing offers stays the caller's query over separate per-provider rows.
- Every discrepancy note now names the value that shipped. A contested unit resolves once across all its carriers, then writes one note per disagreeing source, each carrying the same `chosen_value` (the number in `price`) and the same `resolved_by` (the deciding rung). Previously each fallback was resolved pairwise, so on a unit two fallbacks both disputed, a note could name a value that lost a later pairwise round and never shipped.
- The envelope's `resolution` object: the ladder and each source's scorecard (first-party pairings, declared order, coverage, and evidence fields that stay `null` until measured), so any record's resolution is replayable and auditable from the ledger file alone.
- LiteLLM-derived per-mtok rates round away the unit-conversion float artifact (a per-token rate times a million multiplies to `0.19999999999999998` in binary floats; the ledger now carries `0.2`, the source's decimal intent).

### Fixed

- The CLI's typo suggestions and choice-error messages read argparse's own error text to build their choice list. Python 3.12 stopped quoting each choice in that text (`choose from a, b` instead of `choose from 'a', 'b'`), which emptied the CLI's rendered list under 3.12 while it still worked under 3.10. Parsing no longer requires the quotes.

Full manual: [USAGE.md](https://github.com/shehuphd/rates/blob/main/USAGE.md)

## 0.0.3 (2026-08-16)

Name reservation on PyPI. No functionality.
