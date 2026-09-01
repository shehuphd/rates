import re
from pathlib import Path

import rates

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    pyproject = (ROOT / "pyproject.toml").read_text()
    declared = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert declared is not None, "pyproject.toml has no top-level version line"
    assert rates.__version__ == declared.group(1)
