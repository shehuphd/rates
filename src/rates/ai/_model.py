"""Dataclasses for the AI universe's model records.

The field-by-field schema, source attribution, and worked examples live in
ERD.md. These classes mirror that JSON shape; they don't validate against
upstream sources or compute anything, they hold what a ledger or fusion run
produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO date string, passing None through.

    Upstream sources sometimes carry partial dates ("2025-04", "2025");
    those are floored to the first day of the period so they stay
    comparable and sortable alongside full dates.
    """
    if value is None:
        return None
    parts = value.split("-")
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1)
    if len(parts) == 1:
        return date(int(parts[0]), 1, 1)
    return date.fromisoformat(value)


@dataclass(frozen=True)
class Price:
    """A model's price as a flat map of unit name to rate, plus currency.

    Never a single blended number: different model types bill on different
    units (input_mtok, output_per_second, web_search_per_kcount), and one
    model bills on several at once.
    """

    currency: str | None
    units: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Price:
        """Split the JSON's flat price object into currency and unit rates."""
        units = {k: v for k, v in data.items() if k != "currency"}
        return cls(currency=data.get("currency"), units=units)

    def get(self, unit: str) -> float | None:
        """Rate for one unit, or None when the model doesn't bill on it."""
        return self.units.get(unit)


@dataclass(frozen=True)
class PriceTier:
    """A repricing past a usage threshold. ``price`` holds only the units
    that change; unnamed units fall through to the base tier."""

    when_dimension: str
    when_over: int
    price: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceTier:
        when = data.get("when", {})
        return cls(
            when_dimension=when.get("dimension", ""),
            when_over=when.get("over", 0),
            price=dict(data.get("price", {})),
        )


@dataclass(frozen=True)
class PriceDiscrepancy:
    """A recorded disagreement between two sources on one price unit.

    ``chosen_source``/``chosen_value`` is what shipped in ``price``:
    whichever source's underlying data changed more recently, or a fixed
    preference order when freshness can't decide (see ARCHITECTURE.md §
    Resolving price disagreements). ``resolved_by`` names which of the
    two decided: ``"freshness"`` or ``"preference"``.
    """

    field: str
    chosen_source: str
    chosen_value: float
    other_source: str
    other_value: float
    resolved_by: str
    difference_pct: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceDiscrepancy:
        return cls(
            field=data["field"],
            chosen_source=data["chosen_source"],
            chosen_value=data["chosen_value"],
            other_source=data["other_source"],
            other_value=data["other_value"],
            resolved_by=data["resolved_by"],
            difference_pct=data["difference_pct"],
        )


@dataclass(frozen=True)
class ReasoningLevel:
    """One selectable reasoning-effort value: the label an API call needs,
    and its position in this model's own ascending order. Ranks are only
    comparable within one model's levels."""

    label: str
    rank: int


@dataclass(frozen=True)
class Reasoning:
    """A model's reasoning-effort shape. Absent entirely (None on the model)
    when the model has no reasoning capability.

    ``control`` names how the dial works: ``"effort"`` (named levels;
    ``levels``/``range`` apply), ``"budget_tokens"`` (a numeric thinking
    budget; ``budget`` applies), or ``"toggle"`` (on/off only).
    ``effort_parameter_required`` is tri-state: None means no source covers
    the model, never a guessed False.
    """

    control: str | None = None
    effort_parameter_required: bool | None = None
    can_disable_reasoning: bool = False
    levels: tuple[ReasoningLevel, ...] = ()
    range: tuple[int, int] | None = None
    budget: tuple[int | None, int | None] | None = None
    default: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Reasoning:
        levels = tuple(
            ReasoningLevel(label=lv["label"], rank=lv["rank"])
            for lv in data.get("levels", [])
        )
        raw_range = data.get("range")
        if raw_range is not None and len(raw_range) < 2:
            raw_range = None  # a range needs both ends; anything less is absent
        raw_budget = data.get("budget")
        return cls(
            control=data.get("control"),
            effort_parameter_required=data.get("effort_parameter_required"),
            can_disable_reasoning=data.get("can_disable_reasoning", False),
            levels=levels,
            range=(raw_range[0], raw_range[1]) if raw_range is not None else None,
            budget=(
                (raw_budget.get("min"), raw_budget.get("max"))
                if raw_budget is not None
                else None
            ),
            default=data.get("default"),
        )


@dataclass(frozen=True)
class Lifecycle:
    """Whether a model is still viable to build on. ``deprecation_date`` can
    be past (already retired) or future (scheduled sunset, still callable)."""

    status: str | None
    release_date: date | None = None
    deprecation_date: date | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lifecycle:
        return cls(
            status=data.get("status"),
            release_date=_parse_date(data.get("release_date")),
            deprecation_date=_parse_date(data.get("deprecation_date")),
        )


@dataclass(frozen=True)
class Modalities:
    """Content formats crossing the wire, split by direction."""

    input: tuple[str, ...] = ()
    output: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Modalities:
        return cls(
            input=tuple(data.get("input", [])),
            output=tuple(data.get("output", [])),
        )


