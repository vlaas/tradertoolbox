"""The published rates as FRED serves them: one CSV file per series, without a key (REF-01).

Source: `volcalc` src/vrp/refdata/rates.py at commit f53be83: the address (`FRED_CSV`), the start of
the history (`START`) and the reading of a file (`_fred`).

Changed in the port (2026-11-25, WP-0.6):
- httpx instead of urllib, plain values instead of polars, and one request at a time.
- A file is checked before it is used: its header, its dates, its values. Its size is limited.
- The merge with a local parquet file is dropped (architecture note, extraction table).

Values are percent, as quoted, until ``s3t.pricing.rates`` converts them.
"""

import csv
import io
import math
from datetime import date

import httpx

from s3t import __version__

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
START = date(2018, 1, 1)
TIMEOUT_S = 30.0
MAX_BYTES = 2_000_000
"""A series since 2018 has some 40 kB."""
PLAUSIBLE = (-5.0, 30.0)
"""Percent. A value outside is taken for a fault of the file."""

type Observations = tuple[tuple[date, float], ...]
"""(date, percent), dates rising, days without a value left out."""


class RateDataError(Exception):
    """A series could not be downloaded, or what came is not usable."""


def parse_csv(text: str, series: str) -> Observations:
    """Read a FRED file. Days without a value, blank or ``.``, are left out."""
    rows = csv.reader(io.StringIO(text))
    header = next(rows, [])
    if len(header) != 2 or header[0] not in ("observation_date", "DATE") or header[1] != series:
        raise RateDataError("not a FRED file of this series")
    found: list[tuple[date, float]] = []
    last = date.min
    for row in rows:
        if not row:
            continue
        try:
            day, quoted = date.fromisoformat(row[0]), row[1].strip()
            value = None if quoted in ("", ".") else float(quoted)
        except (ValueError, IndexError):
            raise RateDataError(f"line {rows.line_num} cannot be read") from None
        if day <= last:
            raise RateDataError(f"line {rows.line_num}: the dates do not rise")
        last = day
        if value is None:
            continue
        if not math.isfinite(value) or not PLAUSIBLE[0] <= value <= PLAUSIBLE[1]:
            raise RateDataError(f"line {rows.line_num}: the value is not a plausible rate")
        found.append((day, value))
    if not found:
        raise RateDataError("the file has no value")
    return tuple(found)


def check_replaces(new: Observations, old: Observations, today: date) -> None:
    """Refuse a download that would lose values: one that was cut, at either end, or that states
    days to come. A revised value is accepted."""
    if new[-1][0] > today:
        raise RateDataError("the file has values of days to come")
    if old and (new[-1][0] < old[-1][0] or new[0][0] > old[0][0] or len(new) < 0.9 * len(old)):
        raise RateDataError("the file has fewer values than the one before")


class FredClient:
    """Downloads single series. It sends nothing but the name of the series and the start."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            headers={"User-Agent": f"s3t/{__version__}", "Accept": "text/csv"},
            timeout=TIMEOUT_S,
            transport=transport,
            follow_redirects=False,
        )

    async def fetch(self, series: str) -> str:
        """The file of a series, unread. Raises :class:`RateDataError`."""
        params = {"id": series, "cosd": START.isoformat()}
        body = bytearray()
        try:
            async with self._http.stream("GET", URL, params=params) as response:
                if response.status_code != 200:
                    raise RateDataError(f"HTTP {response.status_code}")
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise RateDataError("the file is larger than any series")
            return body.decode("utf-8")
        except httpx.HTTPError as e:
            raise RateDataError(type(e).__name__) from e
        except UnicodeDecodeError:
            raise RateDataError("the file is not text") from None

    async def aclose(self) -> None:
        await self._http.aclose()
