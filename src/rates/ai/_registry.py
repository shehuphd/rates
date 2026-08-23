"""The Registry: the envelope around a set of model records, and the query
surface over them.

``filter(...)`` narrows by facts the caller supplies and can return zero,
one, or many matches; ``sort_by(...)`` orders, with the direction always
stated explicitly. Neither encodes a view on which model to pick.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Callable, Iterator, Optional

from ._model import Model, _parse_date

# String fields matched case-insensitively, either whole-value (the bare
# name) or substring (the *_contains spelling). "model" is the caller-facing
# name for Model.id.
_STRING_FIELDS = {
    "model": lambda m: m.id,
    "provider": lambda m: m.provider,
    "family": lambda m: m.family,
    "type": lambda m: m.type,
}

_BOOL_FIELDS = {
    "tool_call": lambda m: m.tool_call,
    "structured_output": lambda m: m.structured_output,
}

def _valid_criteria() -> list[str]:
    names = list(_STRING_FIELDS)
    names += [f"{n}_contains" for n in _STRING_FIELDS]
    names += list(_BOOL_FIELDS)
    names += [
        "currency",
        "status",
        "modality_input",
        "modality_output",
        "price_min",
        "price_max",
        "price_unit",
    ]
    return sorted(names)


@dataclass(frozen=True)
class Source:
    """One upstream source consulted for a release, with its role and
    whether it could be reached."""

    name: str
    fetched_at: Optional[date] = None
    role: Optional[str] = None
    status: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        return cls(
            name=data["name"],
            fetched_at=_parse_date(data.get("fetched_at")),
            role=data.get("role"),
            status=data.get("status"),
        )


@dataclass(frozen=True)
class Registry:
    """A set of model records plus the envelope metadata that travels with
    them (schema version, snapshot date, sources consulted).

    ``filter`` and ``sort_by`` each return a new Registry with the same
    envelope, so queries chain and the metadata is never discarded.
    """

    schema_version: Optional[str] = None
    universe: str = "ai"
    snapshot_date: Optional[date] = None
    sources: tuple[Source, ...] = ()
    models: tuple[Model, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Registry":
        return cls(
            schema_version=data.get("schema_version"),
            universe=data.get("universe", "ai"),
            snapshot_date=_parse_date(data.get("snapshot_date")),
            sources=tuple(Source.from_dict(s) for s in data.get("sources", [])),
            models=tuple(Model.from_dict(m) for m in data.get("models", [])),
        )

    def __iter__(self) -> Iterator[Model]:
        return iter(self.models)

    def __len__(self) -> int:
        return len(self.models)

    def price_units(self) -> list[str]:
        """Every billing unit any model in this registry carries, sorted."""
        units: set[str] = set()
        for m in self.models:
            units.update(m.price.units)
        return sorted(units)

    def filter(self, **criteria: Any) -> "Registry":
        """Narrow to models matching every supplied criterion.

        String criteria (``model``, ``provider``, ``family``, ``type``)
        match the whole value, case-insensitively. Their ``*_contains``
        spellings match a substring instead; nothing broadens unless the
        caller wrote the broader form. ``price_min``/``price_max`` always
        need an explicit ``price_unit`` naming the billing unit to compare
        against; a model that doesn't bill on that unit never matches a
        price constraint. Unknown criteria raise, they're never ignored.
        """
        criteria = dict(criteria)
        price_unit = criteria.pop("price_unit", None)
        has_bound = "price_min" in criteria or "price_max" in criteria
        if price_unit is not None and not has_bound:
            raise ValueError(
                "price_unit has no effect without price_min or price_max"
            )
        if has_bound and price_unit is None:
            raise ValueError(
                "price_min/price_max need a price_unit to compare against; "
                "units in this registry: " + ", ".join(self.price_units())
            )

        predicates = [
            self._predicate(name, value, price_unit)
            for name, value in criteria.items()
        ]

        matched = tuple(
            m for m in self.models if all(p(m) for p in predicates)
        )
        return replace(self, models=matched)

    def sort_by(self, field_name: str, *, descending: bool) -> "Registry":
        """Order models by one field, direction stated explicitly.

        ``field_name`` is a model attribute (``"id"``, ``"provider"``) or a
        price unit as ``"price.<unit>"`` (``"price.input_mtok"``). Models
        without a value for the field go last, in either direction.
        """
        keyed: list[tuple[Any, Model]] = []
        missing: list[Model] = []
        for m in self.models:
            value = self._sort_value(m, field_name)
            if value is None:
                missing.append(m)
            else:
                keyed.append((value, m))

        keyed.sort(key=lambda pair: pair[0], reverse=descending)
        ordered = tuple(m for _, m in keyed) + tuple(missing)
        return replace(self, models=ordered)

    def _predicate(
        self, name: str, value: Any, price_unit: Optional[str]
    ) -> Callable[[Model], bool]:
        if name in _STRING_FIELDS:
            getter = _STRING_FIELDS[name]
            want = str(value).casefold()
            return lambda m: (
                getter(m) is not None and getter(m).casefold() == want
            )

        if name.endswith("_contains") and name[: -len("_contains")] in _STRING_FIELDS:
            getter = _STRING_FIELDS[name[: -len("_contains")]]
            want = str(value).casefold()
            return lambda m: (
                getter(m) is not None and want in getter(m).casefold()
            )

        if name in _BOOL_FIELDS:
            getter = _BOOL_FIELDS[name]
            return lambda m: getter(m) is value

        if name == "currency":
            want = str(value).casefold()
            return lambda m: (
                m.price.currency is not None
                and m.price.currency.casefold() == want
            )

        if name == "status":
            want = str(value).casefold()
            return lambda m: (
                m.lifecycle.status is not None
                and m.lifecycle.status.casefold() == want
            )

        if name == "modality_input":
            want = str(value).casefold()
            return lambda m: want in (v.casefold() for v in m.modalities.input)

        if name == "modality_output":
            want = str(value).casefold()
            return lambda m: want in (v.casefold() for v in m.modalities.output)

        if name == "price_min":
            return lambda m: (
                m.price.get(price_unit) is not None
                and m.price.get(price_unit) >= value
            )

        if name == "price_max":
            return lambda m: (
                m.price.get(price_unit) is not None
                and m.price.get(price_unit) <= value
            )

        raise TypeError(
            f"unknown filter criterion {name!r}; valid criteria: "
            + ", ".join(_valid_criteria())
        )

    def _sort_value(self, model: Model, field_name: str) -> Any:
        if field_name.startswith("price."):
            return model.price.get(field_name[len("price."):])
        if not hasattr(model, field_name):
            raise TypeError(
                f"unknown sort field {field_name!r}; use a Model attribute "
                'or "price.<unit>"'
            )
        return getattr(model, field_name)
