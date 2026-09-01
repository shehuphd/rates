"""The resolution ladder: one engine for every value disagreement.

When several sources report different values for one fact (one record's
price on one unit), the shipped value is picked by a fixed ladder of
rungs, each consulted in order:

1. ``origin``: a source that is first-party for the record's provider
   (the horse's mouth speaking about its own product) ends the contest.
2. ``freshness``: whose underlying data for this record changed most
   recently.
3. ``corroboration``: among the tied, the value independent witnesses
   agree on, counting distinct upstream groups rather than raw sources
   so two feeds syncing from one catalog can't vote twice.
4. ``preferred``: pinned overrides only (a source deliberately promoted
   or demoted, with the reason recorded); nothing is hand-ranked here by
   default, the measured rungs below do the ranking work.
5. ``accuracy``: measured contradiction rate against origin checks,
   dated evidence over a window of check runs.
6. ``coverage``: breadth, a late separator only, never a proxy for
   accuracy.
7. ``registry_order``: the declared strict total order, the determinism
   floor, so the ladder can never tie.

Each rung partitions the current field of candidates and keeps its best
class: a rung that leaves several candidates standing (an intra-rung
tie) restricts the field and passes it down; a rung whose evidence can't
rank every candidate in the field (absent, unmeasured) is skipped whole,
because unknown is never zero: a source with no measured wrongness isn't
0% wrong, it's unmeasured, and may not be outranked on evidence nobody
gathered. The ladder ends when one candidate stands, and ``resolved_by``
names the rung that made it one.

The engine is domain-neutral; each domain supplies its own per-source
scorecards (``SourceCard``) and candidate values. Rungs whose evidence
pipelines don't exist yet (corroboration's upstream declarations,
accuracy's contradiction ledger) are inert until the scorecards carry
their data, by the same skip rule, so the ladder ships complete and
rungs start deciding as evidence arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

LADDER = (
    "origin",
    "freshness",
    "corroboration",
    "preferred",
    "accuracy",
    "coverage",
    "registry_order",
)


@dataclass(frozen=True)
class SourceCard:
    """One source's standing, everything the ladder may consult.

    ``origin_providers``: providers this source is first-party for (its
    own resale catalog, its own pricing endpoint). Origin status is a
    property of a (source, record) pair, not of a source: a reseller's
    API is the horse's mouth for its own rows and a witness everywhere
    else.

    ``upstreams``: where this source's data comes from, for
    corroboration's independence weighting. None means undeclared, which
    keeps the corroboration rung inert for any field containing this
    source.

    ``override``: ``"promote"`` or ``"demote"``, with ``override_reason``
    recorded; None means no override, the default for every source.

    ``wrongness``: measured contradiction rate against origin checks, in
    [0, 1], over ``wrongness_runs`` check runs. None means unmeasured.

    ``coverage``: fraction of the current build's records this source
    contributed to, computed per fusion run. None means uncomputed.

    ``registry_rank``: position in the declared strict total order,
    lower is better; unique per source by construction.
    """

    name: str
    registry_rank: int
    origin_providers: tuple[str, ...] = ()
    upstreams: tuple[str, ...] | None = None
    override: str | None = None
    override_reason: str | None = None
    wrongness: float | None = None
    wrongness_runs: int | None = None
    coverage: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """The scorecard as envelope data, so a shipped ledger carries
        the standing every resolution was decided against and any
        record's resolution can be replayed from the ledger alone."""
        record: dict[str, Any] = {
            "registry_rank": self.registry_rank,
            "origin_providers": list(self.origin_providers),
            "upstreams": list(self.upstreams) if self.upstreams is not None else None,
            "wrongness": self.wrongness,
            "coverage": self.coverage,
        }
        if self.override is not None:
            record["override"] = self.override
            record["override_reason"] = self.override_reason
        return record


@dataclass(frozen=True)
class Candidate:
    """One source's reported value for the fact under resolution."""

    source: str
    value: float


@dataclass(frozen=True)
class Resolution:
    """The ladder's answer: who won, and which rung decided."""

    winner: Candidate
    resolved_by: str


