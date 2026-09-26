# AssemblyAI Pricing — Authoritative Reference

> **Source:** assemblyai.com/pricing, assemblyai.com/docs, first-party employee information
> **Last updated:** 2026-05-29
> **Purpose:** Canonical pricing reference for LLM-assisted support and content. Covers per-model rates, add-ons (Speech Understanding, Guardrails), Voice Agent API, LLM Gateway token pricing, billing rules, concurrency limits, and common pricing pitfalls. For model selection, feature support, and language coverage, see `/llms/models.md`.

---

## Key Pricing Facts (Read First)

- **Pre-recorded** is billed per **hour of audio submitted**.
- **Streaming** is billed per **session duration** — the time the WebSocket connection is open, **not** the duration of audio sent. Idle connection time counts. **Close connections immediately when calls end.**
- **Voice Agent API** is billed per session minute at **$4.50/hr ($0.075/min)**.
- **LLM Gateway** is billed per million tokens (separate input / output rates), with prompt caching available on Anthropic, OpenAI, and Google models for additional savings.
- **EU region is the same price as US.** Use `api.eu.assemblyai.com` / `streaming.eu.assemblyai.com` for GDPR-compliant data residency at no premium.
- **Add-ons stack on the base rate.** A request using Universal-3.5 Pro + Medical Mode + Speaker Diarization (standard) costs `$0.21 + $0.15 + $0.02 = $0.38/hr`.
- **Multichannel audio is billed per channel.** A 1-hour 2-channel file = 2 billable hours.
- **$50 in free credits, no credit card required.** Free-tier and pay-as-you-go differ on concurrency (see Concurrency section).
- **Model selection is the biggest cost lever**, but the cheapest model that loses a competitive eval is not the cheapest path to deployment.

---

## Pre-Recorded (Async) Rate Card

| Model | API value | Price |
|---|---|---|
| Universal-3.5 Pro | `universal-3-5-pro` | **$0.21/hr** |
| Universal-2 | `universal-2` | **$0.15/hr** |
| SLAM-1 (deprecated) | `slam-1` | — (do not use; migrate to `universal-3-5-pro`) |

Legacy values `best` and `nano` are deprecated and route to `universal-3-5-pro` and `universal-2` respectively. Do not rely on default model selection — defaults can differ between free-tier and paid accounts.

---

## Streaming (Real-Time) Rate Card

| Model | API value (v3) | Price |
|---|---|---|
| Universal-3.5 Pro Realtime | `u3-rt-pro` (alias `u3-pro`) | **$0.45/hr** base |
| Universal-Streaming English | `universal-streaming-english` | **$0.15/hr** |
| Universal-Streaming Multilingual | `universal-streaming-multilingual` | **$0.15/hr** |

> **Streaming billing reminder:** session duration, not audio duration. A WebSocket open for 60 minutes with 30 minutes of audio sent is billed for 60 minutes.

> **Streaming endpoint:** `wss://streaming.assemblyai.com/v3/ws`. The old v2 endpoint (`wss://api.assemblyai.com/v2/realtime/ws`) is inactive.

---

## Voice Agent API Rate Card

**Launched:** April 2026 (formerly Speech-to-Speech API)
**Docs:** https://www.assemblyai.com/docs/voice-agents/voice-agent-api
**WebSocket endpoint:** `wss://agents.assemblyai.com/v1/ws`
**Architecture:** Cascaded STT (`u3-rt-pro`) + LLM (LLM Gateway) + TTS over self-hosted LiveKit, in a single WebSocket. PCI-certified.

| Product | Price |
|---|---|
| Voice Agent API | **$4.50/hr** ($0.075/min) |

Voice Agent API pricing is all-inclusive of STT, LLM reasoning, TTS, turn detection, interruption handling, and tool calling — billed at a single per-minute rate. There is no separate add-on cost for the underlying components when used through the Voice Agent API.

Twilio SIP integration coming Q2 2026; pricing for Twilio integration TBA.

---

