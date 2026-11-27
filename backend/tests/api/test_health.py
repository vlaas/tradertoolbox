"""The health route (SEC-08): broker connection, stream, use, stores and reference data."""

from dataclasses import fields, replace
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts

from s3t.api.app import create_api
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

from .conftest import ApiRig
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
