from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GreeksSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    GREEKS_SOURCE_UNSPECIFIED: _ClassVar[GreeksSource]
    GREEKS_SOURCE_OWN: _ClassVar[GreeksSource]
    GREEKS_SOURCE_BROKER: _ClassVar[GreeksSource]
    GREEKS_SOURCE_NONE: _ClassVar[GreeksSource]
GREEKS_SOURCE_UNSPECIFIED: GreeksSource
GREEKS_SOURCE_OWN: GreeksSource
GREEKS_SOURCE_BROKER: GreeksSource
GREEKS_SOURCE_NONE: GreeksSource

class ChainHead(_message.Message):
    __slots__ = ("underlying", "kind", "spot", "spot_source", "data_time_us", "stale_after_s", "earnings", "earnings_time", "earnings_estimated")
    UNDERLYING_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    SPOT_FIELD_NUMBER: _ClassVar[int]
    SPOT_SOURCE_FIELD_NUMBER: _ClassVar[int]
    DATA_TIME_US_FIELD_NUMBER: _ClassVar[int]
    STALE_AFTER_S_FIELD_NUMBER: _ClassVar[int]
    EARNINGS_FIELD_NUMBER: _ClassVar[int]
    EARNINGS_TIME_FIELD_NUMBER: _ClassVar[int]
    EARNINGS_ESTIMATED_FIELD_NUMBER: _ClassVar[int]
    underlying: str
    kind: str
    spot: float
    spot_source: str
    data_time_us: float
    stale_after_s: float
    earnings: str
    earnings_time: str
    earnings_estimated: bool
    def __init__(self, underlying: _Optional[str] = ..., kind: _Optional[str] = ..., spot: _Optional[float] = ..., spot_source: _Optional[str] = ..., data_time_us: _Optional[float] = ..., stale_after_s: _Optional[float] = ..., earnings: _Optional[str] = ..., earnings_time: _Optional[str] = ..., earnings_estimated: _Optional[bool] = ...) -> None: ...

class ChainExpiration(_message.Message):
    __slots__ = ("index", "key", "root", "date", "settlement", "settles_at_us", "kind", "multiplier", "standard", "strikes", "forward", "forward_source", "atm_iv", "expected_move", "expected_move_method", "broker_iv", "ex_dividend", "ex_dividend_projected", "earnings_before", "note")
    INDEX_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    ROOT_FIELD_NUMBER: _ClassVar[int]
    DATE_FIELD_NUMBER: _ClassVar[int]
    SETTLEMENT_FIELD_NUMBER: _ClassVar[int]
    SETTLES_AT_US_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    MULTIPLIER_FIELD_NUMBER: _ClassVar[int]
    STANDARD_FIELD_NUMBER: _ClassVar[int]
    STRIKES_FIELD_NUMBER: _ClassVar[int]
    FORWARD_FIELD_NUMBER: _ClassVar[int]
    FORWARD_SOURCE_FIELD_NUMBER: _ClassVar[int]
    ATM_IV_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_MOVE_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_MOVE_METHOD_FIELD_NUMBER: _ClassVar[int]
    BROKER_IV_FIELD_NUMBER: _ClassVar[int]
    EX_DIVIDEND_FIELD_NUMBER: _ClassVar[int]
    EX_DIVIDEND_PROJECTED_FIELD_NUMBER: _ClassVar[int]
    EARNINGS_BEFORE_FIELD_NUMBER: _ClassVar[int]
    NOTE_FIELD_NUMBER: _ClassVar[int]
    index: int
    key: str
    root: str
    date: str
    settlement: str
    settles_at_us: float
    kind: str
    multiplier: float
    standard: bool
    strikes: int
    forward: float
    forward_source: str
    atm_iv: float
    expected_move: float
    expected_move_method: str
    broker_iv: float
    ex_dividend: _containers.RepeatedScalarFieldContainer[str]
    ex_dividend_projected: _containers.RepeatedScalarFieldContainer[bool]
    earnings_before: bool
    note: str
    def __init__(self, index: _Optional[int] = ..., key: _Optional[str] = ..., root: _Optional[str] = ..., date: _Optional[str] = ..., settlement: _Optional[str] = ..., settles_at_us: _Optional[float] = ..., kind: _Optional[str] = ..., multiplier: _Optional[float] = ..., standard: _Optional[bool] = ..., strikes: _Optional[int] = ..., forward: _Optional[float] = ..., forward_source: _Optional[str] = ..., atm_iv: _Optional[float] = ..., expected_move: _Optional[float] = ..., expected_move_method: _Optional[str] = ..., broker_iv: _Optional[float] = ..., ex_dividend: _Optional[_Iterable[str]] = ..., ex_dividend_projected: _Optional[_Iterable[bool]] = ..., earnings_before: _Optional[bool] = ..., note: _Optional[str] = ...) -> None: ...

