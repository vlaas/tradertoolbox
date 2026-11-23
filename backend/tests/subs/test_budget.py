"""The subscription budget (SUB-02, SUB-03): classes, duplicates, the total and the candle
limit, the order inside a class, who is cut and by how much."""

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from s3t.broker.interface import CandlePeriod, EventType, Sub
from s3t.subs.budget import EMPTY, Limits, Wanted, allocate
from s3t.subs.feed import ClassUse, ConsumerUse, Priority

AT = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
DAY = CandlePeriod(1, "day")
WIDE = Limits(total=1000, candles=100)


def quotes(prefix: str, n: int) -> tuple[Sub, ...]:
    return tuple(Sub(f"{prefix}{i}", EventType.QUOTE) for i in range(n))


def candles(prefix: str, n: int) -> tuple[Sub, ...]:
    return tuple(Sub(f"{prefix}{i}", EventType.CANDLE, DAY) for i in range(n))


def test_everything_is_granted_inside_the_budget() -> None:
    views, background = quotes("V", 3), quotes("B", 2)
    grant = allocate(
        [Wanted("scan", Priority.BACKGROUND, background), Wanted("chain", Priority.VIEWS, views)],
        WIDE,
        AT,
    )
    # The sending order: the higher class first, whatever the order of registration.
    assert grant.subs == views + background
    assert grant.priorities == (Priority.VIEWS,) * 3 + (Priority.BACKGROUND,) * 2
    assert grant.reductions == () and grant.candles == 0
    assert grant.consumers == (
        ConsumerUse("scan", Priority.BACKGROUND, 2, 2),
        ConsumerUse("chain", Priority.VIEWS, 3, 3),
    )
    assert grant.classes == (
        ClassUse(Priority.VIEWS, 3, 3, 3),
        ClassUse(Priority.BACKGROUND, 2, 2, 2),
    )
    assert allocate([], WIDE, AT) == EMPTY


def test_the_lowest_class_is_reduced_first() -> None:
    wanted = [
        Wanted("scan", Priority.BACKGROUND, quotes("B", 4)),
        Wanted("chain", Priority.VIEWS, quotes("V", 4)),
        Wanted("recorder", Priority.RECORDER, quotes("R", 4)),
        Wanted("positions", Priority.POSITIONS, quotes("P", 4)),
    ]
    grant = allocate(wanted, Limits(total=10, candles=0), AT)
    assert grant.subs == quotes("R", 4) + quotes("P", 4) + quotes("V", 2)
    by_name = {r.consumer: r for r in grant.reductions}
    assert set(by_name) == {"chain", "scan"}
    chain, scan = by_name["chain"], by_name["scan"]
    # Cut from the end of the consumer's own order, and the reduction says by how much.
    assert (chain.wanted, chain.granted, chain.cut) == (4, 2, quotes("V", 4)[2:])
    assert (scan.wanted, scan.granted, scan.cut) == (4, 0, quotes("B", 4))
    assert chain.at == AT and chain.priority is Priority.VIEWS
    assert [c.counted for c in grant.classes] == [4, 4, 2, 0]
    assert sum(c.counted for c in grant.classes) == len(grant.subs) == 10


def test_inside_a_class_the_first_registered_keeps_its_pairs() -> None:
    first, second = quotes("A", 3), quotes("B", 3)
    grant = allocate(
        [Wanted("first", Priority.VIEWS, first), Wanted("second", Priority.VIEWS, second)],
        Limits(total=4, candles=0),
        AT,
    )
    assert grant.subs == first + second[:1]
    (reduction,) = grant.reductions
    assert reduction.consumer == "second" and reduction.cut == second[1:]


