"""The Tastytrade broker: the REST client and the DXLink session together (GEN-09).

:class:`Tastytrade` is the implementation of :class:`s3t.broker.interface.Broker`. The REST
part is :class:`TastytradeRest`; this class adds the stream, the data source mark and the
time of the data. The session gets the hard cap of ``[subscriptions]``; the budget, the
pacing and the content of the subscriptions belong to the feed (``s3t.subs``). The fake
broker is this same class on fake transports (``s3t.broker.fake.broker``).
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from s3t.broker.interface import BrokerError, DataSource, StreamSink
from s3t.broker.tastytrade.dxlink import DxLinkSession, regular_hours
from s3t.broker.tastytrade.rest import TastytradeRest
from s3t.broker.tastytrade.ws import Connect, open_websocket
from s3t.config import Config, StreamConfig, SubscriptionsConfig
from s3t.secrets import BrokerSecrets, load_broker_secrets


class Tastytrade(TastytradeRest):
    def __init__(
        self,
        secrets: BrokerSecrets,
        *,
        stream_connect: Connect = open_websocket,
        stream: StreamConfig | None = None,
        subscriptions: SubscriptionsConfig | None = None,
        data_expected: Callable[[datetime], bool] = regular_hours,
        source: DataSource | None = None,
        **rest: Any,
    ) -> None:
        super().__init__(secrets, **rest)
        self._stream_connect = stream_connect
        self._stream_config = stream or StreamConfig()
        self._subscriptions = subscriptions or SubscriptionsConfig()
        self._data_expected = data_expected
        self._source = source or DataSource("live")
        self._session: DxLinkSession | None = None

    def stream(self, sink: StreamSink) -> DxLinkSession:
        if self._session is not None:
            raise BrokerError("the broker has one streaming session, and it exists already")
        self._session = DxLinkSession(
            self.quote_token,
            sink,
            connect=self._stream_connect,
            aggregation_period=self._stream_config.aggregation_period,
            stall_after=self._stream_config.stall_after,
            data_expected=self._data_expected,
            limit=self._subscriptions.limit,
            candle_limit=self._subscriptions.candle_limit,
            clock=self._clock,
            sleep=self._sleep,
            now=self._now,
            rng=self._rng,
        )
        return self._session

    def source(self) -> DataSource:
        return self._source

    def data_time(self) -> datetime:
        return self._now()

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.aclose()
        await super().aclose()


def from_config(
    config: Config,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    data_expected: Callable[[datetime], bool] = regular_hours,
) -> Tastytrade:
    """The live broker for the configured environment file, limits and stream settings.
    ``data_expected`` is the stall rule's "the market is open" (BRK-03); the application passes
    the exchange calendar's, and the tools use the stand-in."""
    secrets = load_broker_secrets(config.paths.env_file)
    return Tastytrade(
        secrets,
        stream=config.stream,
        subscriptions=config.subscriptions,
        rate=config.broker.rest_rate,
        burst=config.broker.rest_burst,
        transport=transport,
        data_expected=data_expected,
    )
