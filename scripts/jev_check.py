"""Extraction-fidelity check for origin rows, run in the weekly build.

For each origin vendor, asks TypeSafe's Jev one batched set of yes/no
questions: does the fetched page text state the rate the parser
extracted, for that model, on that unit? This catches the silent parser
failure deterministic checks miss (a redesigned page that still parses
but binds a price to the neighboring model's name).

Advisory by design: every judgment prints, a page-level disagreement is
reported for human review, and the exit code stays zero, because a
blocking threshold is a design decision that hasn't been signed. Jev is
a checker here, never a picker; no model output ever supplies a number.

Degrades to a printed skip, never a failed build, when the SDK isn't
installed, the key is absent or refused, or the API is unreachable.
Requires TYPESAFE_API_KEY (a CI secret; locally, exported by the
caller). Spend is recorded per call to the JSONL path in
JEV_SPEND_FILE (default jev-spend.jsonl in the working directory, the
repo root when run from there, where it's gitignored).

Maintainer/CI tooling, not part of the installed rates package; the
typesafe-sdk dependency exists only in the workflow's environment.
SDK call signatures verified against docs.typesafe.ai 2026-09-26. No temperature
or seed parameter exists on this API; nothing here should add one.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rates._http import fetch_text
from rates.ai._origins import _PARSERS, ORIGIN_URLS

SPEND_FILE = Path(os.environ.get("JEV_SPEND_FILE", "jev-spend.jsonl"))

_UNIT_PHRASES = {
    "audio_minute": "per minute of pre-recorded/batch audio",
    "streaming_audio_minute": "per minute of streamed audio",
    "session_minute": "per minute of streaming session time",
    "kchar": "per 1,000 characters",
    "input_mtok": "per million input tokens",
    "output_mtok": "per million output tokens",
}


def _skip(reason: str) -> int:
    print(f"jev check skipped: {reason}; deterministic qualification stands alone")
    return 0


def _page_state(name: str, page: str) -> str:
    """The page text Jev judges against: the same region the parser read,
    kept inside the state token budget."""
    if name == "deepgram_pricing":
        match = re.search(r'<script id="product-offer-schema"[^>]*>(.*?)</script>', page, re.DOTALL)
        return match.group(1) if match else page[:40_000]
    if name == "livekit_pricing":
        clean = page.replace("<!-- -->", "")
        frags = re.findall(r"<h3[^>]*>[^<]*model prices[^<]*</h3><ul>.*?</ul>", clean, re.DOTALL)
        return "\n".join(frags) or clean[:40_000]
    return page[:80_000]


def _record_spend(entry: dict[str, Any]) -> None:
    SPEND_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SPEND_FILE.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


def _ask(client: Any, state: str, questions: dict[str, Any], purpose: str) -> Any:
    """One batched call, spend recorded at the moment it returns."""
    started = time.monotonic()
    response = client.system_one(state=state, questions=questions)
    usage = getattr(response, "usage", None)
    _record_spend(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "provider": "typesafe",
            "model": getattr(response, "model", None),
            "purpose": purpose,
            "questions": len(questions),
            "wall_ms": round((time.monotonic() - started) * 1000),
            "request_id": getattr(response, "request_id", None),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
    )
    return response


def main() -> int:
    if not os.environ.get("TYPESAFE_API_KEY"):
        return _skip("TYPESAFE_API_KEY isn't set")
    try:
        from typesafe_sdk import Noul, TypeSafeAPIError, TypeSafeClient
    except ImportError:
        return _skip("typesafe-sdk isn't installed")

    disagreements = 0
    with TypeSafeClient() as client:
        # Pre-flight: one minimal judgment, so a refused or drained key
        # surfaces here, not halfway through the vendors.
        try:
            _ask(
                client,
                "The sky is blue.",
                {"preflight": Noul(instructions="Is the sky blue?")},
                "origin-fidelity preflight",
            )
        except TypeSafeAPIError as exc:
            return _skip(f"preflight failed ({exc})")
        except Exception as exc:  # noqa: BLE001 - degrade on any transport failure
            return _skip(f"preflight unreachable ({exc})")

        for name, url in ORIGIN_URLS.items():
            try:
                page = fetch_text(url)
            except Exception as exc:  # noqa: BLE001
                print(f"{name}: page fetch failed ({exc}); skipped")
                continue
            try:
                records = _PARSERS[name](page, "9999-01-01")
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                print(f"{name}: parser raised ({exc}); nothing to verify")
                continue
            if not records:
                print(f"{name}: parser yielded nothing; nothing to verify")
                continue

            questions: dict[str, Any] = {}
            labels: dict[str, str] = {}
            for record in records:
                for unit, rate in record["price"].items():
                    if unit == "currency":
                        continue
                    qid = f"{record['id']}::{unit}"
                    labels[qid] = f"{record['provider']}/{record['id']} {unit}={rate}"
                    model_name = record["id"].replace("-", " ")
                    if rate == 0:
                        # A zero ships only when the page states it in
                        # words, so that's what Jev is asked about.
                        instructions = (
                            "Does this pricing text state, in words or "
                            f"figures, that the model named '{model_name}' "
                            f"costs nothing {_UNIT_PHRASES[unit]} (for "
                            "example, that those tokens are free)?"
                        )
                    else:
                        instructions = (
                            "Does this pricing text state a rate equivalent to "
                            f"${rate} {_UNIT_PHRASES[unit]} for the model named "
                            f"'{model_name}'? A rate published "
                            "per hour or per 1,000 units counts when the "
                            "arithmetic matches."
                        )
                    questions[qid] = Noul(instructions=instructions)

            state = _page_state(name, page)
            try:
                answers = _batched_answers(client, state, questions, name, TypeSafeAPIError)
            except TypeSafeAPIError as exc:
                print(f"{name}: jev refused ({exc}); deterministic checks stand")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"{name}: jev unreachable ({exc}); deterministic checks stand")
                continue

            for qid, label in labels.items():
                probability = answers.get(qid)
                if probability is None:
                    print(f"  {label}: no answer returned")
                elif probability < 0.5:
                    disagreements += 1
                    print(f"  {label}: PAGE DISAGREES (p={probability:.2f}) - review")
                else:
                    print(f"  {label}: agrees (p={probability:.2f})")

    if disagreements:
        print(
            f"jev check: {disagreements} row(s) flagged for review; advisory only, build continues"
        )
    else:
        print("jev check: every extracted row agrees with its page")
    return 0


def _batched_answers(
    client: Any,
    state: str,
    questions: dict[str, Any],
    purpose: str,
    api_error: type[Exception],
) -> dict[str, float]:
    """All questions in one call; on a content-triggered 403 (empty-body
    block, deterministic per state) split the state in halves and take
    each question's highest answer, since the rate's statement lives in
    one half."""
    try:
        response = _ask(client, state, questions, f"origin-fidelity {purpose}")
        return {qid: answer.noul for qid, answer in response.nouls.items()}
    except api_error as exc:
        if getattr(exc, "status", None) != 403:
            raise
        midpoint = len(state) // 2
        merged: dict[str, float] = {}
        for half in (state[:midpoint], state[midpoint:]):
            response = _ask(client, half, questions, f"origin-fidelity {purpose} (split)")
            for qid, answer in response.nouls.items():
                merged[qid] = max(merged.get(qid, 0.0), answer.noul)
        return merged


if __name__ == "__main__":
    sys.exit(main())
