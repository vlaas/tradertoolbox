"""The instruments and option chains read from the broker (MKT-01).

:class:`Instruments` reads each document once per trading day and hands out the record. Two
requests for the same one at the same time make one call. What was read is forgotten when the
New York date of the data changes, because expirations roll off and strikes are added
overnight. An error is not kept: the next request asks again.

A chain of an equity or an index states no exercise style. It is a property of the option root,
so :meth:`Instruments.exercise` reads one contract per root (note B, section 3). It is not
derived from the kind of the underlying: options on the index OEX are American.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from s3t.broker.instruments import (
    Exercise,
    Future,
    OptionChain,
    OptionContract,
    Security,
)
from s3t.broker.interface import Broker

_NY = ZoneInfo("America/New_York")

type _Key = tuple[str, str]


class Instruments:
    def __init__(self, broker: Broker) -> None:
        self._broker = broker
        self._held: dict[_Key, asyncio.Future[Any]] = {}
        self._day: date | None = None

    def __repr__(self) -> str:
        return f"Instruments({len(self._held)} held)"

    async def security(self, symbol: str) -> Security:
        """A stock, an ETF or an index."""
        return await self._get(("security", symbol), lambda: self._broker.equity(symbol))

    async def future(self, symbol: str) -> Future:
        return await self._get(("future", symbol), lambda: self._broker.future(symbol))

    async def chain(self, underlying: str) -> OptionChain:
        """The option chain of a stock, an ETF or an index."""
        return await self._get(("chain", underlying), lambda: self._broker.option_chain(underlying))

    async def futures_chain(self, root: str) -> OptionChain:
        """The option chain of a futures product, for example ``/ES``."""
        return await self._get(
            ("futures chain", root), lambda: self._broker.futures_option_chain(root)
        )

    async def exercise(self, chain: OptionChain, root: str) -> Exercise | None:
        """The exercise style of an option root; None when the root lists no contract."""
        listed = chain.of_root(root)
        if listed and listed[0].exercise is not None:
            return listed[0].exercise
        contract = _any_contract(chain, root)
        if contract is None:
            return None
        found: OptionContract = await self._get(
            ("contract of", f"{chain.underlying} {root}"),
            lambda: self._broker.equity_option(contract.symbol),
        )
        return found.exercise

    async def _get[T](self, key: _Key, load: Callable[[], Awaitable[T]]) -> T:
        today = self._broker.data_time().astimezone(_NY).date()
        if today != self._day:
            self._day = today
            self._held.clear()  # a call under way goes on for those who wait for it
        pending = self._held.get(key)
        if pending is None:
            pending = self._held[key] = asyncio.ensure_future(load())
            # In a callback, because every caller may have given up before the call fails.
            pending.add_done_callback(lambda done: self._drop_a_failure(key, done))
        # One caller that gives up must not end the call for the others.
        return await asyncio.shield(pending)

    def _drop_a_failure(self, key: _Key, done: asyncio.Future[Any]) -> None:
        failed = done.cancelled() or done.exception() is not None
        if failed and self._held.get(key) is done:
            del self._held[key]


def _any_contract(chain: OptionChain, root: str) -> OptionContract | None:
    """A contract of the root, from the last expiration and the middle of its strikes: it has
    not expired, and it is not an odd one at the edge."""
    for expiration in reversed(chain.of_root(root)):
        order = (
            expiration.nearest(expiration.strikes[len(expiration.strikes) // 2], 5)
            if (expiration.strikes)
            else ()
        )
        for index in order:
            for right in ("call", "put"):
                contract = expiration.contract(index, right)
                if contract is not None:
                    return contract
    return None
