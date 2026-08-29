"""The domain registry: what every domain IS, in one place.

Adding a domain is meant to be a data entry, not new code. The presentation a
domain gets on the CLI (columns, examples, flag help) lives with the CLI; the
*semantic* facts that shared logic reads, how to load it, which access tiers it
supports, when its data goes stale, what its headline rate units are, and the
domain's own word for the identity criterion, live here, so the loader and the
staleness check stop hardcoding one domain's name and one domain's number.

A domain's loader is named, not imported, here: `load_target` is a
``"module:attr"`` string resolved on first use, so importing this registry
(which the CLI does on every invocation, tab completion included) does NOT drag
the whole AI domain and its fusion machinery into the process. Only actually
loading a registry pays that cost.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, cast


@dataclass(frozen=True)
class DomainSpec:
    """The domain-defining facts shared logic reads.

    ``load_target`` is either a ``"module:attr"`` string (resolved lazily, the
    production case) or a callable used directly (handy for tests). Read the
    resolved loader through the ``load`` property, never the raw field.

    ``fetch_tiers`` is which access tiers this domain supports, in the order a
    caller would escalate through them. A domain whose data can't be shipped as
    a snapshot (a market price, redistribution its source's terms forbid) omits
    ``"bundled"``/``"stable"`` and offers ``"live"`` only; a bare read then
    fails with a clear message instead of serving a snapshot that shouldn't
    exist. ``staleness_days`` is None for a domain with no daily-snapshot notion
    of stale (a live-only domain answers freshness per record, not per release).
    """

    name: str
    load_target: str | Callable[..., Any]
    fetch_tiers: tuple[str, ...]
    staleness_days: int | None
    rate_units: tuple[str, ...]
    identity_kw: str

    @property
    def load(self) -> Callable[..., Any]:
        """The domain's loader, imported on first access. Importing the
        registry never triggers this; only loading a registry does."""
        target = self.load_target
        if callable(target):
            return target
        module_name, attr = target.split(":")
        return cast("Callable[..., Any]", getattr(import_module(module_name), attr))


DOMAINS: dict[str, DomainSpec] = {
    "ai": DomainSpec(
        name="ai",
        load_target="rates.ai:load",
        fetch_tiers=("bundled", "stable", "live"),
        # Mirror of rates.ai._load.STALENESS_THRESHOLD_DAYS, kept as a literal
        # so reading this registry doesn't import the domain; a test asserts the
        # two agree (tests/test_domains.py).
        staleness_days=28,
        # The units an unscoped listing's rate column reaches for first; a
        # record missing both still shows whatever units it does carry.
        rate_units=("input_mtok", "output_mtok"),
        # Core says "id"; "model" is the AI universe's word for it. Quantum
        # sells qubit-time, cloud sells services, so the AI word never appears
        # in the neutral, cross-domain vocabulary.
        identity_kw="model",
    ),
}
