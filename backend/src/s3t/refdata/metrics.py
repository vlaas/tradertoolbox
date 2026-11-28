"""The market metrics of an underlying, read once per New York day of the data (REF-02, REF-03).

One read of a symbol serves every part that asks for it: the dividend schedule
(``s3t.refdata.dividends``) and the chain's earnings and IV per expiration
(``s3t.analysis.chain``). New in WP-1.2 (2026-11-28); until then ``Dividends`` held the reads
itself. The rule is the one of the instruments (``s3t.market.registry``): a document is read once
per New York day of ``data_time``, callers at the same time share one call, and a read that
fails is not kept.
"""

import asyncio
from datetime import date
from zoneinfo import ZoneInfo

from s3t.broker.interface import Broker, MarketMetrics

_NY = ZoneInfo("America/New_York")


class Metrics:
    def __init__(self, broker: Broker) -> None:
        self._broker = broker
        self._held: dict[str, asyncio.Future[MarketMetrics | None]] = {}
        self._day: date | None = None

    def __repr__(self) -> str:
        return f"Metrics({len(self._held)} held)"

    async def of(self, symbol: str) -> MarketMetrics | None:
        """The metrics of a symbol; None when the broker has none for it (a future)."""
        today = self._broker.data_time().astimezone(_NY).date()
        if today != self._day:
            self._day = today
            self._held.clear()  # a read under way goes on for those who wait for it
        pending = self._held.get(symbol)
        if pending is None:
            pending = self._held[symbol] = asyncio.ensure_future(self._read(symbol))
            pending.add_done_callback(lambda done: self._drop_a_failure(symbol, done))
        return await asyncio.shield(pending)  # one caller that gives up does not end the read

    async def _read(self, symbol: str) -> MarketMetrics | None:
        for metrics in await self._broker.market_metrics([symbol]):
            if metrics.symbol == symbol:
                return metrics
        return None

    def _drop_a_failure(self, symbol: str, done: asyncio.Future[MarketMetrics | None]) -> None:
        failed = done.cancelled() or done.exception() is not None
        if failed and self._held.get(symbol) is done:
            del self._held[symbol]
