"""Build a fresh AI-domain ledger from the live sources.

Writes two artifacts: ledger-ai.json (plain, attached to the GitHub
Release so dated snapshots stay diffable) and the gzipped copy bundled
into the package at src/rates/ai/ledger-ai.json.gz. Run from the repo
root. Exits non-zero if the fusion can't produce a result, so the weekly
workflow fails visibly instead of publishing nothing.

Requires keycall (maintainer/CI only, not a runtime dependency of the
installed rates package) to bake each model's alias fact into the
ledger at build time. See ARCHITECTURE.md § alias facts.
"""

import gzip
import json
import sys
from pathlib import Path
from typing import Any

import keycall

from rates.ai._freshness import gather_source_freshness, record_freshness_lookup
from rates.ai._fusion import fetch_sources, fuse
from rates.ai._origins import ORIGIN_PROVIDERS
from rates.ai._sources import normalize_models_dev

ROOT = Path(__file__).resolve().parent.parent


def _origin_delta_report(fused: dict[str, Any]) -> None:
    """Qualification step two, advisory: how each origin provider's rows
    moved against the previous bundled snapshot. A vanished model, a new
    one, or a changed rate prints here so a parser drifting into wrong
    bindings is read by a person, not shipped in silence; nothing blocks,
    since a route to blocking needs a signed threshold first."""
    bundled = ROOT / "src" / "rates" / "ai" / "ledger-ai.json.gz"
    try:
        previous = json.loads(gzip.decompress(bundled.read_bytes()))
    except (OSError, ValueError):
        return

    def rows(ledger: dict[str, Any], provider: str) -> dict[str, dict[str, Any]]:
        return {
            m["id"]: {k: v for k, v in m["price"].items() if k != "currency"}
            for m in ledger["models"]
            if m["provider"] == provider
        }

    for provider in sorted(ORIGIN_PROVIDERS.values()):
        old, new = rows(previous, provider), rows(fused, provider)
        if not old and not new:
            continue
        for model_id in sorted(set(old) - set(new)):
            print(f"origin delta: {provider}/{model_id} vanished (had {old[model_id]})")
        for model_id in sorted(set(new) - set(old)):
            print(f"origin delta: {provider}/{model_id} is new ({new[model_id]})")
        for model_id in sorted(set(old) & set(new)):
            if old[model_id] != new[model_id]:
                print(
                    f"origin delta: {provider}/{model_id} moved "
                    f"{old[model_id]} -> {new[model_id]}"
                )


def _resolve_alias(provider: str, model_id: str) -> dict[str, Any] | None:
    """This id's rolling-alias fact, per KeyCall's per-provider convention
    catalog, or None when the provider has no recorded convention (KeyCall
    raises UNSUPPORTED_PROVIDER for that case) or the id doesn't match one.
    Any other KeyCallError is unexpected and propagates."""
    try:
        fact = keycall.alias_fact(provider, model_id)
    except keycall.KeyCallError as exc:
        if exc.code == keycall.ErrorCode.UNSUPPORTED_PROVIDER:
            return None
        raise
    if fact is None:
        return None
    return {
        "convention": fact.convention,
        "maintained": fact.maintained,
        "verified": fact.verified,
        "note": fact.note,
    }


def main() -> int:
    payloads, statuses = fetch_sources()
    fused = fuse(
        payloads,
        statuses,
        source_freshness=gather_source_freshness(statuses),
        record_freshness=record_freshness_lookup(),
    )

    degraded = [
        f"{s['name']} ({s['status']})"
        for s in fused["sources"]
        if s["status"] != "ok"
    ]
    if degraded:
        print(f"warning: sources degraded this run: {', '.join(degraded)}")
    _origin_delta_report(fused)

    aliased = 0
    for model in fused["models"]:
        fact = _resolve_alias(model["provider"], model["id"])
        if fact is not None:
            model["alias"] = fact
            aliased += 1
    if aliased:
        print(f"tagged {aliased} record(s) with a rolling-alias fact")

    preferred_count = len(normalize_models_dev(payloads.get("models_dev") or {}))
    preferred_kept = sum(
        1 for m in fused["models"] if "models_dev" in m["sources"]
    )
    dropped = preferred_count - preferred_kept
    if dropped:
        print(
            f"excluded {dropped} record(s) with no per-unit pricing "
            "(admission criterion 2)"
        )

    raw = json.dumps(fused, separators=(",", ":")).encode()
    (ROOT / "ledger-ai.json").write_bytes(raw)
    (ROOT / "src" / "rates" / "ai" / "ledger-ai.json.gz").write_bytes(
        gzip.compress(raw, 9)
    )
    print(
        f"ledger built: {len(fused['models'])} models, "
        f"snapshot {fused['snapshot_date']}, {len(raw) / 1e6:.2f} MB raw"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
