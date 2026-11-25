from s3t.wire.v1 import market_pb2 as _market_pb2
from s3t.wire.v1 import status_pb2 as _status_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class NoteKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    NOTE_KIND_UNSPECIFIED: _ClassVar[NoteKind]
    NOTE_KIND_UNKNOWN: _ClassVar[NoteKind]
    NOTE_KIND_CUT: _ClassVar[NoteKind]
    NOTE_KIND_UNREACHABLE: _ClassVar[NoteKind]

class RefusalKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    REFUSAL_KIND_UNSPECIFIED: _ClassVar[RefusalKind]
    REFUSAL_KIND_INVALID: _ClassVar[RefusalKind]
    REFUSAL_KIND_UNKNOWN_TOPIC: _ClassVar[RefusalKind]
    REFUSAL_KIND_UNAVAILABLE: _ClassVar[RefusalKind]
    REFUSAL_KIND_FAILED: _ClassVar[RefusalKind]

class ByeKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    BYE_KIND_UNSPECIFIED: _ClassVar[ByeKind]
    BYE_KIND_SESSION_ENDED: _ClassVar[ByeKind]
    BYE_KIND_PROTOCOL: _ClassVar[ByeKind]
NOTE_KIND_UNSPECIFIED: NoteKind
NOTE_KIND_UNKNOWN: NoteKind
NOTE_KIND_CUT: NoteKind
NOTE_KIND_UNREACHABLE: NoteKind
REFUSAL_KIND_UNSPECIFIED: RefusalKind
REFUSAL_KIND_INVALID: RefusalKind
REFUSAL_KIND_UNKNOWN_TOPIC: RefusalKind
REFUSAL_KIND_UNAVAILABLE: RefusalKind
REFUSAL_KIND_FAILED: RefusalKind
BYE_KIND_UNSPECIFIED: ByeKind
BYE_KIND_SESSION_ENDED: ByeKind
BYE_KIND_PROTOCOL: ByeKind

class ClientMessage(_message.Message):
    __slots__ = ("subscribe", "unsubscribe")
    SUBSCRIBE_FIELD_NUMBER: _ClassVar[int]
    UNSUBSCRIBE_FIELD_NUMBER: _ClassVar[int]
    subscribe: Subscribe
    unsubscribe: Unsubscribe
    def __init__(self, subscribe: _Optional[_Union[Subscribe, _Mapping]] = ..., unsubscribe: _Optional[_Union[Unsubscribe, _Mapping]] = ...) -> None: ...

class Subscribe(_message.Message):
    __slots__ = ("id", "status", "quotes")
    ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    QUOTES_FIELD_NUMBER: _ClassVar[int]
    id: int
    status: StatusTopic
    quotes: QuotesTopic
    def __init__(self, id: _Optional[int] = ..., status: _Optional[_Union[StatusTopic, _Mapping]] = ..., quotes: _Optional[_Union[QuotesTopic, _Mapping]] = ...) -> None: ...

class Unsubscribe(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: int
    def __init__(self, id: _Optional[int] = ...) -> None: ...

class StatusTopic(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class QuotesTopic(_message.Message):
    __slots__ = ("symbol",)
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    symbol: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, symbol: _Optional[_Iterable[str]] = ...) -> None: ...

class ServerMessage(_message.Message):
    __slots__ = ("id", "snapshot", "status", "quotes", "refused", "heartbeat", "bye")
    ID_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    QUOTES_FIELD_NUMBER: _ClassVar[int]
    REFUSED_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    BYE_FIELD_NUMBER: _ClassVar[int]
    id: int
    snapshot: bool
    status: _status_pb2.Status
    quotes: Quotes
    refused: Refused
    heartbeat: Heartbeat
    bye: Bye
    def __init__(self, id: _Optional[int] = ..., snapshot: _Optional[bool] = ..., status: _Optional[_Union[_status_pb2.Status, _Mapping]] = ..., quotes: _Optional[_Union[Quotes, _Mapping]] = ..., refused: _Optional[_Union[Refused, _Mapping]] = ..., heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ..., bye: _Optional[_Union[Bye, _Mapping]] = ...) -> None: ...

class Quotes(_message.Message):
    __slots__ = ("rows", "notes")
    ROWS_FIELD_NUMBER: _ClassVar[int]
    NOTES_FIELD_NUMBER: _ClassVar[int]
    rows: _market_pb2.QuoteRows
    notes: Notes
    def __init__(self, rows: _Optional[_Union[_market_pb2.QuoteRows, _Mapping]] = ..., notes: _Optional[_Union[Notes, _Mapping]] = ...) -> None: ...

class Notes(_message.Message):
    __slots__ = ("note",)
    NOTE_FIELD_NUMBER: _ClassVar[int]
    note: _containers.RepeatedCompositeFieldContainer[Note]
    def __init__(self, note: _Optional[_Iterable[_Union[Note, _Mapping]]] = ...) -> None: ...

class Note(_message.Message):
    __slots__ = ("symbol", "kind", "text")
    SYMBOL_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    symbol: str
    kind: NoteKind
    text: str
    def __init__(self, symbol: _Optional[str] = ..., kind: _Optional[_Union[NoteKind, str]] = ..., text: _Optional[str] = ...) -> None: ...

class Refused(_message.Message):
    __slots__ = ("kind", "reason")
    KIND_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    kind: RefusalKind
    reason: str
    def __init__(self, kind: _Optional[_Union[RefusalKind, str]] = ..., reason: _Optional[str] = ...) -> None: ...

class Heartbeat(_message.Message):
    __slots__ = ("data_time_us",)
    DATA_TIME_US_FIELD_NUMBER: _ClassVar[int]
    data_time_us: float
    def __init__(self, data_time_us: _Optional[float] = ...) -> None: ...

class Bye(_message.Message):
    __slots__ = ("kind", "reason")
    KIND_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    kind: ByeKind
    reason: str
    def __init__(self, kind: _Optional[_Union[ByeKind, str]] = ..., reason: _Optional[str] = ...) -> None: ...
