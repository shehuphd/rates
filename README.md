# rates

A pricing registry and decision layer, spanning multiple pricing universes. Starting with AI API rates; cloud pricing and others may follow.

**This release (`0.0.3`) is a name reservation on PyPI, not a working library.** It has no functionality beyond a version number. A design is in progress; see [project/ROADMAP.md](project/ROADMAP.md) for status.

## Why this exists

Provider pricing pages change often, aren't machine-readable, and don't answer the question a dev actually has: which option, for this job, at this budget. Several raw feeds already exist ([models.dev](https://github.com/anomalyco/models.dev), [genai-prices](https://github.com/pydantic/genai-prices), [LiteLLM's model_prices_and_context_window.json](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json), [OpenRouter](https://openrouter.ai/api/v1/models)), each with real gaps, `rates` fuses them into one schema rather than duplicating any single one, and ships it as dated, versioned releases none of them offer on their own.

`rates` exposes facts, never opinions: no model gets ranked, scored, or labeled "best." See [ARCHITECTURE.md](ARCHITECTURE.md) for why, and [ERD.md](ERD.md) for the schema itself.

## Status

See [project/ROADMAP.md](project/ROADMAP.md) for what's built and what's next.

---

By [Mo Shehu](https://mohammedshehu.com)
