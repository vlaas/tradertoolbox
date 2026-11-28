"""The choice of the strikes of one expiration (``s3t.analysis.surface.choose``): the core around
the money, the wings on a fixed lattice, and when a choice is kept."""

import math

import numpy as np

from s3t.analysis.surface import KEEP, Choice, choose

STRIKES = np.arange(400.0, 801.0, 1.0)  # 401 strikes, one apart; index i is the strike 400 + i


def pick(money: float, reach: float | None, before: Choice | None = None, **more: int) -> Choice:
    sizes = {"core": 6, "wing": 30, **more}
    return choose(STRIKES, money, reach, before=before, **sizes)


def strikes(indexes: tuple[int, ...]) -> list[float]:
    return [float(STRIKES[i]) for i in indexes]


def test_the_core_is_so_many_strikes_each_side_of_the_money() -> None:
    choice = pick(600.4, None)
    assert choice.centre == 201  # the first strike at or above the money: 601
    assert choice.core == (195, 207) and choice.pairs == 24
    assert (choice.puts, choice.calls, choice.wing_reach) == ((), (), 0.0)
    assert pick(600.0, None).core == (194, 206)  # a strike at the money counts as above it


def test_the_core_stops_at_the_ends_of_the_listing() -> None:
    assert pick(401.5, None).core == (0, 8)
    assert pick(799.5, None).core == (394, 401)
    assert pick(900.0, None).core == (395, 401)  # the money beyond the last strike


def test_the_wings_reach_so_far_and_are_out_of_the_money_only() -> None:
    choice = pick(600.4, 0.02)
    low, high = 600.4 * math.exp(-0.02), 600.4 * math.exp(0.02)
    assert strikes(choice.puts) == [float(k) for k in range(594, math.ceil(low) - 1, -1)]
    assert strikes(choice.calls) == [float(k) for k in range(607, math.floor(high) + 1)]
    assert (choice.put_step, choice.call_step) == (1, 1)
    assert choice.pairs == 24 + len(choice.puts) + len(choice.calls)
    assert not set(choice.puts) & set(range(*choice.core))
    assert not set(choice.calls) & set(range(*choice.core))


def test_long_wings_are_thinned_on_a_lattice_of_the_listing() -> None:
    choice = pick(600.4, 0.2)  # about 109 strikes below the core and 126 above it
    assert (choice.put_step, choice.call_step) == (4, 5)
    assert max(len(choice.puts), len(choice.calls)) <= 30
    assert all(i % 4 == 0 for i in choice.puts) and all(i % 5 == 0 for i in choice.calls)
    assert list(choice.puts) == sorted(choice.puts, reverse=True)  # from the money outwards
    assert list(choice.calls) == sorted(choice.calls)
    assert STRIKES[choice.puts[-1]] >= 600.4 * math.exp(-0.2) > STRIKES[choice.puts[-1] - 4]
    assert STRIKES[choice.calls[-1]] <= 600.4 * math.exp(0.2) < STRIKES[choice.calls[-1] + 5]
    assert len(pick(600.4, 0.2, wing=5).puts) == 5


def test_a_wing_that_the_core_covers_is_empty() -> None:
    choice = pick(600.4, 0.005)  # three strikes each way: inside the core
    assert (choice.puts, choice.calls) == ((), ()) and choice.wing_reach == 0.005


def test_a_choice_equals_itself() -> None:
    """The service compares choices to see whether the set changed."""
    assert pick(600.4, None) == pick(600.4, None)
    assert pick(600.4, 0.2) == pick(600.4, 0.2)


def test_the_core_stays_until_the_money_has_moved_by_two_strikes() -> None:
    first = pick(600.4, 0.2)
    assert pick(601.4, 0.2, first) == first  # one strike on
    assert pick(599.4, 0.2, first) == first
    moved = pick(602.4, 0.2, first)
    assert moved.centre == 203 and moved.core == (197, 209)
    # The wings keep their lattice: only the strikes at the core change.
    assert (moved.put_step, moved.call_step) == (4, 5) and moved.wing_money == 600.4
    assert set(moved.puts) - set(first.puts) == {196}
    assert set(first.calls) - set(moved.calls) == set()  # 207 and 208 were no lattice strikes
    assert set(moved.calls) <= set(first.calls)


def test_the_wings_are_chosen_anew_when_the_money_or_the_reach_moved_far() -> None:
    first = pick(600.4, 0.2)
    near = 600.4 * math.exp(0.9 * KEEP * 0.2)
    assert pick(near, 0.2, first).wing_money == 600.4  # kept
    far = 600.4 * math.exp(1.1 * KEEP * 0.2)
    again = pick(far, 0.2, first)
    assert again.wing_money == far and again.calls[-1] > first.calls[-1]
    assert pick(600.4, 0.2 * (1 + 0.9 * KEEP), first).wing_reach == 0.2
    shorter = pick(600.4, 0.2 * (1 - 1.1 * KEEP), first)
    assert shorter.wing_reach < 0.2 and shorter.puts[-1] > first.puts[-1]


def test_wings_stay_while_the_reach_is_not_known() -> None:
    first = pick(600.4, 0.2)
    assert pick(600.4, None, first) == first
    assert pick(600.4, None, pick(600.4, None)).puts == ()
