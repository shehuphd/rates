# Architecture

## What this document covers

`rates` has one job: given a task, name the model that fits. The registry (raw prices, normalized) is commodity infrastructure borrowed from [genai-prices](https://github.com/pydantic/genai-prices). The tiering taxonomy below is not — it's the editorial layer that decides which models a developer would reasonably reach for and why, and it's the part a scraper can't automate. This document is the record of that reasoning, kept up to date as models change and the taxonomy gets tested against real usage.

## Two axes, not one list

The first draft of this taxonomy was seven flat tags: `dev`, `prod-quality`, `prod-economy`, `agentic`, `bulk`, `long-context`, `vision`. It didn't survive contact with two questions: how does `dev` differ from `prod-economy`, and how does `agentic` differ from `bulk`? Both questions exposed the same problem — the list mixed two unrelated kinds of distinction into one flat scale.

A model's fit for a task is answered by two independent questions:

1. **What is this call for?** — a budget tier. Every model gets exactly one, in a given recommendation.
2. **What does this call specifically need?** — task-shape tags. A model can carry any number, or none.

```
Budget tier (pick one)     Task-shape tags (pick any)
─────────────────────      ───────────────────────────
dev                        agentic
bulk                       long-context
economy                    vision
quality
```

`rates.recommend(tier="economy", shape=["agentic"])` reads as "the best quality-per-dollar model that's also reliable at running a tool loop" — a two-axis query, which is the actual question a developer has.

## Budget tiers

Ordered by what they're optimizing for, not strictly by price (a `quality`-tier model is usually the most expensive, but the tier means "cost is not the constraint," not "highest number on the price sheet").

### `dev`

**What it answers:** "What do I call while I'm building and debugging, before I've committed to a production model?"

**Why it's a distinct tier, not just cheap:** a `dev`-tier call is disposable by design — it exercises a code path, not a user-facing output. The developer expects to swap the model before shipping. Optimizing for cheap and fast is right here even at some cost to output quality, because the quality of a `dev` call was never the point.

**How it differs from `bulk` (the other cheap tier):** `dev` is workflow-scoped — a human iterating, low volume, temporary. `bulk` is traffic-scoped — a permanent production path running the same cheap model millions of times. They often recommend the identical model, but the reasoning for picking it is different, and a future version of `rates` might diverge them (e.g., a `dev` pick optimized for clear error messages over `bulk`'s optimization for raw per-call cost).

**Illustrative example (Anthropic, live pricing):** [Claude Haiku 4.5](https://platform.claude.com/docs/en/about-claude/models/overview) — $1.00 / $5.00 per MTok, 200K context. Fast enough for an interactive dev loop, cheap enough that iterating fifty times doesn't matter.

### `bulk`

**What it answers:** "This runs at extreme volume and the task barely needs judgment — what's the cheapest model that clears the bar?"

**Why it's a distinct tier:** the license to use the cheapest available model comes from the task's simplicity, not the traffic volume alone. Classifying a support ticket into one of five fixed categories is a `bulk` task at any volume. Drafting a reply to that ticket is not, even at the same volume — it needs judgment `bulk`-tier models don't reliably have.

**Illustrative example (Google, genai-prices 2026-08-16):** `gemini-2.0-flash-lite` — $0.075 / $0.30 per MTok. Priced for single-field extraction and boolean flags run millions of times a day, not for anything that requires holding context or making a judgment call.

### `economy`

**What it answers:** "This is real production traffic and the task needs actual language understanding — what's the best quality-per-dollar, not the cheapest possible?"

**Why it's the default tier:** most product traffic belongs here — summarization, drafting, RAG-answer generation, classification where the categories aren't obvious from a keyword match. The task clears the `bulk` bar (it needs judgment) but doesn't clear the `quality` bar (a wrong answer is correctable, not costly).

**Illustrative example (Anthropic):** Claude Sonnet 5 — $2.00 / $10.00 per MTok (introductory, through 2026-08-31; $3.00 / $15.00 standard), 1M context. Anthropic's own stated positioning for it is "near-Opus quality on coding and agentic work at Sonnet cost" — the quality-per-dollar framing this tier is built around.

### `quality`

**What it answers:** "Cost is not the constraint here — what gets the best output?"

**Why it's a distinct tier:** for low-volume, high-stakes, or hard-to-reverse calls (a compliance document, a hard debugging pass, a safety-relevant classification), the cost difference between the best model and the second-best is trivial next to the cost of a wrong answer. This tier exists to say so explicitly rather than making the developer eyeball a price table under pressure.

**Illustrative example (Anthropic):** Claude Opus 5 — $5.00 / $25.00 per MTok, 1M context; Claude Fable 5 for the ceiling case — $10.00 / $50.00 per MTok, Anthropic's most capable widely released model.

## Task-shape tags

Cross-cutting. A model earns a tag by being competitive at that specific thing, not merely capable of it.

### `agentic`

**What it flags:** reliable multi-step tool use — planning, calling tools in the right order, recovering from a bad tool result, running an extended loop without losing the thread.

**How it differs from a budget tier:** `agentic` is a capability claim, independent of what the call should cost. A cheap model that's still decent at a two-step tool call is `bulk` + `agentic`; the best available model for a long autonomous coding run is `quality` + `agentic`. They're not the same axis, so they don't compete for the same slot in a recommendation.

**Illustrative example:** OpenAI's o-series reasoning models (`o3`, `o4-mini` — via genai-prices' Azure-hosted OpenAI entries, $2.00/$8.00 and $1.10/$4.40 per MTok respectively) are built for exactly this: multi-step reasoning before answering, which is the same skill a tool loop needs.

### `long-context`

**What it flags:** a usable context window large enough, at acceptable per-call cost, to hold the whole document or conversation without chunking it first.

**Why "usable," not just "large":** a 1M-token window priced at frontier rates isn't a `long-context` pick for a bulk task — the tag means the window is large *and* affordable enough to actually reach for at that size, which ties it to the budget tier it's paired with.

**Illustrative example:** every current Anthropic model (Sonnet 5, Opus 5, Fable 5) ships a 1M-token window at its listed price — the tag doesn't discriminate within Anthropic's current lineup, but it does discriminate against a 200K-window model like Haiku 4.5 when the task genuinely needs the larger window.

### `vision`

**What it flags:** accepts image input with quality-per-dollar competitive on visual tasks, not merely capable of ingesting pixels.

**How it differs from a `text`/no-tag default:** every budget tier has a vision-capable pick and a text-only pick; the tag is what tells `rates.recommend()` to filter to the vision-capable one instead of defaulting to whichever model is cheapest for that tier's text workloads.

**Illustrative example (Google, genai-prices):** `gemini-3-flash-preview` — $0.50 / $3.00 per MTok, audio input in addition to vision, positioned as the mid-tier multimodal pick rather than Google's cheapest or most expensive option.

## Assignment methodology

Price alone doesn't determine a tier — an expensive model that isn't actually the best available (a stale or mispriced entry) shouldn't land in `quality` just because it's costly, and a cheap model from a strong release shouldn't be stuck in `bulk` if it's a genuine `economy` pick. Assignment uses two signals together:

1. **Price band** — a first-pass filter using the model's blended $/MTok (input plus a weighted output cost, since output tokens dominate spend on most tasks).
2. **A curated frontier flag** — set by hand when a new model ships, based on the provider's own stated positioning (Anthropic's "near-Opus quality" framing for Sonnet 5 is the kind of source this flag is built from) and, once the harness exists, benchmark placement.

The second signal is deliberate, not a placeholder to be automated away. It's the part of `rates` that can't be scraped from a provider's pricing page, and it's the reason this project has a reason to exist next to a raw-price feed like genai-prices.

## Data provenance

Raw prices are sourced from [genai-prices](https://github.com/pydantic/genai-prices) (MIT-licensed, hourly-refreshed) as the primary feed, normalized into `rates`' own schema per [CODING.md](../CODING.md)'s "normalize across backends" pattern — one record shape, `None` for any field a source doesn't carry, never a fabricated number. Anthropic's own catalog (via the `claude-api` reference used to build this document) is treated as the higher-trust source for Anthropic models specifically, since it's maintained directly by the provider.

**Known gap as of this writing:** the OpenAI slice of genai-prices' feed returned only its reasoning-tier models (o1, o1-mini, o3, o3-mini, o4-mini) hosted via Azure — no GPT-5 family entries were present in the fetch used to write this document. The tier tables above do not include OpenAI's mainline chat models until the data pipeline pulls the full feed programmatically rather than through a summarized fetch. Don't extend the OpenAI examples above from memory — verify against the live feed when the pipeline lands.

---

By [Mo Shehu](https://mohammedshehu.com)
