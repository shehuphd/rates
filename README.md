# rates

A pricing registry and decision layer. One domain ships today: AI API rates, per-unit prices, capabilities, and lifecycle for 6,000+ models.

## Before you start

`rates` needs Python 3.10+. Check with `python3 --version`; if that fails, install it from [python.org/downloads](https://www.python.org/downloads/).

## Install

```bash
pip install rates
```

Zero dependencies, data bundled, works offline.

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

20 of 7126 shown (--limit 0 shows all)
```

```bash
rates ai filter --provider anthropic --price-max 3 --price-unit input_mtok
```

Or from Python:

```python
import rates.ai

registry = rates.ai.load()
under_a_dollar = registry.filter(price_max=1, price_unit="input_mtok")
cheapest_first = under_a_dollar.sort_by("price.input_mtok", descending=False)
```

The full manual, CLI and Python API both, is [USAGE.md](https://github.com/shehuphd/rates/blob/main/USAGE.md).

## Why this exists

Provider pricing pages change often, aren't machine-readable, and don't answer the question a dev has mid-build: which option, for this job, at this budget. Several raw feeds exist ([models.dev](https://github.com/anomalyco/models.dev), [genai-prices](https://github.com/pydantic/genai-prices), [LiteLLM's model_prices_and_context_window.json](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json), [OpenRouter](https://openrouter.ai/api/v1/models)), each missing pieces the others carry. `rates` fuses them into one schema rather than duplicating any single one, cross-validates prices between them, and ships the result as a dated, versioned snapshot none of them offer on their own.

The registry holds what could be gathered with verifiable per-unit pricing; services whose pricing isn't published, isn't accessible, or isn't per-unit at all aren't listed. See [ARCHITECTURE.md](https://github.com/shehuphd/rates/blob/main/ARCHITECTURE.md) for the design, and [ERD.md](https://github.com/shehuphd/rates/blob/main/ERD.md) for the schema itself.

## License

`rates` is MIT licensed. The bundled ledger is derived from the upstream sources above, which carry their own licenses and terms; [NOTICE](https://github.com/shehuphd/rates/blob/main/NOTICE) reproduces their copyright notices and records what each one contributes.

---

By [Mo Shehu](https://mohammedshehu.com)
