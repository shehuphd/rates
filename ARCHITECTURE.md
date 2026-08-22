# Architecture

## What this document covers

`rates` is a pricing registry and decision layer. This document covers the two decisions that shape everything else: what `rates` refuses to do, and how it's structured to support more than one pricing domain over time. The concrete data model lives in [ERD.md](ERD.md); this document is the reasoning behind it.

## We don't decide for you

`rates` exposes verified facts about a model: what it costs, what it accepts, what it produces, whether it's still viable to build on. It does not rank, score, or editorialize. No field in the schema encodes an opinion about which model is "best," "quality," or "worth" using for a given task, that judgment belongs to the caller, who knows their own budget and workload in a way a registry never can.

The clearest way to hold this line: think of a model as a food product. The label states the maker, the category, the ingredients, the dimensions, the price, and the dates, all verified facts. It does not print "best peanut butter in the aisle" on the front, that's the manufacturer's opinion, not a fact about the product. `rates` is the label, not the marketing copy.

This ruled out a design we built and then discarded: a four-tier `dev`/`bulk`/`economy`/`quality` taxonomy, assigned per model. It failed for two concrete reasons, not just on principle:

- **The labels claimed things price alone can't establish.** `dev` implied a model wasn't fit for production, but Claude Haiku 4.5 runs at real production volume for classification and extraction work today. `quality` implied "best available," but price-band logic alone would file aging, expensive models (`claude-3-opus-latest`, `gpt-4-32k`) as `quality` just because they cost a lot, not because they're still competitive.
- **Making the labels correct required permanent hand-curation** (a maintained list of which models count as "frontier"), which doesn't scale across a catalog in the thousands and drifts stale the moment someone forgets to update it.

Instead: expose the raw axes (price, context, modality, reasoning capability) as data, and let `Registry.filter(...)` be a query over that data, driven by whatever the caller asks for. Nothing stored on a model record asserts an opinion about it.

## A galaxy of pricing universes, not one registry

`rates` starts with AI API pricing, but the name and the architecture are deliberately broader. Cloud pricing (compute, storage, egress) and quantum computing pricing (qubit-time, queue priority) are different problems with different axes, entirely, not variations on the AI-model schema with a few fields renamed.

The architecture reflects that split directly: a small core, shared by every universe, and a per-universe extension that owes nothing to any other universe's shape.

**Core (every universe implements this):**
- Provider/vendor identity
- Price, as an amount, a unit, and a currency, never collapsed into one blended number (see [ERD.md](ERD.md) for why)
- A tiering *concept* (cheap-to-expensive exists everywhere), with no shared label set or thresholds, each universe defines its own
- Lifecycle (products get deprecated and retired in every domain, not only AI)

