#!/usr/bin/env python3
"""Sync the sample output quoted in the docs to the bundled ledger.

The weekly ledger build changes the model, provider, and typed counts, the
snapshot and source-check dates, the `rates ai list` sample's rows and
column widths, and (rarely) the schema version. README
and USAGE quote those exact values in their sample output, so they drift from
the shipped ledger every rebuild (the CHANGELOG quotes a rounded count, which
hides the drift there). With no arguments this rewrites the samples in place;
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
        "schema": ledger["schema_version"],
        "checked": max(
            (s["fetched_at"][:10] for s in ledger["sources"] if s.get("fetched_at")),
            default=ledger["snapshot_date"],
        ),
    }


def _list_sample() -> str:
    """The header and first three rows `rates ai list` prints for the
    bundled ledger, rendered by the CLI's own table code so the docs'
    sample carries the same column widths and values a reader sees."""
    import contextlib
    import io

    sys.path.insert(0, str(ROOT / "src"))
    from rates import _cli
    from rates.ai._registry import Registry

    registry = Registry.from_dict(json.loads(gzip.decompress(LEDGER.read_bytes())))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        _cli._render_table(_cli.DOMAINS["ai"]["columns"], list(registry), 20)
    return "\n".join(buffer.getvalue().splitlines()[:4])


def _rules(c: dict[str, object]) -> list[tuple[str, re.Pattern[str], str]]:
    """Most rules target one value-bearing line by an anchored pattern whose
    first group is the fixed prefix, so only the value changes. The list
    sample's rule replaces its four-line block (header and three rows)
    with the freshly rendered table text."""
    n, p, t, d = c["models"], c["providers"], c["typed"], c["snapshot"]
    # The `rates ai list` sample: its header line and the three rows under it.
    list_block = re.compile(r"^PROVIDER  MODEL .*\n(?:.*\n){2}.*$", re.MULTILINE)
    list_sample = _list_sample().replace("\\", "\\\\")
    return [
        ("USAGE.md", list_block, list_sample),
        ("README.md", list_block, list_sample),
        ("USAGE.md", re.compile(r"^(  schema version: )[\d.]+$", re.MULTILINE), rf"\g<1>{c['schema']}"),
        ("USAGE.md", re.compile(r"(\bchecked )\d{4}-\d{2}-\d{2}"), rf"\g<1>{c['checked']}"),
        ("USAGE.md", re.compile(r"^(  snapshot: )\d{4}-\d{2}-\d{2}", re.MULTILINE), rf"\g<1>{d}"),
        ("USAGE.md", re.compile(r"^(  models: )\d+$", re.MULTILINE), rf"\g<1>{n}"),
        ("USAGE.md", re.compile(r"^(  providers: )\d+$", re.MULTILINE), rf"\g<1>{p}"),
        ("USAGE.md", re.compile(r"^(  type known: )\d+ of \d+", re.MULTILINE), rf"\g<1>{t} of {n}"),
        ("USAGE.md", re.compile(r"^(20 of )\d+( shown)", re.MULTILINE), rf"\g<1>{n}\g<2>"),
        ("README.md", re.compile(r"^(20 of )\d+( shown)", re.MULTILINE), rf"\g<1>{n}\g<2>"),
    ]


def sync(check: bool) -> list[str]:
    """Rewrite (``check=False``) or verify (``check=True``) the doc samples.
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
            f"{rel}: doc samples are stale against the bundled ledger"
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
