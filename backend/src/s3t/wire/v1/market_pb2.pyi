from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class QuoteRows(_message.Message):
    __slots__ = ("symbol", "bid", "ask", "bid_size", "ask_size", "last", "last_size", "day_volume", "prev_close", "quote_received_us", "trade_received_us")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    BID_FIELD_NUMBER: _ClassVar[int]
    ASK_FIELD_NUMBER: _ClassVar[int]
    BID_SIZE_FIELD_NUMBER: _ClassVar[int]
    ASK_SIZE_FIELD_NUMBER: _ClassVar[int]
    LAST_FIELD_NUMBER: _ClassVar[int]
    LAST_SIZE_FIELD_NUMBER: _ClassVar[int]
    DAY_VOLUME_FIELD_NUMBER: _ClassVar[int]
    PREV_CLOSE_FIELD_NUMBER: _ClassVar[int]
    QUOTE_RECEIVED_US_FIELD_NUMBER: _ClassVar[int]
    TRADE_RECEIVED_US_FIELD_NUMBER: _ClassVar[int]
    symbol: _containers.RepeatedScalarFieldContainer[str]
    bid: _containers.RepeatedScalarFieldContainer[float]
    ask: _containers.RepeatedScalarFieldContainer[float]
    bid_size: _containers.RepeatedScalarFieldContainer[float]
    ask_size: _containers.RepeatedScalarFieldContainer[float]
    last: _containers.RepeatedScalarFieldContainer[float]
    last_size: _containers.RepeatedScalarFieldContainer[float]
    day_volume: _containers.RepeatedScalarFieldContainer[float]
    prev_close: _containers.RepeatedScalarFieldContainer[float]
    quote_received_us: _containers.RepeatedScalarFieldContainer[float]
    trade_received_us: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, symbol: _Optional[_Iterable[str]] = ..., bid: _Optional[_Iterable[float]] = ..., ask: _Optional[_Iterable[float]] = ..., bid_size: _Optional[_Iterable[float]] = ..., ask_size: _Optional[_Iterable[float]] = ..., last: _Optional[_Iterable[float]] = ..., last_size: _Optional[_Iterable[float]] = ..., day_volume: _Optional[_Iterable[float]] = ..., prev_close: _Optional[_Iterable[float]] = ..., quote_received_us: _Optional[_Iterable[float]] = ..., trade_received_us: _Optional[_Iterable[float]] = ...) -> None: ...
