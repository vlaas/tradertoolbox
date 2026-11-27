"""The memory of the process against its bound (PRF-09)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from s3t.system.process import MB, ProcessWatch, peak_rss_bytes

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 30, 14, 0, tzinfo=UTC)


class Rig:
    """A watch on a memory and a clock of the test's own."""

    def __init__(self, bound_mb: int = 100) -> None:
        self.memory = 50 * MB
        self.at = T0
        self.ticks = asyncio.Event()
        self.watch = ProcessWatch(
            bound_mb, read=lambda: self.memory, now=lambda: self.at, sleep=self.sleep
        )

    async def sleep(self, seconds: float) -> None:
        await self.ticks.wait()
        self.ticks.clear()
        self.at += timedelta(seconds=seconds)

    async def minute(self) -> None:
        """One check of the running watch."""
        self.ticks.set()
        for _ in range(5):
            await asyncio.sleep(0)


def test_the_peak_of_this_process_is_plausible() -> None:
    """The unit is bytes on every platform: a Python process with the backend's imports holds
    more than 10 MB and less than 10 GB."""
    assert 10 * MB < peak_rss_bytes() < 10_000 * MB


async def test_below_the_bound() -> None:
    rig = Rig()
    rig.watch.start()
    status = rig.watch.status()
    assert (status.peak_rss_mb, status.bound_mb, status.over_bound_since) == (50.0, 100, None)
    assert status.checked_at == T0
    await rig.watch.aclose()


async def test_above_the_bound_warns_once_and_says_since_when(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = Rig()
    with caplog.at_level(logging.WARNING, logger="s3t.system.process"):
        rig.watch.start()
        rig.memory = 101 * MB
        await rig.minute()
        crossed = rig.watch.status()
        rig.memory = 150 * MB
        await rig.minute()
        later = rig.watch.status()
    assert crossed.over_bound_since == T0 + timedelta(seconds=60)
    assert later.over_bound_since == crossed.over_bound_since
    assert (later.peak_rss_mb, later.checked_at) == (150.0, T0 + timedelta(seconds=120))
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1 and "101 MB" in warnings[0] and "100 MB" in warnings[0]
    await rig.watch.aclose()


async def test_the_status_is_the_last_check_not_a_new_reading() -> None:
    """Two reads of the health route in a row give the same answer (API-01's test compares
    them)."""
    rig = Rig()
    rig.watch.start()
    rig.memory = 70 * MB
    assert rig.watch.status().peak_rss_mb == 50.0
    await rig.minute()
    assert rig.watch.status().peak_rss_mb == 70.0
    await rig.watch.aclose()
