"""The neutral, cross-domain record contract.

Every domain (``rates.ai`` today; crypto, cloud, quantum later) has its own
record class with its own domain-specific fields. Code that runs *across*
domains, an unscoped CLI listing, a cross-domain sort, must not depend on any
one of those classes. It depends on this ``Record`` protocol instead: the
small set of members every domain's record carries, named in core vocabulary
rather than one domain's terms.

Vocabulary is a layering boundary here. Core says ``id``; "model" is the AI
domain's word for it, "instrument" or "pair" would be crypto's, "service"
cloud's. A domain maps its own term onto ``id`` at its boundary; the core word
is what appears here. ``price`` is the domain's core rate in its own units,
never a single blended number (see ``SupportsPrice``).

The members are declared as read-only properties, not writable attributes, so
a domain whose field is stricter (``provider: str``) still satisfies a contract
that allows ``str | None``, and a plain dataclass attribute is a valid
implementation of each. The protocols are ``runtime_checkable`` so a domain's
record class can be smoke-tested against the contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class SupportsPrice(Protocol):
    """A record's price: a currency and a flat map of unit name to rate,
    never a single blended number, since one record bills on several units at
    once and different record types bill on different units entirely."""

    @property
    def currency(self) -> str | None: ...

    @property
    def units(self) -> dict[str, float]: ...

    def get(self, unit: str) -> float | None: ...


@runtime_checkable
class SupportsLifecycle(Protocol):
    """Whether the thing is still viable to build on. Only ``status`` is part
    of the cross-domain contract; a domain's own dates live on its own type."""

    @property
    def status(self) -> str | None: ...


@runtime_checkable
class Record(Protocol):
    """One record, seen through the cross-domain contract.

    A domain's record class (``rates.ai.Model`` today) satisfies this
    structurally, without inheriting from it. Cross-domain code depends on this,
    so adding a domain never edits the code that already lists, sorts, or shows
    records for every other domain.
    """

    @property
    def provider(self) -> str | None: ...

    @property
    def id(self) -> str: ...

    @property
    def type(self) -> str | None: ...

    @property
    def price(self) -> SupportsPrice: ...

    @property
    def lifecycle(self) -> SupportsLifecycle: ...

    @property
    def observed_at(self) -> datetime | None: ...
