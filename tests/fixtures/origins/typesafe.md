# Models

Jev is TypeSafe's flagship model. Every model on this page is served by the same endpoint, `POST /v1/systemone`.

## Current models

| | Jev 1.14 | Jev 1.13 |
| :- | :- | :- |
| Model ID | `jev-1.14.0` | `jev-1.13.0` |
| Price (per Btok / per Mtok) | \$42 / \$0.042 | \$42 / \$0.042 |
| Context length | 64k per-request / 32k per-question | 64k per-request / 32k per-question |
| Input | Text | Text |

Jev 1.14 is the newest Jev release, available from October 14, 2026.

* **Price:** You pay per input token. Output tokens are free. A Btok is a billion tokens and an Mtok is a million tokens.
* **Context length:** Two limits, the per-request context and the per-question context.

## Context length

Long context costs the same per token as any other request.

## Aliases

An alias is a model name that points to a versioned model ID.

| Alias | Points to | Meaning |
| :- | :- | :- |
| `jev-latest` | `jev-1.14.0` | The most recent stable, official release. |
| `jev-preview` | `jev-1.14.0` | The most recent release, official or not. |
