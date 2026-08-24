# Changelog

## v1.0.0 (unreleased)

First functional release.

### Added

- The AI pricing universe: 6,889 models (2026-08-23 snapshot) fused from four sources (models.dev preferred; genai-prices, LiteLLM, and OpenRouter filling and cross-validating), with per-record source attribution and price disagreements past 2% stored on the record.
- Three access tiers: a bundled offline snapshot (`rates.ai.load()`), a cheap published-ledger check (`load(sync=True)`), and a full independent fusion of the raw sources (`load(live=True)`), all returning the same `Registry`.
- The query API: `Registry.filter()` with case-insensitive exact matching, explicit `*_contains` substring matching, and unit-explicit price bounds; `sort_by()` with a required direction; `price_units()`; `Model.price_for()` resolving tiered prices; `Model.to_dict()` emitting ledger-shaped JSON.
- The schema: flat per-unit prices (never blended), price tiers with open-shaped conditions, three reasoning control forms (`effort`, `budget_tokens`, `toggle`), split context limits, and lifecycle with deprecation dates.
- The CLI: `rates [universe] list|filter|search|show|info`, `--json` output in the ledger's own shape, `--no-header`, welcome screens, typo suggestions, and data-driven tab completion for bash, zsh, fish, and PowerShell.
- Staleness self-reporting (28-day threshold), typed exceptions and warning categories, generous escalating network timeouts, and optional traceact instrumentation.
- Per-user caching under `~/.cache/rates`: `live` results for 24 hours, synced ledgers reused across later syncs, and completion candidates.

Full manual: [USAGE.md](https://github.com/shehuphd/rates/blob/main/USAGE.md)

## v0.0.3 (2026-08-16)

Name reservation on PyPI. No functionality.
