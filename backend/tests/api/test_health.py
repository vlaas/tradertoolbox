"""The health route (SEC-08): broker connection, stream, use, stores and reference data."""

import asyncio
import os
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts

from s3t.analysis.surface import WatchedStatus
from s3t.api.app import create_api
from s3t.broker.fake.clock import VirtualClock
from s3t.pricing.surface import OK
from s3t.subs.feed import ClassUse, ConsumerUse, FeedStatus, Priority
from s3t.system import health as service
from s3t.system.health import (
    CLASS_NAMES,
    ClassReport,
    ConsumerReport,
    Health,
    SubscriptionsReport,
    subscriptions_report,
)

from .conftest import ASOF, ApiRig, make_config, running
from .test_status import FEED

pytestmark = pytest.mark.anyio


async def health(rig: ApiRig) -> dict[str, Any]:
    answer = await rig.http.get("/api/health")
    assert answer.status_code == 200
    return answer.json()


async def test_what_it_reports(rig: ApiRig) -> None:
    await rig.log_in()
    socket = await rig.socket()
    socket.subscribe(quotes={"symbol": ["SPY"]})
    await rig.pass_time(2)
    doc = await health(rig)
    assert set(doc) == set(Health.model_fields)
    assert doc["source"] == {"kind": "fake", "mark": "SYNTHETIC DATA", "recorded_on": None}
    assert doc["account"] == "main"  # the nickname (GEN-03)
    # The broker connection and the stream (BRK-06, BRK-09).
    assert doc["rest"]["auth"] == "valid" and doc["rest"]["rate"] == 2.0
    assert doc["stream"]["phase"] == "connected" and doc["stream"]["subscribed"] == 3
    # The use (SUB-07).
    use = doc["subscriptions"]
    assert (use["granted"], use["budget"], use["sent"]) == (3, 10_000, 3)
    assert {c["demand_class"]: c["granted"] for c in use["classes"]}["views"] == 3
    assert use["consumers"] == [
        {"name": "quotes:1:1", "demand_class": "views", "wanted": 3, "granted": 3}
    ]
    # What should be zero.
    assert (use["failures"], use["cap_refusals"], use["lost"]) == (0, 0, 0)
    faults = {k: doc["stream"][k] for k in ("parse_errors", "sink_errors", "ignored_events")}
    assert faults == {"parse_errors": 0, "sink_errors": 0, "ignored_events": 0}
    market = doc["market"]
    assert market["values"]["quote"] == 1 and market["period"] == 0.5 and market["lost"] == 0
    # The stores.
    assert doc["store"]["version"] == 2 and doc["store"]["size"] > 0
    assert doc["copies"] == {"on": False, "last_at": None, "failures": 0, "last_error": None}
    # The reference data in use (REF-01, REF-04).
    assert doc["calendar"]["source"].startswith("exchange-calendars")
    assert doc["calendar"]["last"] == "2033-12-31"
    assert doc["rates"]["source"] == "flat" and doc["rates"]["flat_rate"] == 0.04
    assert doc["rates"]["reason"] and doc["rates"]["downloads"] is False
    # The memory against its bound (PRF-09).
    process = doc["process"]
    assert process["bound_mb"] == 1024 and process["over_bound_since"] is None
    assert 10 < process["peak_rss_mb"] < 10_000 and process["checked_at"] is not None
    # The pool and the surfaces (PRF-05): a view of quotes starts neither.
    assert doc["pool"]["workers"] == 2 and doc["pool"]["started_at"] is None
    assert doc["surface"] == {"refresh_s": 30.0, "watched": []}