**AI universe (this is what's built so far):**
- Model type/purpose, modality support, reasoning capability and its shape, context limits, tool/structured-output support

A future `rates.cloud` universe would define its own axes entirely (region, commitment type, durability class) against the same four-field core. Nothing in the AI universe's schema is assumed to generalize.

## Why the AI universe fuses several sources instead of consuming one

No single existing source covers what this universe needs:

| Source | Strong for | Missing |
|---|---|---|
| [genai-prices](https://github.com/pydantic/genai-prices) | Broadest provider and model-type coverage (36 providers, all model types including non-chat) | No type, modality, or reasoning fields at all |
| [LiteLLM](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) | Native `mode` field (model type), wide capability booleans, per-second video pricing | Reasoning-effort levels are sparse and inconsistently populated |
| [OpenRouter](https://openrouter.ai/api/v1/models) | Ordered reasoning-effort vocabulary, modality arrays | Chat-completion models only, no embedding/image/audio/moderation coverage at all |
| [models.dev](https://github.com/anomalyco/models.dev) | Richest single per-model schema (family, modalities, typed reasoning options, lifecycle status, split context/output limits) | No type/mode field; gaps in specific categories (no pricing at all for Veo video models as of this writing) |
| [Hugging Face Hub](https://huggingface.co/docs/hub/api) | Open-weight model task type and provenance | Not a pricing source; only useful for cross-referencing open-weight models |

**models.dev is the primary ingestion source**, genai-prices, LiteLLM, and OpenRouter fill gaps and cross-validate, Hugging Face enriches open-weight entries specifically. `rates` still exists as an independent project on top of this rather than simply re-exporting models.dev, for reasons distinct from data quality:

1. **Provenance.** models.dev's own update history is bot-synced from other catalogs roughly hourly (`opencode-agent[bot]`), not independently human-verified per field. `rates`' registry carries per-field source attribution (see [ERD.md](ERD.md)) so a caller can see where a value came from, not just what it is.
2. **History.** models.dev serves only its latest snapshot. `rates` ships dated, versioned JSON releases, so "did this model's price change since March" is answerable by diffing two releases, not lost the moment the source overwrites its own data.
3. **Scope.** models.dev is an AI-model database. `rates` is a pricing framework meant to span AI, cloud, and other domains under one coherent, extensible schema, a different project shape entirely.
4. **Control.** A registry this central to how a caller picks and pays for a model is worth owning outright rather than depending on a third party's schema decisions and update cadence.

## `ledger`, `sync`, and `live`: three ways to get data, one fusion engine

Rates are durable. Once a provider sets a price, it typically holds for a long while, AI API pricing especially, cloud pricing similarly, both usually announced well ahead of taking effect. That fact shapes the whole distribution model: the common case isn't "refetch everything," it's "confirm nothing changed," and only doing the expensive work when it did.

- **`ledger` (the default).** A dated, versioned JSON snapshot, bundled with the installed package. Zero network calls, ever. This is what makes `rates` usable in an air-gapped environment with no configuration, `pip install rates` and it works.
- **`sync` (a cheap freshness check).** No bespoke manifest file, GitHub's own Releases API already is one: each `ledger` publishes as a GitHub Release, so `GET /repos/shehuphd/rates/releases/latest` returns the tag and publish date for free, nothing to hand-write or keep in sync during the weekly publish step. If it matches what's already on hand, done, milliseconds. If it doesn't, only then is the newer published ledger's asset pulled down, still our own already-fused output, not a re-fetch of all four raw sources.
- **`live` (opt-in, the heavy option).** The full fusion function (models.dev primary, gaps filled from genai-prices/LiteLLM/OpenRouter, per-field source attribution), run by the caller's own process against the four raw sources directly, bypassing anything we've published. This is the exact same function we run on our own weekly schedule to produce each new `ledger` release, exposed as a public capability rather than kept as internal tooling, so nobody has to trust our merge logic without being able to run it themselves.

`sync` and `live` are both strictly opt-in, never triggered implicitly. A caller who calls neither never causes `rates` to make an outbound request, which matters as much as the features themselves for anyone in a restricted network (a defense environment, an air-gapped facility) who needs that guarantee to hold even if they forget to check a setting.

**`live` caches for the length of a session, not per call.** The first `live` call in a process fetches and merges fresh, then stores the result in a temp file; every subsequent read in that same run reuses it. The temp file expires after 24 hours, since data that old no longer meaningfully qualifies as `live`.

**Staleness is self-reported even fully offline.** The bundled `ledger` carries its own `snapshot_date`, comparable against today's date with no network call at all. Each universe sets its own staleness threshold, since "too old" means something different per domain, the AI universe's is 4 weeks; a hypothetical future forex universe, where rates move by the hour, would need something closer to 24 hours. Past the threshold, `rates` surfaces a visible warning naming how stale the data is and how to fix it, pointing directly at `live` (or `sync`, if network access exists but a full independent fusion isn't wanted), rather than silently continuing to serve an old snapshot as if it were current.

**Partial source failure is a first-class case for `live`, not an edge case.** One of four sources being slow, rate-limited, or returning a changed schema is the normal operating condition for an open-internet call, not a rare failure to special-case later. The fusion function degrades: a source that can't be reached is skipped, noted as unreachable in that response's `sources` metadata, and the other three still return a usable result. It never hard-fails a whole request because one of four sources had a bad moment, and it never silently drops a field without saying which source was supposed to supply it.

**Schema drift in the sources themselves gets watched, not assumed away.** Each of the four upstream sources can change shape without warning (a field renamed, a value vocabulary that changes, an endpoint that starts returning something new). Per [CODING.md](~/.claude/CODING.md)'s "Capability-drift probes" pattern, `rates`' own test suite carries live probes against each source's actual current schema, not mocks frozen at whatever shape they had when this was written, so a source changing underneath us fails a test and names which source and which field, rather than silently degrading `live` results with no one noticing.

### Reliability specifics

Settled by stress-testing the design against realistic bad conditions (per CODING.md's "production is where independently-rare conditions overlap"), not by picking numbers that sounded reasonable in isolation:

- **Timeouts are generous defaults, not fixed ceilings.** 5s to connect, 45s to read a full response, both caller-overridable (`rates.ai.load(live=True, timeout=180)`). A short default tuned only for good connections fails outright on real degraded links, satellite-backed in-flight wifi included, where a few megabytes can genuinely take minutes; a caller who knows their situation needs to be able to say so, rather than being stuck with a number tuned for everyone else.
- **One retry on transient failure only** (timeout, connection error), a short fixed backoff, not exponential. A clean error response (404, malformed JSON) never retries, that's not a connectivity problem a retry fixes.
- **All duration checks anchor to UTC, never local wall-clock time.** The 24-hour `live` cache expiry and the staleness-vs-`snapshot_date` check both use `datetime.now(timezone.utc)`. A system's local timezone shifting mid-session (a flight crossing zones) must never change when a cache is considered stale.
- **`sync` never raises.** Unlike `live`, which explicitly promises fresh-or-nothing, `sync` promises something weaker: a cheap check, best-effort. Any failure to complete that check (GitHub's API rate-limited, GitHub down, a bad connection) falls back silently to whatever's already local, with a warning, never a blocking exception, a feature meant purely as a convenience shouldn't be able to take down a caller's program.
- **GitHub's unauthenticated API rate limit is 60 requests/hour, per IP, confirmed live against `api.github.com`.** That's shared across everyone on the same IP, which matters most in CI: GitHub Actions' hosted runners share IP ranges across every customer on the platform, not just `rates` users, so a `sync` call can get rate-limited by traffic that has nothing to do with `rates` at all. Mitigation that costs nothing: both `sync` and `live` check for a `GITHUB_TOKEN` environment variable, already set in every GitHub Actions job by default, and use it as a bearer token when present, raising the effective limit to 5,000/hour with no configuration from the caller.
- **A `sync`-triggered follow-up fetch (when something did change) is not "cheap."** The manifest check is small and fast; pulling the actual updated ledger asset is multi-megabyte, same as `live`'s fetches, and inherits the same configurable timeout, not a separate hardcoded assumption that it'll always be quick.
- **`REGISTRY.schema_version` is checked before parsing, not after.** A `rates` install pinned for a long time, combined with a schema that evolved a breaking change in the meantime, means a freshly-fetched `sync`/`live` result can be a shape the installed dataclasses don't understand. Checked against a compatibility rule before parsing, with a specific message ("this ledger needs a newer `rates`, run `pip install -U rates`"), rather than an opaque failure partway through building a `Model`.

**Exception shape**, distinct types for distinct situations, so a caller can catch broadly or specifically:

```python
class RatesError(Exception): ...              # base for everything rates raises
class LiveFusionError(RatesError): ...         # base: a live=True call couldn't produce a result
class AllSourcesUnreachableError(LiveFusionError): ...   # every source failed
class PrimarySourceUnavailableError(LiveFusionError): ...  # models.dev specifically failed; even
    # with the other three healthy, the result would be missing most fields per ERD.md's source
    # map, refused rather than returned looking successful
```

`sync` never raises any of these, per the point above, `live` does, since it's the mode that explicitly promises a result or an explanation of why not.

## Public API shape

- `rates.ai` is the namespace for this universe. `rates.ai.load(...)` is the entry point, returning a `Registry`, not a bare list, so the envelope metadata (`schema_version`, `snapshot_date`, `sources` and their `status`) travels with the models rather than being discarded.
- `Registry.filter(...)`, not `recommend(...)`. `recommend` implies a judgment call about which model is right, which contradicts "we don't decide for you." `filter` narrows a set by facts the caller supplied (budget, required modality, reasoning capability, still active) and can return zero, one, or many matches, `rates` has no view on which of those is best.
- Sorting is a separate chained call, `Registry.filter(...).sort_by(field, descending=...)`, never a `filter()` parameter. They're different jobs (narrowing vs. ordering), and a caller may want to sort without filtering at all. `descending` is always stated explicitly, no field defaults to a "good" direction, that would be another opinion smuggled into the interface.
- Model records are stdlib `dataclasses`, not Pydantic. Pydantic would land as a mandatory dependency on the default `ledger` install path, not just `live`, breaking the zero-dependency, air-gapped-safe promise for the common case.

## CLI shape

One primitive, `filter`, with three presets over it. Universe scoping is positional. Matching is exact by default, broadened only by explicit spelling. Flags mirror the Python API one-to-one, so the CLI and the code share a single vocabulary.

### One primitive, three presets

`list`, `search`, and `show` exist because they match how people think, not because they do anything `filter` can't:

| Command | Equivalent filter | For |
|---|---|---|
| `filter` | itself | Narrowing by any schema fields |
| `list` | `filter` with no constraints, a default limit, table output | "Show me what's in here" |
| `search <phrase>` | `filter` with contains-matching preset across name fields | "I don't know the exact ID" |
| `show <id>` | `filter` narrowed to one exact identity, full-detail output | "Tell me everything about this one" |

### Universe scoping is positional, not a flag

`rates ai list` scopes to the AI universe; `rates search "opus"` runs unscoped across every universe. Presence or absence of the segment is the toggle, the same semantics as any omitted flag meaning "no constraint on that dimension."

The universe qualifier is positional rather than a `--universe`/`--world` flag because, unlike `--provider`, it changes the schema of what comes back, not only which rows match. Universes share the four core fields (provider, price, lifecycle, a tiering concept) and nothing else, so an unscoped query can only render the core columns, plus a universe column labeling each row; a scoped query gets the full per-universe schema (reasoning shape, modalities, and so on for `ai`). Precedent: `aws s3 ls` vs. `aws ec2 describe-instances` puts the service first and positional because the verbs and shape differ per service; `kubectl get pods -n foo` puts namespace as a flag because the shape doesn't change, only the row count. Universes are the former case.

A `--world` flag alongside the positional segment was considered and rejected: two spellings for the same constraint, plus an undefined answer to what `rates ai list --world cloud` means. One mechanism only.

### Matching: exact by default, contains by explicit spelling

The precedent is SQL, where determinism lives in the operator, not the verb: `WHERE model = 'x'` and `WHERE model LIKE '%x%'` are both deterministic, the caller stated which predicate they wanted. So `filter` and `search` aren't "the exact command" and "the fuzzy command," they're one operation whose match mode is chosen per field:

- `--model claude-opus-5` / `.filter(model="claude-opus-5")`: case-insensitive exact match. Returns that model or nothing. Safe to wire into a script that feeds the result into an API call.
- `--model-contains opus` / `.filter(model_contains="opus")`: substring, visibly opted into. Returns every opus-family model.
- `rates ai search "opus"`: the `*_contains` predicate pre-selected across the human-facing name fields, nothing more.

Nothing broadens silently. A caller who wired `results[0]` into an LLM call and typed `--model opus` gets zero results and a clear miss, never a silent substring expansion that hands them the wrong (pricier, non-reasoning) model. The rejected alternative was Google Ads' trajectory, phasing out exact match in favor of phrase match, which serves the platform's reach, not the caller's precision, the opposite of what a registry feeding programmatic API calls needs.

Result cardinality was examined and dismissed as the filter/search distinction: `filter --price-max 3` returning twelve models is just as deterministic as an exact-ID lookup returning one. Match semantics is the axis, not how many rows come back.

### Numeric comparisons: paired flags, no operator expressions

`--price-max 3.00` and `--price-min 0.50` cover at-most, at-least, and between, mirroring `.filter(price_max=3.00, price_min=0.50)`. Currency is always an ISO code (`--currency USD`), never a symbol, since `$` is ambiguous across USD, CAD, AUD, and others.

An inline operator syntax (`rates ai search provider=anthropic price<=3.00 model=%opus%`) was considered and rejected on two grounds:

1. **The shell fights the useful characters.** `<` and `>` are redirection operators, `$` starts variable expansion, so every comparison predicate needs quoting, which erases the ergonomic win over flags. Tools using this style either stop at equality (`docker ps --filter status=running`, `kubectl --field-selector`) or wrap the whole expression in one quoted string with a full query-language parser behind it (`gcloud --filter="..."`).
2. **A custom expression syntax is a second language to own.** Tokenizer, precedence, error messages, documentation, and no shell tab-completion, versus flags, which get validation, `--help`, and completion free from the argument parser, and which stay one-to-one with the Python API instead of drifting into a vocabulary the code surface doesn't accept.

If a predicate ever proves inexpressible as flags, the additive path is a single quoted `--query "..."` argument, gcloud-style, added alongside the flags. Nothing on the roadmap until someone names such a predicate; none is currently known.

### A composed example

```bash
rates ai filter --provider anthropic --model-contains opus --price-max 5.00 --limit 10
```

```python
rates.ai.load().filter(provider="anthropic", model_contains="opus", price_max=5.00)
```

Anthropic models with opus in the name, at most $5/mtok input, first ten. Every constraint explicit, and the result is whatever the facts return: zero, one, or many, with no view from `rates` on which of them to pick.

### Notes for future universes

Decisions rejected here that may still apply elsewhere:

- **Identity vs. display name.** Every AI-universe identity field is a slug by construction (`anthropic/claude-opus-5`), so spaces in filter values never arise. A universe with human-readable filterable entities (retail's "Tesco Extra") should split the field: a slug for exact scriptable matching (`store_id: "tesco-extra"`), a display field for the readable name (`store_name: "Tesco Extra"`), never one field doing both jobs.
- **The `--query` expression language** stays available as the pressure valve if some universe's axes (cloud's region × commitment-type × durability matrix, say) outgrow flat flags.
- **Per-universe columns in unscoped output** need no design work per universe: unscoped queries render core fields plus the universe label, and that holds for any number of universes automatically.

## Update cadence

The `ledger` refreshes weekly (52 releases a year), each one a dated, versioned JSON file. Drift detection (a source changing a field's shape, a model disappearing from an upstream feed, a price moving without a corresponding release) runs as part of that cycle, not as a manual check, and doubles as the schedule for re-verifying the capability-drift probes above.

---

By [Mo Shehu](https://mohammedshehu.com)
