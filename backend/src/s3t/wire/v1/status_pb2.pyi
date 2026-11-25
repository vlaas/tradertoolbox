from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SourceKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SOURCE_KIND_UNSPECIFIED: _ClassVar[SourceKind]
    SOURCE_KIND_LIVE: _ClassVar[SourceKind]
    SOURCE_KIND_FAKE: _ClassVar[SourceKind]
    SOURCE_KIND_REPLAY: _ClassVar[SourceKind]

class StreamPhase(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STREAM_PHASE_UNSPECIFIED: _ClassVar[StreamPhase]
    STREAM_PHASE_IDLE: _ClassVar[StreamPhase]
    STREAM_PHASE_CONNECTING: _ClassVar[StreamPhase]
    STREAM_PHASE_CONNECTED: _ClassVar[StreamPhase]
    STREAM_PHASE_STALLED: _ClassVar[StreamPhase]
    STREAM_PHASE_DISCONNECTED: _ClassVar[StreamPhase]
    STREAM_PHASE_CLOSED: _ClassVar[StreamPhase]

class DemandClass(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DEMAND_CLASS_UNSPECIFIED: _ClassVar[DemandClass]
    DEMAND_CLASS_RECORDER: _ClassVar[DemandClass]
    DEMAND_CLASS_POSITIONS: _ClassVar[DemandClass]
    DEMAND_CLASS_VIEWS: _ClassVar[DemandClass]
    DEMAND_CLASS_BACKGROUND: _ClassVar[DemandClass]

class AuthState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AUTH_STATE_UNSPECIFIED: _ClassVar[AuthState]
    AUTH_STATE_NONE: _ClassVar[AuthState]
    AUTH_STATE_VALID: _ClassVar[AuthState]
    AUTH_STATE_DUE: _ClassVar[AuthState]
    AUTH_STATE_LAPSED: _ClassVar[AuthState]
    AUTH_STATE_RENEWING: _ClassVar[AuthState]
    AUTH_STATE_REFUSED: _ClassVar[AuthState]
SOURCE_KIND_UNSPECIFIED: SourceKind
SOURCE_KIND_LIVE: SourceKind
SOURCE_KIND_FAKE: SourceKind
SOURCE_KIND_REPLAY: SourceKind
STREAM_PHASE_UNSPECIFIED: StreamPhase
STREAM_PHASE_IDLE: StreamPhase
STREAM_PHASE_CONNECTING: StreamPhase
STREAM_PHASE_CONNECTED: StreamPhase
STREAM_PHASE_STALLED: StreamPhase
STREAM_PHASE_DISCONNECTED: StreamPhase
STREAM_PHASE_CLOSED: StreamPhase
DEMAND_CLASS_UNSPECIFIED: DemandClass
DEMAND_CLASS_RECORDER: DemandClass
DEMAND_CLASS_POSITIONS: DemandClass
DEMAND_CLASS_VIEWS: DemandClass
DEMAND_CLASS_BACKGROUND: DemandClass
AUTH_STATE_UNSPECIFIED: AuthState
AUTH_STATE_NONE: AuthState
AUTH_STATE_VALID: AuthState
AUTH_STATE_DUE: AuthState
AUTH_STATE_LAPSED: AuthState
AUTH_STATE_RENEWING: AuthState
AUTH_STATE_REFUSED: AuthState

class Status(_message.Message):
    __slots__ = ("source", "data_time_us", "account", "stream", "subscriptions", "rest", "live_since_us")
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    DATA_TIME_US_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_FIELD_NUMBER: _ClassVar[int]
    STREAM_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIPTIONS_FIELD_NUMBER: _ClassVar[int]
    REST_FIELD_NUMBER: _ClassVar[int]
    LIVE_SINCE_US_FIELD_NUMBER: _ClassVar[int]
    source: Source
    data_time_us: float
    account: str
    stream: StreamState
    subscriptions: Subscriptions
    rest: RestState
    live_since_us: float
    def __init__(self, source: _Optional[_Union[Source, _Mapping]] = ..., data_time_us: _Optional[float] = ..., account: _Optional[str] = ..., stream: _Optional[_Union[StreamState, _Mapping]] = ..., subscriptions: _Optional[_Union[Subscriptions, _Mapping]] = ..., rest: _Optional[_Union[RestState, _Mapping]] = ..., live_since_us: _Optional[float] = ...) -> None: ...

class Source(_message.Message):
    __slots__ = ("kind", "mark", "recorded_on")
    KIND_FIELD_NUMBER: _ClassVar[int]
    MARK_FIELD_NUMBER: _ClassVar[int]
    RECORDED_ON_FIELD_NUMBER: _ClassVar[int]
    kind: SourceKind
    mark: str
    recorded_on: str
    def __init__(self, kind: _Optional[_Union[SourceKind, str]] = ..., mark: _Optional[str] = ..., recorded_on: _Optional[str] = ...) -> None: ...

class StreamState(_message.Message):
    __slots__ = ("phase", "since_us", "reason", "connected_at_us", "last_data_at_us", "reconnects", "stalls", "resubscribing", "last_gap", "aggregation_period", "stalled")
    PHASE_FIELD_NUMBER: _ClassVar[int]
    SINCE_US_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    CONNECTED_AT_US_FIELD_NUMBER: _ClassVar[int]
    LAST_DATA_AT_US_FIELD_NUMBER: _ClassVar[int]
    RECONNECTS_FIELD_NUMBER: _ClassVar[int]
    STALLS_FIELD_NUMBER: _ClassVar[int]
    RESUBSCRIBING_FIELD_NUMBER: _ClassVar[int]
    LAST_GAP_FIELD_NUMBER: _ClassVar[int]
    AGGREGATION_PERIOD_FIELD_NUMBER: _ClassVar[int]
    STALLED_FIELD_NUMBER: _ClassVar[int]
    phase: StreamPhase
    since_us: float
    reason: str
    connected_at_us: float
    last_data_at_us: float
    reconnects: int
    stalls: int
    resubscribing: bool
    last_gap: Gap
    aggregation_period: float
    stalled: bool
    def __init__(self, phase: _Optional[_Union[StreamPhase, str]] = ..., since_us: _Optional[float] = ..., reason: _Optional[str] = ..., connected_at_us: _Optional[float] = ..., last_data_at_us: _Optional[float] = ..., reconnects: _Optional[int] = ..., stalls: _Optional[int] = ..., resubscribing: _Optional[bool] = ..., last_gap: _Optional[_Union[Gap, _Mapping]] = ..., aggregation_period: _Optional[float] = ..., stalled: _Optional[bool] = ...) -> None: ...

class Gap(_message.Message):
    __slots__ = ("since_us", "until_us", "reason")
    SINCE_US_FIELD_NUMBER: _ClassVar[int]
    UNTIL_US_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    since_us: float
    until_us: float
    reason: str
    def __init__(self, since_us: _Optional[float] = ..., until_us: _Optional[float] = ..., reason: _Optional[str] = ...) -> None: ...

class ClassUse(_message.Message):
    __slots__ = ("demand_class", "wanted", "granted", "counted")
    DEMAND_CLASS_FIELD_NUMBER: _ClassVar[int]
    WANTED_FIELD_NUMBER: _ClassVar[int]
    GRANTED_FIELD_NUMBER: _ClassVar[int]
    COUNTED_FIELD_NUMBER: _ClassVar[int]
    demand_class: DemandClass
    wanted: int
    granted: int
    counted: int
    def __init__(self, demand_class: _Optional[_Union[DemandClass, str]] = ..., wanted: _Optional[int] = ..., granted: _Optional[int] = ..., counted: _Optional[int] = ...) -> None: ...

class Subscriptions(_message.Message):
    __slots__ = ("granted", "budget", "candles", "candle_budget", "sent", "pending", "changes_last_minute", "change_budget", "rate_errors", "lost", "classes")
    GRANTED_FIELD_NUMBER: _ClassVar[int]
    BUDGET_FIELD_NUMBER: _ClassVar[int]
    CANDLES_FIELD_NUMBER: _ClassVar[int]
    CANDLE_BUDGET_FIELD_NUMBER: _ClassVar[int]
    SENT_FIELD_NUMBER: _ClassVar[int]
    PENDING_FIELD_NUMBER: _ClassVar[int]
    CHANGES_LAST_MINUTE_FIELD_NUMBER: _ClassVar[int]
    CHANGE_BUDGET_FIELD_NUMBER: _ClassVar[int]
    RATE_ERRORS_FIELD_NUMBER: _ClassVar[int]
    LOST_FIELD_NUMBER: _ClassVar[int]
    CLASSES_FIELD_NUMBER: _ClassVar[int]
    granted: int
    budget: int
    candles: int
    candle_budget: int
    sent: int
    pending: int
    changes_last_minute: int
    change_budget: int
    rate_errors: int
    lost: int
    classes: _containers.RepeatedCompositeFieldContainer[ClassUse]
    def __init__(self, granted: _Optional[int] = ..., budget: _Optional[int] = ..., candles: _Optional[int] = ..., candle_budget: _Optional[int] = ..., sent: _Optional[int] = ..., pending: _Optional[int] = ..., changes_last_minute: _Optional[int] = ..., change_budget: _Optional[int] = ..., rate_errors: _Optional[int] = ..., lost: _Optional[int] = ..., classes: _Optional[_Iterable[_Union[ClassUse, _Mapping]]] = ...) -> None: ...

class RestState(_message.Message):
    __slots__ = ("auth", "token_expires_at_us", "renews_from_us", "last_renewal_at_us", "renewals", "refusal", "rate", "burst", "requests_last_minute", "held_until_us", "rate_limited")
    AUTH_FIELD_NUMBER: _ClassVar[int]
    TOKEN_EXPIRES_AT_US_FIELD_NUMBER: _ClassVar[int]
    RENEWS_FROM_US_FIELD_NUMBER: _ClassVar[int]
    LAST_RENEWAL_AT_US_FIELD_NUMBER: _ClassVar[int]
    RENEWALS_FIELD_NUMBER: _ClassVar[int]
    REFUSAL_FIELD_NUMBER: _ClassVar[int]
    RATE_FIELD_NUMBER: _ClassVar[int]
    BURST_FIELD_NUMBER: _ClassVar[int]
    REQUESTS_LAST_MINUTE_FIELD_NUMBER: _ClassVar[int]
    HELD_UNTIL_US_FIELD_NUMBER: _ClassVar[int]
    RATE_LIMITED_FIELD_NUMBER: _ClassVar[int]
    auth: AuthState
    token_expires_at_us: float
    renews_from_us: float
    last_renewal_at_us: float
    renewals: int
    refusal: str
    rate: float
    burst: int
    requests_last_minute: int
    held_until_us: float
    rate_limited: int
    def __init__(self, auth: _Optional[_Union[AuthState, str]] = ..., token_expires_at_us: _Optional[float] = ..., renews_from_us: _Optional[float] = ..., last_renewal_at_us: _Optional[float] = ..., renewals: _Optional[int] = ..., refusal: _Optional[str] = ..., rate: _Optional[float] = ..., burst: _Optional[int] = ..., requests_last_minute: _Optional[int] = ..., held_until_us: _Optional[float] = ..., rate_limited: _Optional[int] = ...) -> None: ...
