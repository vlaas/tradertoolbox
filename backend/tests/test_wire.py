"""The generated Python messages and the cross-language fixture (API-04, QA-05).

The frontend's ``src/wire.test.ts`` decodes the same fixture with the TypeScript code.
"""

import json
import math
from pathlib import Path

from google.protobuf import json_format
from hypothesis import given
from hypothesis import strategies as st

from generate import FIXTURE_VALUES
from s3t.wire.v1 import check_pb2
from s3t.wire.v1.check_pb2 import WireCheck

REPO = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((REPO / "proto" / "fixtures" / "wirecheck.json").read_text())

INT64 = st.integers(min_value=-(2**63), max_value=2**63 - 1)
DOUBLE = st.floats(allow_nan=True, allow_infinity=True)
KINDS = [check_pb2.CHECK_KIND_UNSPECIFIED, check_pb2.CHECK_KIND_FIRST, check_pb2.CHECK_KIND_SECOND]
TEXT = st.text(alphabet=st.characters(exclude_categories=("Cs",)))  # UTF-8 encodable


def same_double(a: float, b: float) -> bool:
    return a == b or (math.isnan(a) and math.isnan(b))


def same(a: WireCheck, b: WireCheck) -> bool:
    return (
        a.time_us == b.time_us
        and same_double(a.value, b.value)
        and a.HasField("maybe") == b.HasField("maybe")
        and (not a.HasField("maybe") or same_double(a.maybe, b.maybe))
        and len(a.column) == len(b.column)
        and all(same_double(x, y) for x, y in zip(a.column, b.column, strict=True))
        and a.text == b.text
        and a.kind == b.kind
    )


def test_runtime_accepts_the_generated_code() -> None:
    # Importing ran the runtime's version check against the protoc that generated the code.
    assert check_pb2.DESCRIPTOR.name == "s3t/wire/v1/check.proto"


def test_fixture_decodes_to_its_values() -> None:
    decoded = WireCheck.FromString(bytes.fromhex(FIXTURE["binary_hex"]))
    assert same(decoded, WireCheck(**FIXTURE_VALUES))
    assert not decoded.HasField("maybe")
    assert decoded.time_us == -(2**53) - 1


def test_fixture_json_is_canonical() -> None:
    message = WireCheck(**FIXTURE_VALUES)
    assert json_format.MessageToDict(message) == FIXTURE["json"]
    parsed = json_format.ParseDict(FIXTURE["json"], WireCheck())
    assert same(parsed, message)
    # int64 travels as a string, NaN and the infinities as names: the canonical mapping.
    assert FIXTURE["json"]["timeUs"] == "-9007199254740993"
    assert FIXTURE["json"]["column"][2:5] == ["NaN", "Infinity", "-Infinity"]


@st.composite
def wire_checks(draw: st.DrawFn) -> WireCheck:
    maybe = draw(st.none() | DOUBLE)
    return WireCheck(
        time_us=draw(INT64),
        value=draw(DOUBLE),
        column=draw(st.lists(DOUBLE, max_size=20)),
        text=draw(TEXT),
        kind=draw(st.sampled_from(KINDS)),
        **({} if maybe is None else {"maybe": maybe}),
    )


@given(wire_checks())
def test_binary_round_trip(message: WireCheck) -> None:
    assert same(WireCheck.FromString(message.SerializeToString()), message)


@given(wire_checks())
def test_json_round_trip(message: WireCheck) -> None:
    assert same(json_format.Parse(json_format.MessageToJson(message), WireCheck()), message)
