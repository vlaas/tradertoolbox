"""Reading and checking the files of FRED. The values are invented. test_parse_* carry the cases of
`volcalc` tests/test_refdata.py at commit f53be83 (a blank and a dot for a day without a value)."""

from datetime import date

import httpx
import pytest

from s3t.refdata.fred import (
    MAX_BYTES,
    START,
    URL,
    FredClient,
    Observations,
    RateDataError,
    check_replaces,
    parse_csv,
)

pytestmark = pytest.mark.anyio

FILE = "observation_date,SOFR\n2024-01-02,5.00\n2024-01-03,.\n2024-01-04,\n2024-01-05,5.10\n"
VALUES = ((date(2024, 1, 2), 5.0), (date(2024, 1, 5), 5.1))
TODAY = date(2024, 1, 8)


def test_parse_leaves_out_the_days_without_a_value() -> None:
    assert parse_csv(FILE, "SOFR") == VALUES
    assert parse_csv(FILE.replace("observation_date", "DATE"), "SOFR") == VALUES  # older files
    assert parse_csv(FILE.replace("\n", "\r\n") + "\r\n", "SOFR") == VALUES
    negative = parse_csv("observation_date,DGS1MO\n2024-01-02,-0.05\n", "DGS1MO")
    assert negative == ((date(2024, 1, 2), -0.05),)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "not a FRED file"),
        ("<!doctype html><html>busy</html>", "not a FRED file"),
        (FILE.replace("SOFR", "DGS1"), "not a FRED file of this series"),
        ("observation_date,SOFR\n", "no value"),
        ("observation_date,SOFR\n2024-01-03,.\n", "no value"),
        (FILE + "2024-01-05,5.2\n", "do not rise"),
        (FILE + "2024-01-04,5.2\n", "do not rise"),
        (FILE + "05/01/2024,5.2\n", "cannot be read"),
        (FILE + "2024-01-08,five\n", "cannot be read"),
        (FILE + "2024-01-08\n", "cannot be read"),
        (FILE + "2024-01-08,nan\n", "not a plausible rate"),
        (FILE + "2024-01-08,inf\n", "not a plausible rate"),
        (FILE + "2024-01-08,530\n", "not a plausible rate"),
    ],
)
def test_parse_refuses(text: str, reason: str) -> None:
    with pytest.raises(RateDataError, match=reason) as info:
        parse_csv(text, "SOFR")
    assert "530" not in str(info.value)  # a reason, not the content


def days(*numbers: int) -> Observations:
    return tuple((date(2024, 1, n), 5.0) for n in numbers)


def test_a_download_may_not_lose_values() -> None:
    check_replaces(days(2, 3), (), TODAY)
    check_replaces(days(2, 3, 4), days(2, 3), TODAY)
    check_replaces(((date(2024, 1, 2), 4.0),), days(2), TODAY)  # a revision
    check_replaces(days(*range(1, 8)), days(*range(1, 8)), TODAY)  # nothing new
    for cut in (days(2, 3), days(3, 4)):
        with pytest.raises(RateDataError, match="fewer"):
            check_replaces(cut, days(2, 3, 4), TODAY)
    with pytest.raises(RateDataError, match="fewer"):
        check_replaces(days(1, 8), days(1, 2, 3, 4, 5, 8), TODAY)
    with pytest.raises(RateDataError, match="days to come"):
        check_replaces(days(2, 9), days(2), TODAY)


async def fetched(answer: httpx.Response | Exception) -> tuple[str, httpx.Request]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    client = FredClient(transport=httpx.MockTransport(handle))
    try:
        return await client.fetch("SOFR"), seen[0]
    finally:
        await client.aclose()


async def test_the_request_names_the_series_and_nothing_else() -> None:
    text, request = await fetched(httpx.Response(200, text=FILE))
    assert text == FILE
    assert request.method == "GET" and str(request.url).startswith(URL)
    assert dict(request.url.params) == {"id": "SOFR", "cosd": START.isoformat()}
    assert request.headers["user-agent"].startswith("s3t/")
    assert not {"authorization", "cookie", "from", "referer"} & set(request.headers)


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(429), "HTTP 429"),
        (httpx.Response(302, headers={"location": "https://elsewhere.invalid/"}), "HTTP 302"),
        (httpx.ConnectTimeout("slow"), "ConnectTimeout"),
        (httpx.ConnectError("none"), "ConnectError"),
        (httpx.Response(200, content=b"x" * (MAX_BYTES + 1)), "larger"),
        (httpx.Response(200, content=b"\xff\xfe\x00"), "not text"),
    ],
)
async def test_what_fetch_refuses(answer: httpx.Response | Exception, reason: str) -> None:
    with pytest.raises(RateDataError, match=reason):
        await fetched(answer)
