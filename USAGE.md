# Usage

`rates` is a pricing registry and decision layer. It answers "what does this model cost, what can it do, and is it still viable to build on" from your terminal or your code, without a browser.

This manual covers the CLI first, then the Python API, then the data itself. Every command and recipe here was run before it was written down.

## Before you start

`rates` needs Python 3.10 or newer. Check with:

```bash
python3 --version
```

If that fails, install Python from [python.org/downloads](https://www.python.org/downloads/) (or `apt install python3-pip` / `dnf install python3-pip` on Linux).

## Install

```bash
pip install rates
```

That's the whole setup. `rates` has zero dependencies and bundles its own data snapshot, so it works immediately, offline included.

## See it work

```bash
rates ai list
```

```
PROVIDER  MODEL         TYPE  IN $/MTOK  OUT $/MTOK  STATUS
302ai     MiniMax-M1    chat  0.132      1.254       active
302ai     MiniMax-M2    chat  0.33       1.32        active
302ai     MiniMax-M2.1  chat  0.3        1.2         active
...

20 of 7109 shown (--limit 0 shows all)
```

Everything about one model:

```bash
rates ai show anthropic/claude-haiku-4-5
```

```
anthropic/claude-haiku-4-5
  family: claude-haiku
  type: chat
  status: active
  released: 2025-10-15
  deprecated: 2026-10-15
  modalities in: text, image, pdf
  modalities out: text
  context in: 200000
  context out: 64000
  tool call: True
  structured out: True
  price (USD):
    cache_read_mtok: 0.1
    cache_write_1h_mtok: 2
    cache_write_mtok: 1.25
    input_mtok: 1
    output_mtok: 5
  reasoning: budget_tokens
    budget: at least 1,024 tokens
    can disable: False
  sources: litellm, models_dev
```

## The CLI

### Domains

Queries are scoped by domain; `ai` is the one domain today. Naming it (`rates ai list`) unlocks its full fields. Leaving it out (`rates search "opus"`) runs on the core fields every domain shares (provider, id, type, status, rate), labels each row with its domain, and prints a notice on stderr saying how to scope for more. RATE shows each record's own price in its own domain's units (`$0.132/input_mtok, $1.254/output_mtok` for an `ai` row), not a number stripped of what it means.

Bare `rates` or bare `rates ai` prints a welcome with example commands. Every command takes `--help`.

### The five commands

| Command | What it does |
|---|---|
| `rates ai list` | The catalog, twenty rows at a time |
| `rates ai filter --provider anthropic ...` | Narrow by any schema fields |
| `rates ai search "opus"` | Substring match across name fields |
| `rates ai show anthropic/claude-opus-5` | Everything about one model |
| `rates ai info` | The registry itself: snapshot date, sources, freshness |

`list`, `search`, and `show` are conveniences over `filter`: `list` is `filter` with no constraints, `search` is `filter` with substring matching preset across the name fields, and `show` is `filter` narrowed to one identity with full-detail output. `show` accepts `provider/model` or a bare model id, and takes only the data-tier flags (`--json`, `--fetch`, `--timeout`): it renders one record whole, so the query flags don't apply.

### Filtering

Bare flags match the whole value, case-insensitively. The `-contains` spelling matches a substring instead; nothing matches more loosely than you asked.

```bash
rates ai filter --model claude-opus-5          # this model or nothing
rates ai filter --model-contains opus          # every opus-family model
```

| Flag | Matches on |
|---|---|
| `--model` / `--model-contains` | The model id |
| `--provider` / `--provider-contains` | The provider |
| `--family` / `--family-contains` | The lineage grouping (e.g. `claude-opus`) |
| `--type` / `--type-contains` | What the API contract does: `chat`, `embedding`, `image_generation`, `audio_transcription`, ... Type coverage is partial (`rates ai info` reports it), and untyped models never match a type filter |
| `--currency` | ISO code, e.g. `USD` |
| `--status` | `active`, `beta`, or `deprecated` |
| `--modality-input` / `--modality-output` | A content format the model accepts/produces: `text`, `image`, `audio`, ... |
| `--tool-call` / `--structured-output` | `true` or `false`; models where the fact is unknown match neither |
| `--price-min` / `--price-max` | Price bounds, always with `--price-unit` |
| `--price-unit` | Which billing unit the bounds compare against |

Price bounds always name their unit, because models bill on different units (`input_mtok`, `output_per_second`, `requests_kcount`):

```bash
rates ai filter --provider anthropic --price-max 3 --price-unit input_mtok
```

A bound without a unit is an error that lists the units present in the data.

Flags combine with AND semantics. Unscoped queries take the shared flags plus `--id`/`--id-contains`, the domain-neutral name for the identity field.

### Sorting and limits

```bash
rates ai filter --type chat --sort-by price.input_mtok --ascending
```

`--sort-by` takes a catalog field (`model`, `provider`, `family`, `type`; `id` is `model`'s domain-neutral spelling) or a price unit as `price.<unit>`; structured fields (modalities, reasoning, the whole price map) have no meaningful order, and asking for one gets an error naming the sortable set. It always needs `--ascending` or `--descending`: no field has a default direction. Models without the field sort last either way. Unscoped queries sort on the shared fields (`domain`, `provider`, `id`, `currency`, `status`); asking for a domain field unscoped is an error that says how to scope.

`--limit` defaults to 20; `--limit 0` shows everything.

For interactive paging through a large `--limit 0` result, pipe into a pager: `rates ai list --limit 0 | less -S` (`-S` keeps wide rows on one line instead of wrapping them). Once inside `less`, `q` quits; `Ctrl+C` only cancels a search or scroll in progress, it doesn't exit the pager.

### Machine output

```bash
rates ai filter --model-contains opus --json | jq '.[].price.input_mtok'
```

`--json` emits records in the same JSON shape the published ledger files use, so there's one format everywhere. `--no-header` prints rows only, no table header and no result-count footer, for `awk`/`cut` pipelines. Tables truncate to the terminal width only on a live terminal; piped output is never truncated. Notices go to stderr, so stdout stays clean for pipes.

Exit codes: `0` success (including the welcome screens), `1` the work failed (no such model, sources unreachable), `2` the command was malformed (unknown flag, missing unit or direction).

On a color-capable terminal, `Error:` prints in red and `Warning:` in yellow so each is the first thing the eye catches, and an unscoped listing's RATE column (see § Domains) prints in green; the rest of each message or row stays plain. Off automatically when the relevant stream isn't a terminal (piped, redirected, or in a log), and honors the [`NO_COLOR`](https://no-color.org) convention either way.

### Typos

A mistyped verb, domain, or flag gets the error plus one suggestion when something close exists:

```
$ rates ai searc
Error: 'searc' isn't valid for a command, choose from: list, filter, search, show, info.
Perhaps you meant `rates ai search`?
Use --help for the full option list.
```

Errors never dump the full usage block; a specific mistake gets a specific answer, and `--help` is one flag away for everything else.

### Data freshness

The bundled snapshot refreshes with each release and reports its own age:

```bash
rates ai info
```

```
domain: ai
  schema version: 1.0.0
  snapshot: 2026-08-23 (1 day old)
  models: 6889
  providers: 184
  type known: 3380 of 6889 (untyped models never match --type)
  sources: ok (checked 2026-08-23)
  Note: Services with unpublished, inaccessible, or non-unit pricing (subscriptions, platform bundles) aren't listed.
```

The sources line stays one summary: `ok` with the check date when every source answered, or a count (`checked 2026-08-23; two sources inaccessible`) when some didn't.

Past 28 days, commands print a staleness warning with the ways to refresh. One flag fetches fresher data on demand, on any command:

| `--fetch` value | What it does | On failure |
|---|---|---|
| (omitted) | Reads the best snapshot already on this machine: the one installed with the package, or a newer one a prior `--fetch stable` already downloaded | Can't fail, no network |
| `stable` | Checks for a newer published ledger (one small request); downloads it only if one exists | Warns and serves the best local snapshot; never fails the command |
| `live` | Refetches the raw sources and fuses them in your own process; cached for 24 hours | Fails with a named reason |

`--timeout SECONDS` (up to 300) applies to `stable`/`live` only, and is an error without one of them, since the default tier makes no requests for it to govern. `--fetch` is never implied: omit it and `rates` makes no network requests at all.

`stable` and `live` each cache under `~/.cache/rates`, a private per-user directory: a `stable` fetch is served on later `stable` (and default) calls until a newer ledger is published, so repeat checks cost one small request, not a re-download; `live` keeps its fused result for 24 hours. A successful `stable` fetch also resets the default tier's staleness clock, since the default reads whichever local snapshot is newest, not always the one installed with the package. On the CLI, every `rates` warning (staleness, stable fallback) prints as a single `Warning:` line on stderr, yellow on a color-capable terminal; the Python API keeps the standard `warnings` rendering.

`--force`, with `--fetch live` only, skips that 24-hour cache and fuses fresh regardless of how recent the cached result is. It's for a volatile domain (crypto, say) where the cache window itself is already too coarse, e.g. news just moved a price and "an hour old" is wrong, not just imprecise. An error with any other `--fetch` value, or with none, since the bundled and stable tiers don't hold a cached result of their own for it to bypass.

Since `stable`/`live` make a network call, the CLI (not the Python API) prints a one-line notice before it starts and how long it took once it's back, both on stderr: `Checking live rates...` / `Checking for a newer ledger...`, then `Done in 2.3s.`. The default tier is instant and stays silent.

### Tab completion (optional)

`rates` prints a completion script for your shell on demand; wiring it up is opt-in and something you do yourself. `rates` never edits your shell configuration for you. Completion is per-shell: it's active only in shells whose startup loads it, so a one-off `eval`/`source` in the current shell lasts until you close that window. To keep it, add one of the lines below to your shell's startup file, which is a persistent change to how your shell starts. Each has an uninstall step.

**bash**: add to `~/.bashrc`:

```bash
eval "$(rates completion bash)"
```

Uninstall: delete that line.

**zsh**: the completion system must be initialized first (`autoload -Uz compinit && compinit`, which most configurations already run). Two routes:

- Simplest, add to `~/.zshrc` below your `compinit`:

  ```bash
  eval "$(rates completion zsh)"
  ```

  This runs `rates` once per shell start. Uninstall: delete the line.

- No per-shell cost, drop the script into a directory already on your `$fpath` (see them with `print -l $fpath`; `~/.local/share/zsh/site-functions` is common) as `_rates`, and zsh autoloads it:

  ```bash
  rates completion zsh > ~/.local/share/zsh/site-functions/_rates
  ```

  The generated script carries a `#compdef` tag, so the same output works either sourced or autoloaded from `$fpath`. Uninstall: delete the `_rates` file.

**fish**: write it where fish auto-loads completions; no config edit:

```bash
rates completion fish > ~/.config/fish/completions/rates.fish
```

Uninstall: delete that file.

**PowerShell**: add to your `$PROFILE`:

```powershell
rates completion powershell | Out-String | Invoke-Expression
```

Uninstall: remove that line from `$PROFILE`.

Completion covers commands and flags, and pulls values from the data itself: `--provider <TAB>` lists the providers in the registry, `--price-unit <TAB>` the billing units, and `rates ai show anthropic/<TAB>` cycles the model identities.

## The Python API

### Loading

```python
import rates.ai

registry = rates.ai.load()
```

`load()` reads the best local snapshot, zero network. The same three tiers as the CLI, picked by one `fetch` parameter instead of a combination:

```python
registry = rates.ai.load()                                # fetch="bundled", the default
registry = rates.ai.load(fetch="stable")                  # check for a newer published ledger
registry = rates.ai.load(fetch="live", timeout=180)       # fuse the raw sources directly
registry = rates.ai.load(fetch="live", force=True)        # skip live's 24-hour cache too
```

`force=True` only combines with `fetch="live"`; it's a `ValueError` otherwise, since `bundled` and `stable` don't hold a cached result of their own for it to bypass.

`fetch="stable"` never raises; any failed check falls back to the best local snapshot with a `SyncFallbackWarning`. `fetch="live"` raises when it can't produce an honest result, and warns with a `SourceUnreachableWarning` when it produced one but a non-preferred source was unreachable, so the fields that source enriches may be absent. Warnings use Python's `warnings` machinery, so you can escalate or silence them:

```python
import warnings
import rates

warnings.simplefilter("error", rates.StaleLedgerWarning)  # stale data becomes a hard failure
```

| Exception / warning | Meaning |
|---|---|
| `rates.RatesError` | Base class for everything `rates` raises |
| `rates.LiveFusionError` | A `fetch="live"` call couldn't produce a result |
| `rates.AllSourcesUnreachableError` | Every upstream source failed |
| `rates.PreferredSourceUnavailableError` | The preferred source failed; a result from fallbacks alone is refused |
| `rates.RatesWarning` | Base class for every warning `rates` emits |
| `rates.StaleLedgerWarning` | The best local snapshot is past its staleness threshold |
| `rates.SyncFallbackWarning` | A `fetch="stable"` check couldn't complete; local data served |
| `rates.SourceUnreachableWarning` | A `fetch="live"` fusion ran with a non-preferred source unreachable; its enriched fields may be absent |

### Querying

`filter()` takes the same criteria as the CLI's flags, in snake_case; `sort_by()` requires its direction. Both return a new `Registry`, so calls chain and the envelope metadata travels along:

```python
import rates.ai

registry = rates.ai.load()
affordable = registry.filter(
    provider="anthropic", type="chat", price_max=5, price_unit="input_mtok"
)
cheapest_first = affordable.sort_by("price.input_mtok", descending=False)
for model in cheapest_first:
    print(model.id, model.price.get("input_mtok"))
```

A `Registry` iterates over its models, has a length, and knows its own billing vocabulary:

```python
registry.price_units()   # ['cache_audio_read_mtok', ..., 'input_mtok', 'output_mtok', ...]
```

### Working with a model

```python
(opus,) = registry.filter(provider="anthropic", model="claude-opus-5")

opus.price.get("input_mtok")        # 5
opus.reasoning.levels               # (low, medium, high, xhigh, max)
opus.lifecycle.deprecation_date     # datetime.date or None
opus.to_dict()                      # the record as ledger-shaped JSON
```

Some models reprice past a usage threshold (long-context tiers). `price` is always the base tier; `price_for()` resolves the price under stated conditions:

```python
model.price.get("input_mtok")                       # base rate
model.price_for(context=500_000).get("input_mtok")  # rate with tier overrides applied
```

Where two sources disagreed about a price by more than 2%, the disagreement is stored on the record rather than discarded:

```python
for d in model.price_discrepancies:
    print(d.field, d.chosen_value, "vs", d.other_source, d.other_value)
```

Some model ids are rolling references rather than dated snapshots (`gemini-pro-latest`, `gpt-5.2-chat-latest`): the provider retargets them over time, so today's price belongs to whatever the alias currently points at. Where the id matches a recorded, evidence-backed naming convention for its provider, the record carries that as `alias`:

```python
if model.alias is not None:
    model.alias.convention   # "-latest suffix"
    model.alias.maintained   # True (kept aimed at a live model), False (observed stale), None (unverified)
    model.alias.verified     # date of the backing evidence
    model.alias.note         # one sentence of that evidence
```

`alias` is `None` for a dated or pinned id, and for any provider without a recorded convention; it's never guessed from the id's shape alone. `maintained` describes whether the id is safe to call, not whether the price is right, since pricing always reflects the model the alias currently resolves to. `rates ai show <model>` prints the fact, and `--json` carries it in the record.

The full field-by-field schema, including the reasoning control forms (`effort`, `budget_tokens`, `toggle`), lifecycle fields, and the alias fact, is in [ERD.md](ERD.md).

## The data

The registry holds what could be gathered with verifiable per-unit pricing: services whose pricing isn't published, isn't accessible, or isn't per-unit at all (subscriptions, platform bundles) aren't listed, so the catalog is what we could verify for you, never a census of everything that exists.

The AI domain fuses four sources, [models.dev](https://github.com/anomalyco/models.dev) as the preferred source with [genai-prices](https://github.com/pydantic/genai-prices), [LiteLLM](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json), and [OpenRouter](https://openrouter.ai/api/v1/models) filling and cross-validating, into one schema in which every record lists the sources that contributed to it. The snapshot is dated and versioned, and `--fetch stable` checks the project's published releases for a newer one. What it takes for a model to appear is covered in [ARCHITECTURE.md](ARCHITECTURE.md).

## Tracing

If [traceact](https://github.com/traceact/traceact) is installed alongside `rates`, loads, live fusions, and CLI runs record traces; without it, nothing changes and nothing extra is required. The CLI writes traces to `~/.traceact/rates.jsonl` unless your app has configured its own traceact sinks, which always win. The Python API never touches tracing configuration, so a script with traceact installed but unconfigured gets traceact's own default, traces printed to the console; configure a sink (`traceact.configure(sinks=[traceact.JsonlSink("traces.jsonl")])`) to route them elsewhere.

---

By [Mo Shehu](https://mohammedshehu.com)