SURFACES = """source = "fake"
[stream]
stall_after = 3600.0
[surface]
max_dte = 30
wing_strikes = 8
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_a_watched_surface_is_fitted_in_worker_processes_and_reported(
    tmp_path: Path,
) -> None:
    """The one test through the real pool (WP-1.1; PRF-05): the composed backend, a watch, the
    fits in worker processes, and both in the health record. The clock of the data is virtual
    and the workers take real time, so the test lets both pass."""
    async with running(make_config(tmp_path, SURFACES), VirtualClock(ASOF)) as rig:
        await rig.up()
        await rig.log_in()
        backend = rig.backend
        assert backend.pool.status().started_at is None
        watch = backend.surface.watch("AAPL")

        def filled() -> bool:
            watched = backend.surface.status().watched[0]
            return watch.latest is not None and not watched.filling

        async with asyncio.timeout(180):  # generous: the machine may be loaded
            while not filled():
                await rig.pass_time(1)
                await asyncio.sleep(0.02)
            # No look follows while the clock stands still: wait for the fit under way.
            while backend.pool.status().under_way:  # noqa: ASYNC110
                await asyncio.sleep(0.02)
        await rig.clock.settle()
        fit = watch.latest
        assert fit is not None and fit.fit_s > 0 and fit.cpu_s > 0 and fit.took_s >= fit.fit_s
        assert [s.status for s in fit.surface.slices[1:5]] == [OK] * 4
        assert all(s.forward > 0 for s in fit.surface.slices)

        doc = await health(rig)
        pool = doc["pool"]
        assert pool["started_at"] is not None and 1 <= pool["processes"] <= 2
        assert (pool["failed"], pool["broken"], pool["under_way"]) == (0, 0, 0)
        pids = backend.pool.pids()
        assert pids and os.getpid() not in pids
        (watched,) = doc["surface"]["watched"]
        assert set(watched) == {f.name for f in fields(WatchedStatus)}
        assert watched["underlying"] == "AAPL" and watched["watchers"] == 1
        assert watched["problem"] is None and watched["filling"] is False
        assert watched["fits"] == pool["done"] == fit.number and watched["failures"] == 0
        assert watched["expirations"] == len(fit.surface.slices) == sum(watched["slices"].values())
        assert watched["values"] == fit.values and watched["not_live"] == 0
        assert watched["pairs"] == fit.values + 2 and watched["last_fit_s"] > 0
        assert 0 < watched["loop_ms_max"] < 1000
        use = {c["name"]: c for c in doc["subscriptions"]["consumers"]}["surface"]
        assert use == {
            "name": "surface",
            "demand_class": "views",
            "wanted": watched["pairs"],
            "granted": watched["pairs"],
        }
        watch.release()
        assert (await health(rig))["surface"]["watched"] == []
    # The backend is closed: its workers are gone.
    assert not any(alive(pid) for pid in pids)


async def test_the_route_is_the_service(rig: ApiRig) -> None:
    """API-01: the answer of a route is what the service returns, and nothing else."""
    await rig.log_in()
    direct = (await service.health(rig.backend)).model_dump(mode="json")
    through = await health(rig)
    assert through == direct
    features = (await rig.backend.features.report()).model_dump(mode="json")
    assert (await rig.http.get("/api/features")).json() == features


async def test_faults_are_shown(rig: ApiRig) -> None:
    await rig.log_in()
    feed = rig.backend.feed
    feed.failures, feed.cap_refusals = 2, 3
    use = (await health(rig))["subscriptions"]
    assert (use["failures"], use["cap_refusals"]) == (2, 3)


def test_every_field_of_the_use_is_reported() -> None:
    """A field that is added to a record is added here too."""
    feed = {f.name for f in fields(FeedStatus)}
    assert feed - {"stream"} == set(SubscriptionsReport.model_fields) - {"failures", "cap_refusals"}
    for record, report in ((ClassUse, ClassReport), (ConsumerUse, ConsumerReport)):
        own = {"demand_class" if f.name == "priority" else f.name for f in fields(record)}
        assert own == set(report.model_fields)
    report = subscriptions_report(FEED, failures=1, cap_refusals=2)
    for name in feed - {"stream", "classes", "consumers"}:
        assert getattr(report, name) == getattr(FEED, name), name
    assert report.classes[0] == ClassReport(demand_class="views", wanted=11, granted=12, counted=13)
    assert report.consumers[0].name == "a view" and report.consumers[0].wanted == 14
    assert set(CLASS_NAMES) == set(Priority) and len(set(CLASS_NAMES.values())) == len(Priority)


async def test_every_text_is_redacted(rig: ApiRig, monkeypatch: pytest.MonkeyPatch) -> None:
    from s3t.secrets import REDACTOR

    secret = "canary-health-text-31c0"
    REDACTOR.register(secret)
    status = rig.backend.broker.rest_status()
    monkeypatch.setattr(
        rig.backend.broker, "rest_status", lambda: replace(status, refusal=f"with {secret}")
    )
    await rig.log_in()
    answer = await rig.http.get("/api/health")
    assert secret not in answer.text and "with ***" in answer.text


def test_every_route_answers_with_a_typed_record() -> None:
    """API-02: the TypeScript types come from these. An answer without a model has none."""
    schema = create_api().openapi()
    for context in iter_route_contexts(create_api().routes):
        route = context.original_route
        if not isinstance(route, APIRoute):
            continue
        for method in (m.lower() for m in route.methods or ()):
            answers = schema["paths"][context.path][method]["responses"]
            assert "default" not in answers
            for code, answer in answers.items():
                body = answer.get("content", {}).get("application/json", {}).get("schema")
                assert code == "204" or (body and "$ref" in body), (context.path, code)
                if code != "204" and int(code) >= 400:
                    assert body["$ref"].endswith("/ApiError"), (context.path, code)
