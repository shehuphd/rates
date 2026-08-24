"""Hygiene checks on the public docs: no references to internal files,
and README links that survive PyPI's rendering.

PyPI freezes each release's rendered README at build time and resolves
relative links against pypi.org, so a leak here is permanent for that
release; these tests make the leak a failing build instead.
"""

import re
from pathlib import Path

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
