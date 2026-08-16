# rates

A pricing registry and decision layer, spanning multiple pricing universes. Starting with AI API rates; cloud pricing and others may follow.

**This release (`0.0.2`) is a name reservation on PyPI, not a working library.** It has no functionality beyond a version number. A design is in progress; see [project/ROADMAP.md](project/ROADMAP.md) for status.

## Why this exists

Provider pricing pages change often, aren't machine-readable, and don't answer the question a dev actually has: which option, for this job, at this budget. Raw per-token price feeds already exist and stay current for AI APIs ([genai-prices](https://github.com/pydantic/genai-prices), [LiteLLM's model_prices_and_context_window.json](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)) — `rates` is meant to build on feeds like these rather than duplicate them, adding the decision layer they don't attempt.

## Status

See [project/ROADMAP.md](project/ROADMAP.md) for what's built and what's next.

---

By [Mo Shehu](https://mohammedshehu.com)
