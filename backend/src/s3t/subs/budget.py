"""The subscription budget: from what is wanted to what is granted (SUB-02, SUB-03). Pure.

The rule, decided 2026-11-23:

- The classes are served in order, the highest first. Inside a class the consumers are
  served in the order they registered, and each consumer's pairs in the order it gave.
- A pair that is granted already costs nothing: two consumers that want the same pair hold
  one subscription. It counts for the class that got it first.
- A new pair is granted while the total stays inside the limit and, for a candle, the
  candle count stays inside the candle limit. Otherwise it is cut.

So the lowest class is reduced first, the consumer that registered first keeps its pairs,
and each consumer is cut from the end of its own order. The budget counts symbol and event
pairs until WP-0.9 has measured how the broker counts.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from s3t.broker.interface import EventType, Sub
from s3t.subs.feed import ClassUse, ConsumerUse, Priority, Reduction


@dataclass(frozen=True)
class Limits:
    total: int
    """Subscriptions the manager may grant."""
    candles: int
    """Candle subscriptions among them."""


@dataclass(frozen=True)
class Wanted:
    """The demand of one consumer. The order of a sequence of these is the order of
    registration."""

    name: str
    priority: Priority
    subs: tuple[Sub, ...]


@dataclass(frozen=True)
class Grant:
    subs: tuple[Sub, ...]
    """What is granted, in the order it is to be sent: the highest class first."""
    priorities: tuple[Priority, ...]
    """The class each of ``subs`` counts for."""
    candles: int
    reductions: tuple[Reduction, ...]
    """Who was reduced and by how much."""
    classes: tuple[ClassUse, ...]
    consumers: tuple[ConsumerUse, ...]
    """In the order of registration."""


EMPTY = Grant((), (), 0, (), (), ())


def allocate(wanted: Sequence[Wanted], limits: Limits, at: datetime) -> Grant:
    """Grant what the limits allow. ``at`` is the time written into the reductions."""
    granted: dict[Sub, Priority] = {}
    candles = 0
    reductions: list[Reduction] = []
    use: dict[str, ConsumerUse] = {}
    asked: dict[Priority, set[Sub]] = {}
    order = sorted(range(len(wanted)), key=lambda i: wanted[i].priority)  # stable
    for i in order:
        consumer = wanted[i]
        own = dict.fromkeys(consumer.subs)  # a pair stated twice is one pair
        asked.setdefault(consumer.priority, set()).update(own)
        cut: list[Sub] = []
        for sub in own:
            if sub in granted:
                continue
            candle = sub.event is EventType.CANDLE
            if len(granted) >= limits.total or (candle and candles >= limits.candles):
                cut.append(sub)
                continue
            granted[sub] = consumer.priority
            candles += candle
        got = len(own) - len(cut)
        use[consumer.name] = ConsumerUse(consumer.name, consumer.priority, len(own), got)
        if cut:
            reduction = Reduction(at, consumer.name, consumer.priority, len(own), got, tuple(cut))
            reductions.append(reduction)
    classes = tuple(
        ClassUse(
            priority,
            wanted=len(subs),
            granted=sum(1 for sub in subs if sub in granted),
            counted=sum(1 for p in granted.values() if p is priority),
        )
        for priority, subs in sorted(asked.items())
    )
    return Grant(
        subs=tuple(granted),
        priorities=tuple(granted.values()),
        candles=candles,
        reductions=tuple(reductions),
        classes=classes,
        consumers=tuple(use[w.name] for w in wanted),
    )
