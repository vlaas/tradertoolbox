"""Reading stream symbols out of Tastytrade's nested option chain.

A stop-gap for the tools of WP-0.4 (the capture tool and the live stream check). WP-0.5 types
the chain as the instrument model, and this module goes then. It is here, inside the broker
module, because nothing outside it may read the broker's keys (GEN-09).
"""

from s3t.broker.interface import JsonObject


def near_the_money(
    chain: JsonObject, spot: float, *, expirations: int, strikes: int, min_days: int = 0
) -> list[str]:
    """The stream symbols of calls and puts around ``spot``: the ``strikes`` nearest strikes
    of each of the first ``expirations`` expirations of the first root that are at least
    ``min_days`` away."""
    items = chain.get("items") or []
    if not items:
        return []
    found: list[str] = []
    listed = [
        e for e in items[0].get("expirations", []) if e.get("days-to-expiration", 0) >= min_days
    ]
    for expiration in listed[:expirations]:
        rows = sorted(
            expiration.get("strikes", []),
            key=lambda row: abs(float(row["strike-price"]) - spot),
        )
        for row in rows[:strikes]:
            found += [row["call-streamer-symbol"], row["put-streamer-symbol"]]
    return found
