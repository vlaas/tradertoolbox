"""The data tier that every feature needs, and whether it is there (GEN-04; product definition,
section 5). Proposed 2026-11-25 (WP-0.7).

A feature is declared once, here. A route or a topic that serves one names it; those that belong
to the process itself (login, health, status) need no data and are listed in a test. When a tier
is absent, the feature's routes and topics refuse with the reason, and the app hides the feature or
marks it as unavailable, as the declaration says. Nothing fails silently.

A package that brings a feature adds it to :data:`FEATURES`. Features that are not built are not
declared.
"""

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from s3t.broker.interface import Broker
from s3t.secrets import redact


class Tier(StrEnum):
    T1 = "T1"
    """Live broker data."""
    T2 = "T2"
    """History that the backend records itself."""
    T3 = "T3"
    """An external historical store, read-only and optional."""


type WhenAbsent = Literal["hidden", "unavailable"]


@dataclass(frozen=True)
class Feature:
    name: str
    title: str
    """For people."""
    needs: tuple[Tier, ...]
    when_absent: WhenAbsent
    """What the app does with the feature when one of these tiers is absent."""


QUOTES = Feature("quotes", "Live quotes", (Tier.T1,), "unavailable")

FEATURES: tuple[Feature, ...] = (QUOTES,)


class TierReport(BaseModel):
    tier: Tier
    present: bool
    reason: str | None
    """Why it is absent."""


class FeatureReport(BaseModel):
    name: str
    title: str
    needs: list[Tier]
    available: bool
    reason: str | None
    """Why it is not available."""
    show: Literal["available", "unavailable", "hidden"]
    """What the app does with it."""


class FeaturesReport(BaseModel):
    tiers: list[TierReport]
    features: list[FeatureReport]


class Features:
    def __init__(
        self,
        broker: Broker,
        t3_root: Path | None,
        declared: Sequence[Feature] = FEATURES,
    ) -> None:
        self._broker, self._t3_root = broker, t3_root
        self.declared = tuple(declared)

    async def absent(self, tiers: Iterable[Tier] = tuple(Tier)) -> dict[Tier, str]:
        """Which of these tiers are not there now, and why."""
        reasons = {tier: await self._why_absent(tier) for tier in tiers}
        return {tier: reason for tier, reason in reasons.items() if reason is not None}

    async def _why_absent(self, tier: Tier) -> str | None:
        match tier:
            case Tier.T1:
                rest = self._broker.rest_status()
                if rest.auth != "refused":
                    return None
                why = redact(rest.refusal or "no reason given")
                return f"the broker refuses its data: {why}"
            case Tier.T2:
                # R3 brings the recorder of the daily snapshots (STO-03).
                return "the backend does not record any history yet"
            case Tier.T3:
                if self._t3_root is None:
                    return "no historical store is configured"
                if await asyncio.to_thread(self._t3_root.is_dir):  # it may be a network disk
                    return None
                return "the configured historical store is not there"

    async def refusal(self, feature: Feature) -> str | None:
        """Why this feature cannot be served now; None when it can."""
        return _reason(feature, await self.absent(feature.needs))

    async def report(self) -> FeaturesReport:
        absent = await self.absent()
        return FeaturesReport(
            tiers=[
                TierReport(tier=tier, present=tier not in absent, reason=absent.get(tier))
                for tier in Tier
            ],
            features=[_report(feature, _reason(feature, absent)) for feature in self.declared],
        )


def _reason(feature: Feature, absent: Mapping[Tier, str]) -> str | None:
    missing = [absent[tier] for tier in feature.needs if tier in absent]
    return "; ".join(missing) or None


def _report(feature: Feature, reason: str | None) -> FeatureReport:
    return FeatureReport(
        name=feature.name,
        title=feature.title,
        needs=list(feature.needs),
        available=reason is None,
        reason=reason,
        show="available" if reason is None else feature.when_absent,
    )
