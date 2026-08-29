"""Build a fresh AI-domain ledger from the live sources.

Writes two artifacts: ledger-ai.json (plain, attached to the GitHub
Release so dated snapshots stay diffable) and the gzipped copy bundled
into the package at src/rates/ai/ledger-ai.json.gz. Run from the repo
root. Exits non-zero if the fusion can't produce a result, so the weekly
workflow fails visibly instead of publishing nothing.
"""

import gzip
import json
import sys
from pathlib import Path

from rates.ai._freshness import gather_source_freshness, record_freshness_lookup
from rates.ai._fusion import fetch_sources, fuse
from rates.ai._sources import normalize_models_dev

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    payloads, statuses = fetch_sources()
    fused = fuse(
        payloads,
        statuses,
        source_freshness=gather_source_freshness(statuses),
        record_freshness=record_freshness_lookup(),
    )

    unreachable = [n for n, s in statuses.items() if s != "ok"]
    if unreachable:
        print(f"warning: sources unreachable this run: {', '.join(unreachable)}")

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
