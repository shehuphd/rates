# rates

A pricing registry and decision layer, spanning multiple pricing universes. Starting with AI API rates; cloud pricing and others may follow.

**Status:** the version on PyPI (`0.0.3`) is a name reservation with no functionality. The library itself, the fused AI-universe registry, the query API, and the three access tiers described in [ARCHITECTURE.md](ARCHITECTURE.md), lives on this branch and ships as `1.0.0` once the CLI and docs are done.

## Why this exists

Provider pricing pages change often, aren't machine-readable, and don't answer the question a dev has mid-build: which option, for this job, at this budget. Several raw feeds exist ([models.dev](https://github.com/anomalyco/models.dev), [genai-prices](https://github.com/pydantic/genai-prices), [LiteLLM's model_prices_and_context_window.json](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json), [OpenRouter](https://openrouter.ai/api/v1/models)), each missing pieces the others carry. `rates` fuses them into one schema rather than duplicating any single one, cross-validates prices between them, and ships the result as dated, versioned releases none of them offer on their own.

`rates` exposes facts, never opinions: no model gets ranked, scored, or labeled "best." See [ARCHITECTURE.md](ARCHITECTURE.md) for why, and [ERD.md](ERD.md) for the schema itself.

---

By [Mo Shehu](https://mohammedshehu.com)