def test_a_pair_wanted_twice_is_held_once() -> None:
    shared = quotes("S", 2)
    wanted = [
        Wanted("scan", Priority.BACKGROUND, shared + quotes("B", 2) + shared),
        Wanted("chain", Priority.VIEWS, quotes("V", 1) + shared),
    ]
    grant = allocate(wanted, Limits(total=4, candles=0), AT)
    # Three pairs for the views, one left for the background. The shared pairs cost the
    # background nothing, so they are granted to it though it is cut.
    assert grant.subs == quotes("V", 1) + shared + quotes("B", 1)
    (reduction,) = grant.reductions
    assert (reduction.consumer, reduction.wanted, reduction.granted) == ("scan", 4, 3)
    assert reduction.cut == quotes("B", 2)[1:]
    views, background = grant.classes
    assert (views.wanted, views.granted, views.counted) == (3, 3, 3)
    assert (background.wanted, background.granted, background.counted) == (4, 3, 1)


def test_the_candle_limit_is_its_own() -> None:
    wanted = [
        Wanted("chart", Priority.VIEWS, candles("C", 3) + quotes("Q", 2)),
        Wanted("scan", Priority.BACKGROUND, candles("D", 1) + quotes("B", 1)),
    ]
    grant = allocate(wanted, Limits(total=100, candles=2), AT)
    assert grant.candles == 2
    assert grant.subs == candles("C", 2) + quotes("Q", 2) + quotes("B", 1)
    cuts = {r.consumer: r.cut for r in grant.reductions}
    # A candle beyond the candle limit is cut; the pairs after it are still granted.
    assert cuts == {"chart": candles("C", 3)[2:], "scan": candles("D", 1)}
    # A different start is a different subscription (carried to R2, plan section 8).
    again = Sub("C0", EventType.CANDLE, DAY, AT)
    grant = allocate([Wanted("chart", Priority.VIEWS, (candles("C", 1)[0], again))], WIDE, AT)
    assert grant.candles == 2


SUBS = st.one_of(
    st.builds(Sub, st.sampled_from([f"S{i}" for i in range(12)]), st.just(EventType.QUOTE)),
    st.builds(Sub, st.sampled_from([f"S{i}" for i in range(12)]), st.just(EventType.GREEKS)),
    st.builds(
        Sub, st.sampled_from([f"S{i}" for i in range(6)]), st.just(EventType.CANDLE), st.just(DAY)
    ),
)
DEMANDS = st.lists(
    st.tuples(st.sampled_from(list(Priority)), st.lists(SUBS, max_size=12)), max_size=6
)


@given(DEMANDS, st.integers(0, 30), st.integers(0, 5))
def test_properties_of_a_grant(
    demands: list[tuple[Priority, list[Sub]]], total: int, candle_limit: int
) -> None:
    wanted = [Wanted(f"c{i}", p, tuple(subs)) for i, (p, subs) in enumerate(demands)]
    limits = Limits(total, candle_limit)
    grant = allocate(wanted, limits, AT)
    granted = set(grant.subs)
    asked = {sub for w in wanted for sub in w.subs}

    # Never above a limit, nothing twice, nothing that was not wanted.
    assert len(grant.subs) == len(granted) <= limits.total
    assert grant.candles == sum(s.event is EventType.CANDLE for s in granted) <= limits.candles
    assert granted <= asked
    # The sending order is by class, and the classes account for the whole grant.
    assert list(grant.priorities) == sorted(grant.priorities)
    assert sum(c.counted for c in grant.classes) == len(grant.subs)
    # Every wanted pair is either granted or named in the consumer's reduction.
    cuts = {r.consumer: r.cut for r in grant.reductions}
    for consumer, use in zip(wanted, grant.consumers, strict=True):
        own = set(consumer.subs)
        cut = set(cuts.get(consumer.name, ()))
        assert cut == own - granted and (use.wanted, use.granted) == (len(own), len(own - cut))
    # While a pair of a class is cut for the total, no lower class adds a pair of its own.
    for reduction in grant.reductions:
        if any(s.event is not EventType.CANDLE for s in reduction.cut):
            lower = [p for p in grant.priorities if p > reduction.priority]
            assert not lower
    # The same demand gives the same grant, and room for everything cuts nothing.
    assert allocate(wanted, limits, AT) == grant
    assert allocate(wanted, Limits(len(asked), len(asked)), AT).reductions == ()
