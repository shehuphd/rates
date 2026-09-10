#!/usr/bin/env python3
"""Sync the exact count samples in the docs to the bundled ledger.

The weekly ledger build changes the model, provider, and typed counts. README
and USAGE quote those exact numbers in their sample output, so they drift from
the shipped ledger every rebuild (the CHANGELOG quotes a rounded count, which
hides the drift there). With no arguments this rewrites the numbers in place;
with ``--check`` it reports any sample that's out of sync and exits non-zero,
so a stale figure fails the build instead of reaching a reader.

The weekly workflow runs the rewrite before committing the rebuilt ledger; the
docs-hygiene test runs the check.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "src" / "rates" / "ai" / "ledger-ai.json.gz"


def _counts() -> dict[str, object]:
    ledger = json.loads(gzip.decompress(LEDGER.read_bytes()))
    models = ledger["models"]
    return {
        "models": len(models),
        "providers": len({m["provider"] for m in models}),
        "typed": sum(1 for m in models if m.get("type")),
        "snapshot": ledger["snapshot_date"],
    }


def _rules(c: dict[str, object]) -> list[tuple[str, re.Pattern[str], str]]:
    """Each rule targets one count-bearing line by an anchored pattern whose
    first group is the fixed prefix, so only the number changes."""
    n, p, t, d = c["models"], c["providers"], c["typed"], c["snapshot"]
    return [
        ("USAGE.md", re.compile(r"^(  snapshot: )\d{4}-\d{2}-\d{2}", re.MULTILINE), rf"\g<1>{d}"),
        ("USAGE.md", re.compile(r"^(  models: )\d+$", re.MULTILINE), rf"\g<1>{n}"),
        ("USAGE.md", re.compile(r"^(  providers: )\d+$", re.MULTILINE), rf"\g<1>{p}"),
        ("USAGE.md", re.compile(r"^(  type known: )\d+ of \d+", re.MULTILINE), rf"\g<1>{t} of {n}"),
        ("USAGE.md", re.compile(r"^(20 of )\d+( shown)", re.MULTILINE), rf"\g<1>{n}\g<2>"),
        ("README.md", re.compile(r"^(20 of )\d+( shown)", re.MULTILINE), rf"\g<1>{n}\g<2>"),
    ]


def sync(check: bool) -> list[str]:
    """Rewrite (``check=False``) or verify (``check=True``) the doc counts.
    Returns a list of human-readable problems, empty when everything agrees."""
    counts = _counts()
    edits: dict[Path, str] = {}
    stale: set[str] = set()
    for rel, pattern, repl in _rules(counts):
        path = ROOT / rel
        text = edits.get(path, path.read_text())
        new = pattern.sub(repl, text)
        if new != text:
            stale.add(rel)
        edits[path] = new
    if check:
        return [
            f"{rel}: count samples are stale against the bundled ledger"
            for rel in sorted(stale)
        ]
    for path, text in edits.items():
        path.write_text(text)
    return []


def main(argv: list[str]) -> int:
    problems = sync(check="--check" in argv)
    if problems:
        for line in problems:
            print(line, file=sys.stderr)
        print("Run `python scripts/sync_docs.py` to resync.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
