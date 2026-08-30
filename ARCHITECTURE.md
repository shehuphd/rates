# Architecture

## What this document covers

`rates` is a pricing registry and decision layer. This document covers the two decisions behind everything else: which data a record stores, and how the project is structured so more than one pricing domain can fit. The concrete data model lives in [ERD.md](ERD.md); this document is the reasoning behind it.

## What a record stores

`rates` exposes verified facts about a model: what it costs, what it accepts, what it produces, whether it's still viable to build on. It does not rank, score, or editorialize. No field in the schema encodes an opinion about which model is "best," "quality," or "recommended" for a given task, that judgment belongs to the caller, who knows their own budget and workload in a way a registry never can.

The clearest way to hold this line: think of a model as a food product. The label states the maker, the category, the ingredients, the dimensions, the price, and the dates, all verified facts. It does not print "best peanut butter in the aisle" on the front, that's the manufacturer's opinion, not a fact about the product. `rates` is the label, not the marketing copy.

This ruled out a design we built and then discarded: a four-tier `dev`/`bulk`/`economy`/`quality` taxonomy, assigned per model. It failed for two concrete reasons, not just on principle:

- **The labels claimed things price alone can't establish.** `dev` implied a model wasn't fit for production, but Claude Haiku 4.5 runs at production volume for classification and extraction work today. `quality` implied "best available," but price-band logic alone would file aging, expensive models (`claude-3-opus-latest`, `gpt-4-32k`) as `quality` just because they cost a lot, not because they're still competitive.
- **Making the labels correct required permanent hand-curation** (a maintained list of which models count as "frontier"), which doesn't scale across a catalog in the thousands and drifts stale the moment someone forgets to update it.

Instead: expose the raw axes (price, context, modality, reasoning capability) as data, and let `Registry.filter(...)` be a query over that data, driven by whatever the caller asks for. Nothing stored on a model record asserts an opinion about it.

Timestamps split by what they represent. A source `fetched_at` and a record's `observed_at` are events, carried as UTC instants so a fast-moving domain can date a value to the second; an older ledger's day-granular value reads as that day's midnight UTC. A release's `snapshot_date` and a provider's announced `release_date`/`deprecation_date` are calendar facts, carried as dates, because a daily-published snapshot and an announced launch day have no sub-second meaning to invent.

## A small core, per-domain extensions

`rates` is structured as a small core that every domain implements, plus a per-domain extension that owes nothing to any other domain's shape. One domain is registered today, `ai`; the split is what the unscoped query surface and the core vocabulary run on. Different pricing domains have different axes entirely, not variations on one schema with fields renamed, which is why the core stays this small.

**Core (every domain implements this; unscoped queries see only this):**
- Provider/vendor identity, and an `id` for the record (each domain maps its own word onto `id`: "model" for AI, "service" for cloud, "device" for quantum)
- Price, as an amount, a unit, and a currency, never collapsed into one blended number (see [ERD.md](ERD.md) for why)
- Record type/category
- A tiering *concept* (cheap-to-expensive exists everywhere), with no shared label set or thresholds, each domain defines its own
- Lifecycle (products get deprecated and retired in every domain, not only AI)
- `observed_at`, a UTC instant for when a value was observed, present on the record for a domain whose prices move continuously; null in the AI domain, whose prices are announced

The core contract is an explicit `Record` protocol (`rates._record`) in neutral vocabulary; each domain's record class satisfies it structurally, without inheriting from it, so cross-domain code depends on the contract and never on one domain's class. Which domains exist, and the per-domain facts shared logic reads, how to load a domain, which access tiers it supports, when its data goes stale, live in one domain registry (`rates._domains`), so adding a domain is a spec entry, not an edit to the loader or the query code. A domain declares its access tiers there: a domain whose data can't ship as a snapshot (a market price, or one a source's terms forbid redistributing) offers `live` only, and a bare read fails with a clear message instead of serving a snapshot that shouldn't exist.

**AI domain (the extension):**
- Model type/purpose, modality support, reasoning capability and its shape, context limits, tool/structured-output support

Nothing in the AI domain's extension is part of the core; the boundary is enforced by the `Record` protocol and the domain registry, and in the CLI's vocabulary (see CLI shape below) as well as the schema.

## Why the AI domain fuses several sources instead of consuming one

No single existing source covers what this domain needs:

