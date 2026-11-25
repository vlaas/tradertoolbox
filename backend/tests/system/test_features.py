"""The data tier of every feature (GEN-04)."""

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute, iter_route_contexts

from s3t.api.app import create_api
from s3t.api.routes import FeatureGate, needs
from s3t.api.topics import make_topics
from s3t.broker.interface import Broker, RestStatus
from s3t.broker.replay import ReplayBroker
from s3t.system.features import FEATURES, QUOTES, Feature, Features, Tier
from s3t.wire.v1.channel_pb2 import Subscribe

pytestmark = pytest.mark.anyio

VOLATILITY = Feature("volatility", "IV history", (Tier.T1, Tier.T3), "hidden")
RECORDED = Feature("recorded", "Own history", (Tier.T2,), "unavailable")
HISTORY = Feature("history", "Simulation on history", (Tier.T3,), "unavailable")


class FakeStatus:
    """A broker that only states its REST status."""

    def __init__(self, **change: Any) -> None:
        status = ReplayBroker.rest_status(cast(Any, None))
        self.status: RestStatus = replace(status, **change)

    def rest_status(self) -> RestStatus:
        return self.status


def features(t3: Path | None = None, *declared: Feature, **status: Any) -> Features:
    broker = cast(Broker, FakeStatus(**status))
    return Features(broker, t3, declared or FEATURES)


async def test_what_is_there_without_any_store() -> None:
    report = await features(None, QUOTES, VOLATILITY, RECORDED).report()
    tiers = {t.tier: (t.present, t.reason) for t in report.tiers}
    assert tiers[Tier.T1] == (True, None)
    assert not tiers[Tier.T2][0] and "does not record" in str(tiers[Tier.T2][1])
    assert tiers[Tier.T3] == (False, "no historical store is configured")
    shown = {f.name: (f.available, f.show) for f in report.features}
    assert shown == {
        "quotes": (True, "available"),
        "volatility": (False, "hidden"),
        "recorded": (False, "unavailable"),
    }
    assert all(f.available or f.reason for f in report.features)  # never without a reason


async def test_the_historical_store(tmp_path: Path) -> None:
    """It may be absent on a machine, and come and go."""
    root = tmp_path / "t3"
    service = features(root, HISTORY)
    gone = await service.refusal(HISTORY)
    assert gone == "the configured historical store is not there"
    assert str(root) not in gone  # the path belongs to the machine
    root.mkdir()
    assert await service.refusal(HISTORY) is None
    root.rmdir()
    root.write_text("a file")
    assert await service.refusal(HISTORY) == gone


async def test_a_refused_grant_takes_the_broker_data_away() -> None:
    service = features(None, QUOTES, auth="refused", refusal="the grant was revoked")
    assert await service.refusal(QUOTES) == "the broker refuses its data: the grant was revoked"
    for state in ("none", "valid", "due", "lapsed", "renewing"):
        assert await features(None, QUOTES, auth=state).refusal(QUOTES) is None


async def test_a_feature_that_needs_two_tiers_names_both() -> None:
    service = features(None, VOLATILITY, auth="refused", refusal="no")
    reason = await service.refusal(VOLATILITY)
    assert reason == "the broker refuses its data: no; no historical store is configured"


async def test_only_the_tiers_that_are_needed_are_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store on a network disk that hangs must not hold up the quotes."""

    def hangs(_: Path) -> bool:
        raise AssertionError("looked at the store")

    monkeypatch.setattr(Path, "is_dir", hangs)
    assert await features(tmp_path, QUOTES).refusal(QUOTES) is None


def test_the_declaration() -> None:
    names = [f.name for f in FEATURES]
    assert len(set(names)) == len(names) and all(f.needs and f.title for f in FEATURES)


# Routes and topics of the process itself: they need no data.
SYSTEM_ROUTES = {"/api/login", "/api/logout", "/api/session", "/api/health", "/api/features"}
SYSTEM_ROUTES |= {"/api/ws"}  # its topics state their own
SYSTEM_TOPICS = {"status"}


def test_every_route_and_topic_names_its_feature() -> None:
    """One that is added without a feature is named here, or the suite fails."""
    without: set[str] = set()
    for context in iter_route_contexts(create_api().routes):
        route = context.original_route
        gates: list[Feature] = []
        if isinstance(route, APIRoute):
            calls = [d.dependency for d in route.dependencies]
            gates = [call.feature for call in calls if isinstance(call, FeatureGate)]
        assert isinstance(route, APIRoute | APIWebSocketRoute)
        assert all(feature in FEATURES for feature in gates)
        if not gates:
            without.add(route.path)
    assert without == SYSTEM_ROUTES

    topics = make_topics(cast(Any, None))
    assert set(topics) == {f.name for f in Subscribe.DESCRIPTOR.oneofs_by_name["topic"].fields}
    assert {name for name, topic in topics.items() if topic.feature is None} == SYSTEM_TOPICS
    assert all(t.feature in FEATURES for t in topics.values() if t.feature is not None)


async def test_a_route_whose_data_is_absent_says_why() -> None:
    import httpx
    from fastapi import FastAPI

    from s3t.api import routes

    class Holder:
        pass

    api = create_api()
    backend = object.__new__(routes.Backend)
    object.__setattr__(backend, "features", features(None, HISTORY))
    api.state.backend = backend
    assert isinstance(api, FastAPI)

    @api.get("/api/history", dependencies=[needs(HISTORY)])
    async def history() -> None: ...

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(api), base_url="http://127.0.0.1"
    ) as http:
        answer = await http.get("/api/history")
    assert answer.status_code == 503
    assert answer.json() == {
        "code": "unavailable",
        "message": "Simulation on history: no historical store is configured",
    }
