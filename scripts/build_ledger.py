"""Build a fresh AI-universe ledger from the live sources.

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

from rates.ai._fusion import fetch_sources, fuse

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    payloads, statuses = fetch_sources()
    fused = fuse(payloads, statuses)

    unreachable = [n for n, s in statuses.items() if s != "ok"]
    if unreachable:
        print(f"warning: sources unreachable this run: {', '.join(unreachable)}")

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