| Source | Strong for | Missing |
|---|---|---|
| [genai-prices](https://github.com/pydantic/genai-prices) | Broadest provider and model-type coverage (36 providers, all model types including non-chat) | No type, modality, or reasoning fields at all |
| [LiteLLM](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) | Native `mode` field (model type), wide capability booleans, per-second video pricing | Reasoning-effort levels are sparse and inconsistently populated |
| [OpenRouter](https://openrouter.ai/api/v1/models) | Ordered reasoning-effort vocabulary, modality arrays | Chat-completion models only, no embedding/image/audio/moderation coverage at all |
| [models.dev](https://github.com/anomalyco/models.dev) | Richest single per-model schema (family, modalities, typed reasoning options, lifecycle status, split context/output limits) | No type/mode field; gaps in specific categories (no pricing at all for Veo video models as of this writing) |
| [Hugging Face Hub](https://huggingface.co/docs/hub/api) | Open-weight model task type and provenance | Not a pricing source; only useful for cross-referencing open-weight models |

**models.dev is `rates`' preferred ingestion source**; genai-prices, LiteLLM, and OpenRouter fill gaps and cross-validate. "Preferred," not "authoritative": none of the four is the provider itself, so none of them gets to be treated as automatically right when they disagree, see § Resolving price disagreements below for what "preferred" governs. Hugging Face was evaluated for open-weight cross-referencing and isn't consumed; no open-weights field ships ([ERD.md](ERD.md) § Excluded fields). `rates` still exists as an independent project on top of this rather than simply re-exporting models.dev, for reasons distinct from data quality:

1. **Provenance.** models.dev's own update history is bot-synced from other catalogs roughly hourly (`opencode-agent[bot]`), not independently human-verified per field. `rates`' records each list the sources that contributed to them and when each was fetched, and [ERD.md](ERD.md) maps every field to the source that supplies it, so a caller can see where a value came from, not just what it is.
2. **History.** models.dev serves only its latest snapshot. `rates` ships dated, versioned JSON releases, so "did this model's price change since March" is answerable by diffing two releases, not lost the moment the source overwrites its own data.
3. **Scope.** models.dev is an AI-model database. `rates` is a pricing framework whose core is domain-neutral, with AI as one domain inside it, a different project shape entirely.
4. **Control.** A registry this central to how a caller picks and pays for a model should be owned outright rather than depend on a third party's schema decisions and update cadence.

## Admission criteria: what it takes for a record to appear

The registry's scope is defined by a completeness bar, not by any single source's catalog. Every criterion is checkable mechanically, and the fusion applies them automatically, with no hand-curation:

1. **Identity** (every record). A provider id and model id, matched across whatever sources know it, never fuzzy-guessed.
2. **Price** (every record). At least one per-unit rate. A record in a pricing registry that can't answer any price question serves nobody, so listings with no per-unit pricing at all (subscription and platform catalogs, mostly) are excluded, and each build logs how many it excluded. An explicit zero rate is a carried value and survives; free models stay in.
3. **Type** (fallback-admitted records only). What the API contract does (chat, embedding, rerank, ...). On the corroboration route, type is what determines which price units to demand, so it's required there. Preferred-carried records ship with type where a source supplies it and without it where none does; the preferred source itself carries no type field, so coverage is partial by nature, and `rates ai info` reports it.
4. **Lifecycle** (every record). A status, even if only active-by-absence-of-deprecation.
5. **Corroboration** (every record). Carried by the preferred source, or by at least two independent fallback sources agreeing on price within the discrepancy threshold. Two independent agreeing sources is more verification than one preferred-source listing, not less, which is what lets a model the preferred source hasn't cataloged yet appear without lowering the bar.

Records admitted through the two-fallback route are visibly thinner (their `sources` field never lists the preferred source, and fields only the preferred source carries stay absent), so a caller who wants preferred-source-backed records only can filter by provenance.

**Type coverage comes from exact-id matching, never inference.** The preferred source has no type field, so type arrives from LiteLLM's `mode`, matched first by provider and id, then by bare model id where every LiteLLM listing of that id agrees on one mode (a reseller lists the same model under the same id; type is a property of the model, not the listing), then by OpenRouter membership (its catalog is chat-completion models only, so listing an id implies `chat`). An id whose listings disagree stays untyped rather than guessed, and no type is ever derived from modalities or price units, the rerank/moderation counterexample in [ERD.md](ERD.md) is why. The same bare-id route, with the same all-listings-agree gate, fills OpenRouter's reasoning enrichment (`effort_parameter_required`, default effort) for models it lists under other providers.

## Resolving price disagreements: freshness first, then a fallback preference

The preferred source doesn't win a price disagreement by declaration. It's the default when nothing else can decide, not a claim that it's automatically right. That distinction stopped being academic on 2026-08-24: OpenAI cut GPT-5.6 Sol's price, LiteLLM's community shipped the new number within two days, and models.dev's per-model file for that record still hadn't moved. A rule that always ships the preferred source's value regardless of how current it is would have shipped the wrong price with a straight face.

**Freshness decides first, when it's available.** Every source's underlying data carries a "last touched" date, readable from the same commit history the fusion already treats as ground truth for that source. When two sources disagree on a price, the one whose data changed more recently wins for that record, not the one declared preferred. Two of the three price-carrying sources (LiteLLM, genai-prices) are single bulk files, so freshness there is source-wide, one check per fusion run tells us when the whole dataset last moved. models.dev is per-model files, so its freshness is per-record, checked lazily and only for records that already show a price disagreement, riding on work the fusion is doing anyway rather than scanning the full catalog.

**A plain, editable preference order settles it when freshness can't.** A source unreachable at fusion time, or two sources' data tied on the same date: the tie goes to a per-field order (starting point: models.dev, then LiteLLM, then genai-prices for price), set by us and revised as evidence accumulates, never treated as permanent. This is a fallback, not the default mechanism; freshness is checked first because a fixed order is the assumption that broke on the Sol price cut.

**Never blocks a result.** A `live()` caller whose network can't reach a freshness check doesn't fail the whole call over a tiebreak detail, it falls straight through to the preference order, the same "a quality signal failing costs nothing but that signal" pattern `sync` already follows for its own freshness check.

**This adds no new source to the schema.** The freshness check doesn't contribute price, type, or any field to a record, it only informs which of the *existing* four sources' price value gets chosen when they disagree. Which mechanism decided travels with the record: `price_discrepancy.resolved_by` is `"freshness"` or `"preference"` (see [ERD.md](ERD.md) § `PRICE_DISCREPANCY`).

**Caching.** Each source's last-known commit date is cached for an hour, so repeat calls and repeat records within one fusion run don't refetch. Confirmed live (2026-08-24): the commit-history feed used for freshness carries none of `api.github.com`'s `x-ratelimit-*` headers, it's served from a separate, undocumented budget on GitHub's web frontend, not the REST API's 60/hour unauthenticated or 5,000/hour token-authenticated core limit that `sync` and `live` already use. Caching is the mitigation regardless of where that undocumented ceiling falls.

## `bundled`, `stable`, and `live`: three ways to get data, one fusion engine

Rates are durable. Once a provider sets a price, it typically holds for a long while, AI API pricing especially, cloud pricing similarly, both usually announced well ahead of taking effect. That fact drives the whole distribution model: the common case isn't "refetch everything," it's "confirm nothing changed," and only doing the expensive work when it did.

- **`bundled` (the default).** The best snapshot already on this machine: the dated, versioned JSON ledger shipped inside the installed package, or a newer one a prior `stable` fetch already downloaded and cached. Zero network calls, ever. This is what makes `rates` usable in an air-gapped environment with no configuration, `pip install rates` and it works.
- **`stable` (a cheap freshness check).** No bespoke manifest file, GitHub's own Releases API already is one: each ledger publishes as a GitHub Release, so listing recent releases (`GET /repos/shehuphd/rates/releases`) and taking the newest one carrying a ledger asset answers "is there something newer" for free, nothing to hand-write or keep in sync during the publish step (the newest release overall can be a package release, so the check walks past those). If it matches what's already on hand, done, milliseconds. If it doesn't, only then is the newer published ledger's asset pulled down, still our own already-fused output, not a re-fetch of all four raw sources.
- **`live` (opt-in, the heavy option).** The full fusion function (models.dev preferred, gaps filled from genai-prices/LiteLLM/OpenRouter, per-record source attribution), run by the caller's own process against the four raw sources directly, bypassing anything we've published. This is the exact same function we run ourselves to produce each ledger release, exposed as a public capability rather than kept as internal tooling, so nobody has to trust our merge logic without being able to run it themselves.

`stable` and `live` are both strictly opt-in, never triggered implicitly. A caller who calls neither never causes `rates` to make an outbound request; for anyone in a restricted network (a defense environment, an air-gapped facility), that guarantee counts as much as the features themselves, and it holds even if they forget to check a setting.

**`live` caches for the length of a session, not per call.** The first `live` call in a process fetches and merges fresh, then stores the result in the cache directory; every subsequent read in that same run reuses it. The cached result expires after 24 hours, since data that old no longer meaningfully qualifies as `live`, and is ignored when a different `rates` version with an incompatible schema wrote it.

**`stable` keeps what it downloads, and `bundled` benefits from it too.** A fetched ledger is stored in the cache directory and counts as local data from then on, so a repeat `stable` serves the earlier download after one small freshness check instead of fetching the asset again; `bundled`'s own staleness check reads that same cached snapshot when it's newer than the one installed with the package, so a successful `stable` fetch resets what `bundled` warns against too. Freshness compares snapshot dates, not publish dates: the release tag carries the ledger's own snapshot date (`ledger-YYYY-MM-DD`), so a ledger built one day and released the next never looks newer than its own content. Every fallback branch, check failed, nothing newer, schema too new, still runs the staleness check on whatever is served.

**Caches live in `~/.cache/rates`, a per-user directory created `0700`**, never under a predictable name in the world-shared temp root, where another local user could pre-create the file and feed fabricated prices to every process that reads it.

**Staleness is self-reported even fully offline.** The `bundled` tier's best local snapshot carries its own `snapshot_date`, comparable against today's date with no network call at all. Each domain sets its own staleness threshold, since "too old" means something different per domain; the AI domain's is 4 weeks. Past the threshold, `rates` surfaces a visible warning naming how stale the data is and how to fix it, pointing directly at `live` (or `stable`, if network access exists but a full independent fusion isn't wanted), rather than silently continuing to serve an old snapshot as if it were current.

**Partial source failure is a first-class case for `live`, not an edge case.** One of four sources being slow, rate-limited, or returning a changed schema is the normal operating condition for an open-internet call, not a rare failure to special-case later. The fusion function degrades: a source that can't be reached is skipped, noted as unreachable in that response's `sources` metadata, and the other three still return a usable result. It never hard-fails a whole request because one of four sources had a bad moment, and it never silently drops a field without saying which source was supposed to supply it.

**Schema drift in the sources themselves gets watched, not assumed away.** Each of the four upstream sources can change shape without warning (a field renamed, a value vocabulary that changes, an endpoint that starts returning something new). The test suite carries live probes against each source's current schema, not mocks frozen at whatever shape a source had when the normalizer was written, so a source changing underneath us fails a test that names which source and which field, rather than silently degrading `live` results with no one noticing.

### Reliability specifics

Settled by stress-testing the design against realistic bad conditions, where independently-rare failures overlap, not by picking numbers that sounded reasonable in isolation:

- **Timeouts are generous, and the slow connection is the design case, not the edge case.** Up to three attempts per URL on an escalating ladder, 30s, 60s, 120s; a caller timeout replaces any rung below it, capped at a 300-second ceiling (`rates.ai.load(fetch="live", timeout=180)`), above which the value is rejected with a clear error rather than clamped. The ladder starts generous rather than small deliberately: a short first attempt would save a fast-broadband user a few seconds on a rare blip while guarantee-failing the user on desert wifi, a satellite link, or a congested mobile network, who is who these numbers are for.
- **Server-side transient failures retry with backoff; clean errors never do.** A 429, 500, 502, 503, or 504 resolves on the server's schedule, so it gets the same up-to-three attempts with short exponential pauses between (1s, then 2s), honoring a `Retry-After` header when the server sends one, capped at 60 seconds so a hostile or broken header can't stall the caller. A 404, a 400, or malformed JSON never retries, no wait fixes those. The ladder and the status list are per-domain constants; the retry mechanism itself is shared.
- **All duration checks anchor to UTC, never local wall-clock time.** The 24-hour `live` cache expiry and the staleness-vs-`snapshot_date` check both use `datetime.now(timezone.utc)`. A system's local timezone shifting mid-session (a flight crossing zones) must never change when a cache is considered stale.
- **`stable` never raises, and never fails silently either.** Unlike `live`, which explicitly promises fresh-or-nothing, `stable` promises something weaker: a cheap check, best-effort. Any failure to complete that check (GitHub's API rate-limited, GitHub down, a bad connection) falls back to the local ledger with a visible warning saying the check couldn't complete and what data is being served instead, never a blocking exception, a feature meant purely as a convenience shouldn't be able to take down a caller's program, and never a wordless fallback the caller can't see happened.
- **GitHub's unauthenticated API rate limit is 60 requests/hour, per IP, confirmed live against `api.github.com`.** That's shared across everyone on the same IP, which bites hardest in CI: GitHub Actions' hosted runners share IP ranges across every customer on the platform, not just `rates` users, so a `stable` call can get rate-limited by traffic that has nothing to do with `rates` at all. Mitigation that costs nothing: both `stable` and `live` check for a `GITHUB_TOKEN` environment variable, already set in every GitHub Actions job by default, and use it as a bearer token when present, raising the effective limit to 5,000/hour with no configuration from the caller.
- **A `stable`-triggered follow-up fetch (when something did change) is not "cheap."** The manifest check is small and fast; pulling the actual updated ledger asset is multi-megabyte, same as `live`'s fetches, and inherits the same configurable timeout, not a separate hardcoded assumption that it'll always be quick.
- **`REGISTRY.schema_version` is checked before parsing, not after.** A `rates` install pinned for a long time, combined with a schema that evolved a breaking change in the meantime, means a freshly-fetched `stable`/`live` result can be a shape the installed dataclasses don't understand. Checked against a compatibility rule before parsing, with a specific message ("this ledger needs a newer `rates`, run `pip install -U rates`"), rather than an opaque failure partway through building a `Model`.

**Exception shape**, distinct types for distinct situations, so a caller can catch broadly or specifically:

```python
class RatesError(Exception): ...              # base for everything rates raises
class LiveFusionError(RatesError): ...         # base: a fetch="live" call couldn't produce a result
class AllSourcesUnreachableError(LiveFusionError): ...   # every source failed
class PreferredSourceUnavailableError(LiveFusionError): ...  # models.dev specifically failed; even
    # with the other three healthy, the result would be missing most fields per ERD.md's source
    # map, refused rather than returned looking successful
```

`stable` never raises any of these, per the point above, `live` does, since it's the mode that explicitly promises a result or an explanation of why not.

## Public API shape

- `rates.ai` is the namespace for this domain. `rates.ai.load(...)` is the entry point, returning a `Registry`, not a bare list, so the envelope metadata (`schema_version`, `snapshot_date`, `sources` and their `status`) travels with the models rather than being discarded.
- One entry point for all three access tiers, picked by a single `fetch` parameter rather than a combination of flags: `load()` reads the best local snapshot (`fetch="bundled"`, the default), `load(fetch="stable")` runs the freshness check, `load(fetch="live")` runs the full fusion. All three return the same `Registry`, so downstream code never cares which path produced it; a single parameter means the invalid state (two tiers at once) has nowhere to live, not a case to guard against.
- Warnings (staleness, a stable check that couldn't complete, a live fusion that ran with a non-preferred source unreachable) surface through Python's `warnings` module with typed categories under a `RatesWarning` base (`StaleLedgerWarning`, `SyncFallbackWarning`, `SourceUnreachableWarning`), so a caller can filter, silence, or escalate them with stdlib machinery, `warnings.simplefilter("error", StaleLedgerWarning)` turns staleness into a hard failure for whoever wants that.
- No HTTP client dependency: `stable` and `live` run on a small stdlib (`urllib`) helper, so the entire package installs with zero dependencies and live checking works out of the box, opt-in by calling it, nothing extra to install.
- `Registry.filter(...)`, not `recommend(...)`. `recommend` implies a judgment call about which model is right; `filter` narrows a set by facts the caller supplied (budget, required modality, reasoning capability, still active) and can return zero, one, or many matches, `rates` has no view on which of those is best.
- Sorting is a separate chained call, `Registry.filter(...).sort_by(field, descending=...)`, never a `filter()` parameter. They're different jobs (narrowing vs. ordering), and a caller may want to sort without filtering at all. `descending` is always stated explicitly, no field defaults to a "good" direction, that would be another opinion smuggled into the interface.
- Model records are stdlib `dataclasses`, not Pydantic. Pydantic would land as a mandatory dependency on the default `ledger` install path, not just `live`, breaking the zero-dependency, air-gapped-safe promise for the common case.

### Alias facts: a build-time dependency that never reaches an installed package

A model's own `id` is sometimes a rolling reference (`gemini-pro-latest`) rather than a dated snapshot, and what that implies for callability differs by provider: Gemini and DeepSeek keep such aliases aimed at a live model and date nothing, while OpenAI's `-chat-latest` family was observed going stale wholesale on 2026-08-10. Classifying this correctly needs per-provider convention evidence with dated drift probes, knowledge [KeyCall](https://pypi.org/project/keycall) already owns; duplicating it inside `rates` would be a second, unverified copy of the same classifier.

Rather than add KeyCall as a runtime dependency, `scripts/build_ledger.py` (a maintainer/CI script, not part of the installed package) calls KeyCall's keyless `alias_fact(provider, model_id)` once per model while building the ledger, and bakes the result into the record as `alias` (see ERD.md § ALIAS). The fact stays with the record from then on: a plain `pip install rates` never imports KeyCall, and `fetch="live"`'s in-process fusion (which never touches the ledger-build pipeline) simply carries no `alias` field, same as any other build-time-only enrichment. Staleness of the baked fact is KeyCall's problem, not `rates`': its own drift probes failing in its release gate are what trigger a refresh, and the next scheduled ledger build picks up the new classification.

## CLI shape

One primitive, `filter`, with three presets over it. Domain scoping is positional. Matching is exact by default, broadened only by explicit spelling. Flags mirror the Python API one-to-one, so the CLI and the code share a single vocabulary.

### One primitive, three presets

`list`, `search`, and `show` exist because they match how people think, not because they do anything `filter` can't:

| Command | Equivalent filter | For |
|---|---|---|
| `filter` | itself | Narrowing by any schema fields |
| `list` | `filter` with no constraints, a default limit, table output | "Show me what's in here" |
| `search <phrase>` | `filter` with contains-matching preset across name fields | "I don't know the exact ID" |
| `show <id>` | `filter` narrowed to one exact identity, full-detail output | "Tell me everything about this one" |

### Domain scoping is positional, not a flag

`rates ai list` scopes to the AI domain; `rates search "opus"` runs unscoped across every domain. Presence or absence of the segment is the toggle, the same semantics as any omitted flag meaning "no constraint on that dimension."

The domain qualifier is positional rather than a `--domain`/`--world` flag because, unlike `--provider`, it changes the schema of what comes back, not only which rows match. Domains share the four core fields (provider, price, lifecycle, a tiering concept) and nothing else, so an unscoped query can only render the core columns, plus a domain column labeling each row; a scoped query gets the full per-domain schema (reasoning shape, modalities, and so on for `ai`).

Vocabulary follows the same boundary: the core layer never borrows one domain's words. Every domain has an identity field, but only the AI domain calls its items "models" (quantum sells qubit-time by provider, cloud sells services), so unscoped queries speak the neutral name, `--id`/`--id-contains` and an ID column, each domain mapping it onto its own criterion; `--model` exists only inside `rates ai ...`.

The general rule: **specificity unlocks a domain's full vocabulary; ambiguity falls back to what all domains share, and says so.** Unscoped queries show and sort core fields only, in one global sort over the pooled rows (never per-domain blocks concatenated, which would rank a cheaper row below a pricier one purely by iteration order), with a notice on stderr, keeping stdout pipe-clean, naming the fallback and how to scope. The one place fallback would be dishonest is the sort field: an unscoped `--sort-by price.input_mtok` has no core substitute that honors the request, so it errors with the scoping hint instead of silently sorting by something else. Precedent: `aws s3 ls` vs. `aws ec2 describe-instances` puts the service first and positional because the verbs and shape differ per service; `kubectl get pods -n foo` puts namespace as a flag because the shape doesn't change, only the row count. Domains are the former case.

A `--world` flag alongside the positional segment was considered and rejected: two spellings for the same constraint, plus an undefined answer to what `rates ai list --world cloud` means. One mechanism only.

### Matching: exact by default, contains by explicit spelling

The precedent is SQL, where determinism lives in the operator, not the verb: `WHERE model = 'x'` and `WHERE model LIKE '%x%'` are both deterministic, the caller stated which predicate they wanted. So `filter` and `search` aren't "the exact command" and "the fuzzy command," they're one operation whose match mode is chosen per field:

- `--model claude-opus-5` / `.filter(model="claude-opus-5")`: case-insensitive exact match. Returns that model or nothing. Safe to wire into a script that feeds the result into an API call.
- `--model-contains opus` / `.filter(model_contains="opus")`: substring, visibly opted into. Returns every opus-family model.
- `rates ai search "opus"`: the `*_contains` predicate pre-selected across the human-facing name fields, nothing more.

Nothing broadens silently. A caller who wired `results[0]` into an LLM call and typed `--model opus` gets zero results and a clear miss, never a silent substring expansion that hands them the wrong (pricier, non-reasoning) model. The rejected alternative was Google Ads' trajectory, phasing out exact match in favor of phrase match, which serves the platform's reach, not the caller's precision, the opposite of what a registry feeding programmatic API calls needs.

Result cardinality was examined and dismissed as the filter/search distinction: `filter --price-max 3` returning twelve models is just as deterministic as an exact-ID lookup returning one. Match semantics is the axis, not how many rows come back.

### Numeric comparisons: paired flags, no operator expressions

`--price-max 3.00` and `--price-min 0.50` cover at-most, at-least, and between, mirroring `.filter(price_max=3.00, price_min=0.50)`. A price bound always names its billing unit explicitly (`--price-unit input_mtok` / `price_unit="input_mtok"`), never defaults to one: models bill on fundamentally different units (`input_mtok`, `output_per_second`, `requests_kcount`), and a default would be a silent assumption about which one the caller meant. A bound without a unit raises, and the error lists the units present in the data itself (`Registry.price_units()`), so the fix is in the message, not the docs. Currency is always an ISO code (`--currency USD`), never a symbol, since `$` is ambiguous across USD, CAD, AUD, and others.

An inline operator syntax (`rates ai search provider=anthropic price<=3.00 model=%opus%`) was considered and rejected on two grounds:

1. **The shell fights the useful characters.** `<` and `>` are redirection operators, `$` starts variable expansion, so every comparison predicate needs quoting, which erases the ergonomic win over flags. Tools using this style either stop at equality (`docker ps --filter status=running`, `kubectl --field-selector`) or wrap the whole expression in one quoted string with a full query-language parser behind it (`gcloud --filter="..."`).
2. **A custom expression syntax is a second language to own.** Tokenizer, precedence, error messages, documentation, and no shell tab-completion, versus flags, which get validation, `--help`, and completion free from the argument parser, and which stay one-to-one with the Python API instead of drifting into a vocabulary the code surface doesn't accept.

### A composed example

```bash
rates ai filter --provider anthropic --model-contains opus --price-max 5.00 --price-unit input_mtok --limit 10
```

```python
rates.ai.load().filter(
    provider="anthropic", model_contains="opus", price_max=5.00, price_unit="input_mtok"
)
```

Anthropic models with opus in the name, at most $5 per million input tokens, first ten. Every constraint explicit, and the result is whatever the facts return: zero, one, or many, with no view from `rates` on which of them to pick.

### Machine output and registry introspection

`--json` on any query emits records in the ledger's own JSON shape, the same format the published releases and ERD.md use, so the CLI, the Python API, and the data files speak one format and `rates ai filter ... --json | jq` needs no translation layer. `--no-header` strips the table header for `awk`/`cut` pipelines; tables truncate to the terminal width only when stdout is a terminal, piped output is never touched. `rates [domain] info` reports the registry itself: snapshot date and age against the staleness threshold, model and provider counts, and a one-line summary of whether the sources could all be reached.

### Tab completion and tracing

Completion is hand-rolled on the shell's own protocol, no dependency: `rates completion bash|zsh|fish|powershell` prints the few registration lines (PowerShell registers through `Register-ArgumentCompleter -Native`; cmd.exe has no argument-completion hook, so PowerShell is the Windows surface), and a hidden `rates __complete` prints candidates. Value positions complete from the data itself, `--provider <TAB>` lists the providers in the registry, `--price-unit <TAB>` the units, `show <TAB>` the model identities, the same data-driven principle as the error messages. Candidates come from a cache in the same per-user `~/.cache/rates` directory, keyed on the bundled ledger's file stat, so a repeat TAB costs roughly interpreter startup.

Tracing is a soft dependency on [traceact](https://github.com/traceact/traceact): installed alongside `rates`, key operations (CLI runs, loads, live fusions) record traces; absent, a no-op shim stands in and nothing changes. The CLI routes traces to `~/.traceact/rates.jsonl` only when the surrounding app hasn't configured its own sinks; the Python API never touches tracing configuration at all.

---

By [Mo Shehu](https://mohammedshehu.com)
