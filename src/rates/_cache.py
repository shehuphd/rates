"""The per-user cache directory, shared by every domain and the CLI.

Kept in the core, not in a domain, so the CLI's tab-completion path can find
the cache directory without importing a domain (and its data machinery) just to
resolve a path. Callers reach it as ``_cache.cache_dir()`` through the module so
one test fixture can redirect every caller at once.
"""

from __future__ import annotations

from pathlib import Path


def cache_dir() -> Path:
    """The per-user cache dir at ``~/.cache/rates``, created ``0700``.

    Never under a predictable name in the world-shared temp root, where another
    local user could pre-create a file and feed fabricated data to this one.
    """
    path = Path.home() / ".cache" / "rates"
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path
