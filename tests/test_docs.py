"""Hygiene checks on the public docs: no references to internal files,
README links that survive PyPI's rendering, and third-party attribution
covering every source the shipped ledger is derived from.

PyPI freezes each release's rendered README at build time and resolves
relative links against pypi.org, so a leak here is permanent for that
release; these tests make the leak a failing build instead.
"""

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DOCS = ["README.md", "USAGE.md", "ARCHITECTURE.md", "ERD.md", "CHANGELOG.md"]

# Internal-only paths and files that must never appear in public text.
# pypi.org/project/... URLs are the one legitimate "project/" spelling.
_INTERNAL = re.compile(
    r"(?<!pypi\.org/)project/|CODING\.md|ROADMAP|PRD|~/\.claude|\.claude/"
)


@pytest.mark.parametrize("doc", PUBLIC_DOCS)
def test_public_docs_reference_no_internal_files(doc):
    text = (ROOT / doc).read_text()
    hits = [
        (i, line)
        for i, line in enumerate(text.splitlines(), 1)
        if _INTERNAL.search(line)
    ]
    assert not hits, (
        f"{doc} references an internal file or folder; state the behavior "
        f"directly instead: {hits}"
    )


def test_shipped_source_references_no_internal_files():
    for path in sorted((ROOT / "src").rglob("*.py")):
        hits = [
            (i, line)
            for i, line in enumerate(path.read_text().splitlines(), 1)
            if _INTERNAL.search(line)
        ]
        assert not hits, (
            f"{path.relative_to(ROOT)} ships in the wheel and references "
            f"an internal file: {hits}"
        )


def test_readme_links_are_absolute_for_pypi():
    # PyPI resolves relative links against pypi.org, silently breaking
    # them; every markdown link in the README must be a full URL.
    text = (ROOT / "README.md").read_text()
    links = re.findall(r"\]\(([^)]+)\)", text)
    relative = [
        link
        for link in links
        if not link.startswith(("http://", "https://", "#", "mailto:"))
    ]
    assert not relative, f"relative links break on PyPI's rendered page: {relative}"


def _identity(url: str) -> str:
    """What names the source in prose: an owner/repo for a file served off
    raw.githubusercontent.com, whose host says nothing about whose data it
    is, and the hostname for everything else."""
    parsed = urlparse(url)
    if parsed.netloc == "raw.githubusercontent.com":
        owner, repo, *_ = parsed.path.lstrip("/").split("/")
        return f"{owner}/{repo}"
    return parsed.netloc


def test_notice_covers_every_upstream_source():
    # Every source the fusion fetches contributes to the ledger shipped in
    # the wheel, so each one's attribution has to ship with it. A fifth
    # source added without a NOTICE entry fails here.
    from rates.ai._sources import SOURCE_URLS

    notice = (ROOT / "NOTICE").read_text()
    missing = [url for url in SOURCE_URLS.values() if _identity(url) not in notice]
    assert not missing, (
        f"NOTICE doesn't attribute every source the fusion fetches: {missing}"
    )


def test_changelog_model_count_and_date_match_bundled_ledger():
    # The latest release's line quotes an approximate model count and the
    # snapshot date; both drift every time the ledger rebuilds. Read the
    # shipped ledger and assert the line still agrees, so a stale figure
    # fails the build instead of reaching a reader.
    import gzip
    import json

    ledger = json.loads(
        gzip.decompress((ROOT / "src/rates/ai/ledger-ai.json.gz").read_bytes())
    )
    rounded = round(len(ledger["models"]) / 1000) * 1000
    snapshot = ledger["snapshot_date"]
    expected = f"~{rounded:,} models ({snapshot} snapshot)"

    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert expected in changelog, (
        f"CHANGELOG's latest-release line is stale against the bundled ledger; "
        f"expected {expected!r}"
    )