def resolve(
    candidates: list[Candidate],
    *,
    cards: dict[str, SourceCard],
    provider: str,
    freshness: dict[str, date | None],
) -> Resolution:
    """Run the ladder over ``candidates`` for one record's one unit.

    ``freshness`` maps source name to the best available date evidence
    of when that source's data for this record last changed (per-record
    where the source supports it, dataset-wide otherwise); None means
    unmeasured. The registry-order floor guarantees a single winner.
    """
    if not candidates:
        raise ValueError("resolve() needs at least one candidate")
    field = list(candidates)

    field, decided = _rung_origin(field, cards, provider)
    if decided:
        return Resolution(field[0], "origin")

    field, decided = _rung_freshness(field, freshness)
    if decided:
        return Resolution(field[0], "freshness")

    field, decided = _rung_corroboration(field, cards)
    if decided:
        return Resolution(field[0], "corroboration")

    field, decided = _rung_preferred(field, cards)
    if decided:
        return Resolution(field[0], "preferred")

    field, decided = _rung_accuracy(field, cards)
    if decided:
        return Resolution(field[0], "accuracy")

    field, decided = _rung_coverage(field, cards)
    if decided:
        return Resolution(field[0], "coverage")

    winner = min(field, key=lambda c: cards[c.source].registry_rank)
    return Resolution(winner, "registry_order")


def _keep_best(
    field: list[Candidate], keep: list[Candidate]
) -> tuple[list[Candidate], bool]:
    """A rung's outcome: the surviving class, and whether it decided
    (shrank the field to one). A rung that keeps everything separated
    nothing; a rung that keeps several restricted the field for the
    rungs below."""
    if not keep or len(keep) == len(field):
        return field, False
    return keep, len(keep) == 1


def _rung_origin(
    field: list[Candidate], cards: dict[str, SourceCard], provider: str
) -> tuple[list[Candidate], bool]:
    """First-party sources for this record's provider end the contest;
    several of them (a provider's page and its API disagreeing) restrict
    the field to themselves and the rungs below separate them."""
    origin = [c for c in field if provider in cards[c.source].origin_providers]
    return _keep_best(field, origin)


def _rung_freshness(
    field: list[Candidate], freshness: dict[str, date | None]
) -> tuple[list[Candidate], bool]:
    """Strictly freshest update evidence wins; ties restrict. Skipped
    whole unless every candidate in the field is measured: an unmeasured
    source can't be declared staler on evidence nobody gathered."""
    dates = {c.source: freshness.get(c.source) for c in field}
    if any(d is None for d in dates.values()):
        return field, False
    newest = max(d for d in dates.values() if d is not None)
    keep = [c for c in field if dates[c.source] == newest]
    return _keep_best(field, keep)


def _rung_corroboration(
    field: list[Candidate], cards: dict[str, SourceCard]
) -> tuple[list[Candidate], bool]:
    """The value the most independent witnesses agree on, counting
    distinct upstream groups so shared upstreams can't vote twice.
    Skipped whole unless every candidate's upstreams are declared."""
    if any(cards[c.source].upstreams is None for c in field):
        return field, False
    votes: dict[float, set[tuple[str, ...]]] = {}
    for c in field:
        votes.setdefault(c.value, set()).add(tuple(sorted(cards[c.source].upstreams or ())))
    best = max(len(groups) for groups in votes.values())
    winning_values = [v for v, groups in votes.items() if len(groups) == best]
    if len(winning_values) != 1:
        return field, False
    keep = [c for c in field if c.value == winning_values[0]]
    return _keep_best(field, keep)


def _rung_preferred(
    field: list[Candidate], cards: dict[str, SourceCard]
) -> tuple[list[Candidate], bool]:
    """Pinned overrides only: promoted sources outrank the default
    class, which outranks demoted ones. With no overrides in the field
    (the normal state) this rung separates nothing."""
    rank = {"promote": 0, None: 1, "demote": 2}
    best = min(rank[cards[c.source].override] for c in field)
    keep = [c for c in field if rank[cards[c.source].override] == best]
    return _keep_best(field, keep)


def _rung_accuracy(
    field: list[Candidate], cards: dict[str, SourceCard]
) -> tuple[list[Candidate], bool]:
    """Lowest measured contradiction rate wins. Skipped whole unless
    every candidate in the field is measured (unknown is never zero)."""
    if any(cards[c.source].wrongness is None for c in field):
        return field, False
    best = min(cards[c.source].wrongness or 0.0 for c in field)
    keep = [c for c in field if cards[c.source].wrongness == best]
    return _keep_best(field, keep)


def _rung_coverage(
    field: list[Candidate], cards: dict[str, SourceCard]
) -> tuple[list[Candidate], bool]:
    """Broadest contribution to the current build wins, a late separator
    only. Skipped whole unless computed for every candidate."""
    if any(cards[c.source].coverage is None for c in field):
        return field, False
    best = max(cards[c.source].coverage or 0.0 for c in field)
    keep = [c for c in field if cards[c.source].coverage == best]
    return _keep_best(field, keep)
