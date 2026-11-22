from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CheckKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHECK_KIND_UNSPECIFIED: _ClassVar[CheckKind]
    CHECK_KIND_FIRST: _ClassVar[CheckKind]
    CHECK_KIND_SECOND: _ClassVar[CheckKind]
CHECK_KIND_UNSPECIFIED: CheckKind
CHECK_KIND_FIRST: CheckKind
CHECK_KIND_SECOND: CheckKind

class WireCheck(_message.Message):
    __slots__ = ("time_us", "value", "maybe", "column", "text", "kind")
    TIME_US_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    MAYBE_FIELD_NUMBER: _ClassVar[int]
    COLUMN_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    time_us: int
    value: float
    maybe: float
    column: _containers.RepeatedScalarFieldContainer[float]
    text: str
    kind: CheckKind
    def __init__(self, time_us: _Optional[int] = ..., value: _Optional[float] = ..., maybe: _Optional[float] = ..., column: _Optional[_Iterable[float]] = ..., text: _Optional[str] = ..., kind: _Optional[_Union[CheckKind, str]] = ...) -> None: ...
