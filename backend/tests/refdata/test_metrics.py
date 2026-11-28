"""One read of the market metrics a day serves the dividends and the chain (REF-02, REF-03). The
rules of the read itself are tested through ``Dividends`` in ``test_dividends.py``."""

from datetime import date
from typing import cast

import pytest

from s3t.broker.interface import Broker, EarningsFacts, MarketMetrics
from s3t.refdata.dividends import Dividends
from s3t.refdata.metrics import Metrics

from .test_dividends import SPY, Stub

pytestmark = pytest.mark.anyio


async def test_one_read_serves_the_dividends_and_the_earnings() -> None:
    earnings = EarningsFacts(date(2027, 1, 28), "after_close", False)

    class WithEarnings(Stub):
        async def market_metrics(self, symbols):  # type: ignore[override]
            found = await super().market_metrics(symbols)
            return [MarketMetrics(m.symbol, m.dividend, earnings) for m in found]

    stub = WithEarnings({"SPY": SPY})
    metrics = Metrics(cast(Broker, stub))
    dividends = Dividends(metrics)
    assert (await dividends.of("SPY")).last == SPY
    found = await metrics.of("SPY")
    assert found is not None and found.earnings == earnings
    assert await metrics.of("/ES") is None
    assert stub.calls == [["SPY"], ["/ES"]]
    assert repr(metrics) == "Metrics(2 held)"