class ChainRows(_message.Message):
    __slots__ = ("expiration", "strike", "iv", "call", "put")
    EXPIRATION_FIELD_NUMBER: _ClassVar[int]
    STRIKE_FIELD_NUMBER: _ClassVar[int]
    IV_FIELD_NUMBER: _ClassVar[int]
    CALL_FIELD_NUMBER: _ClassVar[int]
    PUT_FIELD_NUMBER: _ClassVar[int]
    expiration: _containers.RepeatedScalarFieldContainer[int]
    strike: _containers.RepeatedScalarFieldContainer[float]
    iv: _containers.RepeatedScalarFieldContainer[float]
    call: ChainSide
    put: ChainSide
    def __init__(self, expiration: _Optional[_Iterable[int]] = ..., strike: _Optional[_Iterable[float]] = ..., iv: _Optional[_Iterable[float]] = ..., call: _Optional[_Union[ChainSide, _Mapping]] = ..., put: _Optional[_Union[ChainSide, _Mapping]] = ...) -> None: ...

class ChainSide(_message.Message):
    __slots__ = ("symbol", "bid", "ask", "bid_size", "ask_size", "quote_us", "stale", "iv", "delta", "gamma", "theta", "vega", "source", "volume", "open_interest")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    BID_FIELD_NUMBER: _ClassVar[int]
    ASK_FIELD_NUMBER: _ClassVar[int]
    BID_SIZE_FIELD_NUMBER: _ClassVar[int]
    ASK_SIZE_FIELD_NUMBER: _ClassVar[int]
    QUOTE_US_FIELD_NUMBER: _ClassVar[int]
    STALE_FIELD_NUMBER: _ClassVar[int]
    IV_FIELD_NUMBER: _ClassVar[int]
    DELTA_FIELD_NUMBER: _ClassVar[int]
    GAMMA_FIELD_NUMBER: _ClassVar[int]
    THETA_FIELD_NUMBER: _ClassVar[int]
    VEGA_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    OPEN_INTEREST_FIELD_NUMBER: _ClassVar[int]
    symbol: _containers.RepeatedScalarFieldContainer[str]
    bid: _containers.RepeatedScalarFieldContainer[float]
    ask: _containers.RepeatedScalarFieldContainer[float]
    bid_size: _containers.RepeatedScalarFieldContainer[float]
    ask_size: _containers.RepeatedScalarFieldContainer[float]
    quote_us: _containers.RepeatedScalarFieldContainer[float]
    stale: _containers.RepeatedScalarFieldContainer[bool]
    iv: _containers.RepeatedScalarFieldContainer[float]
    delta: _containers.RepeatedScalarFieldContainer[float]
    gamma: _containers.RepeatedScalarFieldContainer[float]
    theta: _containers.RepeatedScalarFieldContainer[float]
    vega: _containers.RepeatedScalarFieldContainer[float]
    source: _containers.RepeatedScalarFieldContainer[GreeksSource]
    volume: _containers.RepeatedScalarFieldContainer[float]
    open_interest: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, symbol: _Optional[_Iterable[str]] = ..., bid: _Optional[_Iterable[float]] = ..., ask: _Optional[_Iterable[float]] = ..., bid_size: _Optional[_Iterable[float]] = ..., ask_size: _Optional[_Iterable[float]] = ..., quote_us: _Optional[_Iterable[float]] = ..., stale: _Optional[_Iterable[bool]] = ..., iv: _Optional[_Iterable[float]] = ..., delta: _Optional[_Iterable[float]] = ..., gamma: _Optional[_Iterable[float]] = ..., theta: _Optional[_Iterable[float]] = ..., vega: _Optional[_Iterable[float]] = ..., source: _Optional[_Iterable[_Union[GreeksSource, str]]] = ..., volume: _Optional[_Iterable[float]] = ..., open_interest: _Optional[_Iterable[float]] = ...) -> None: ...