@dataclass(frozen=True)
class Context:
    """Token limits, split because input and output limits often differ."""

    input: int | None = None
    output: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Context:
        return cls(input=data.get("input"), output=data.get("output"))


@dataclass(frozen=True)
class Model:
    """One model record. Every field is a verified fact carried by an
    upstream source; nothing here ranks or scores the model."""

    provider: str
    id: str
    family: str | None = None
    type: str | None = None
    modalities: Modalities = field(default_factory=Modalities)
    context: Context = field(default_factory=Context)
    tool_call: bool | None = None
    structured_output: bool | None = None
    price: Price = field(default_factory=lambda: Price(currency=None))
    price_tiers: tuple[PriceTier, ...] = ()
    price_discrepancies: tuple[PriceDiscrepancy, ...] = ()
    reasoning: Reasoning | None = None
    lifecycle: Lifecycle = field(default_factory=lambda: Lifecycle(status=None))
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The record in ERD.md's JSON shape, the inverse of from_dict:
        price flat, dates ISO, absent facts omitted rather than nulled."""
        record: dict[str, Any] = {"provider": self.provider, "id": self.id}
        if self.family is not None:
            record["family"] = self.family
        if self.type is not None:
            record["type"] = self.type
        record["modalities"] = {
            "input": list(self.modalities.input),
            "output": list(self.modalities.output),
        }
        record["context"] = {"input": self.context.input, "output": self.context.output}
        if self.tool_call is not None:
            record["tool_call"] = self.tool_call
        if self.structured_output is not None:
            record["structured_output"] = self.structured_output

        price: dict[str, Any] = {}
        if self.price.currency is not None:
            price["currency"] = self.price.currency
        price.update(self.price.units)
        record["price"] = price
        if self.price_tiers:
            record["price_tiers"] = [
                {
                    "when": {"dimension": t.when_dimension, "over": t.when_over},
                    "price": dict(t.price),
                }
                for t in self.price_tiers
            ]
        record["price_discrepancies"] = [
            {
                "field": d.field,
                "chosen_source": d.chosen_source,
                "chosen_value": d.chosen_value,
                "other_source": d.other_source,
                "other_value": d.other_value,
                "resolved_by": d.resolved_by,
                "difference_pct": d.difference_pct,
            }
            for d in self.price_discrepancies
        ]

        if self.reasoning is not None:
            r: dict[str, Any] = {}
            if self.reasoning.control is not None:
                r["control"] = self.reasoning.control
            if self.reasoning.effort_parameter_required is not None:
                r["effort_parameter_required"] = self.reasoning.effort_parameter_required
            r["can_disable_reasoning"] = self.reasoning.can_disable_reasoning
            r["levels"] = [
                {"label": lv.label, "rank": lv.rank} for lv in self.reasoning.levels
            ]
            r["range"] = list(self.reasoning.range) if self.reasoning.range else None
            if self.reasoning.budget is not None:
                r["budget"] = {
                    "min": self.reasoning.budget[0],
                    "max": self.reasoning.budget[1],
                }
            if self.reasoning.default is not None:
                r["default"] = self.reasoning.default
            record["reasoning"] = r

        record["lifecycle"] = {
            "status": self.lifecycle.status,
            "release_date": (
                self.lifecycle.release_date.isoformat()
                if self.lifecycle.release_date
                else None
            ),
            "deprecation_date": (
                self.lifecycle.deprecation_date.isoformat()
                if self.lifecycle.deprecation_date
                else None
            ),
        }
        record["sources"] = dict(self.sources)
        return record

    def price_for(self, **conditions: int) -> Price:
        """The effective price under the given conditions, e.g.
        ``price_for(context=500000)``.

        Applies every matching tier's overrides on top of the base price,
        lowest threshold first, so the highest matching threshold wins per
        unit. With no matching tier (or no tiers at all) this is the base
        price unchanged.
        """
        units = dict(self.price.units)
        matching = sorted(
            (
                t for t in self.price_tiers
                if t.when_dimension in conditions
                and conditions[t.when_dimension] > t.when_over
            ),
            key=lambda t: t.when_over,
        )
        for tier in matching:
            units.update(tier.price)
        return Price(currency=self.price.currency, units=units)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Model:
        reasoning_data = data.get("reasoning")
        lifecycle_data = data.get("lifecycle")
        return cls(
            provider=data["provider"],
            id=data["id"],
            family=data.get("family"),
            type=data.get("type"),
            modalities=Modalities.from_dict(data.get("modalities", {})),
            context=Context.from_dict(data.get("context", {})),
            tool_call=data.get("tool_call"),
            structured_output=data.get("structured_output"),
            price=Price.from_dict(data.get("price", {})),
            price_tiers=tuple(
                PriceTier.from_dict(t) for t in data.get("price_tiers", [])
            ),
            price_discrepancies=tuple(
                PriceDiscrepancy.from_dict(d)
                for d in data.get("price_discrepancies", [])
            ),
            reasoning=(
                Reasoning.from_dict(reasoning_data)
                if reasoning_data is not None
                else None
            ),
            lifecycle=(
                Lifecycle.from_dict(lifecycle_data)
                if lifecycle_data is not None
                else Lifecycle(status=None)
            ),
            sources=dict(data.get("sources", {})),
        )
