"""Instants as numbers: microseconds since 1970 in UTC (GEN-10).

One form for the recordings, the stores and the wire. An instant without a time zone is refused,
because what it means would depend on the machine. The arithmetic is exact: a float is made only
at the edge of the wire (``s3t.api.rows``).
"""

from datetime import UTC, datetime, timedelta

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


def to_micros(instant: datetime) -> int:
    if instant.tzinfo is None:
        raise ValueError("an instant is aware (GEN-10)")
    return (instant - _EPOCH) // _MICROSECOND


def from_micros(micros: int) -> datetime:
    """The instant, in UTC."""
    return _EPOCH + timedelta(microseconds=micros)
