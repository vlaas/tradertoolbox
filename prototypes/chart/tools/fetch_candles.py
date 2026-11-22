"""Download SPY candles for the chart prototype. Read-only; prints counts only.

Usage: python fetch_candles.py <env_file> <out_dir> [set,set,...]
The env file holds TASTY_REFRESH_TOKEN and TASTY_CLIENT_SECRET. The output is market data and
must not be committed (public/data/ is git-ignored).
Rows are [time_ms_utc, open, high, low, close, volume].
"""
import asyncio
import json
import math
import sys
import time
from pathlib import Path

import httpx
import websockets
from dotenv import dotenv_values

ENV, OUT = sys.argv[1], Path(sys.argv[2])
env = dotenv_values(ENV)
IS_TEST = str(env.get("TASTY_IS_TEST", "")).lower() in ("1", "true", "yes")
BASE = "https://api.cert.tastyworks.com" if IS_TEST else "https://api.tastyworks.com"
SETS = {
    "spy_d": "SPY{=d}", "spy_15m": "SPY{=15m}", "spy_15m_rth": "SPY{=15m,tho=true}",
    "spy_1m": "SPY{=m}", "spy_5m": "SPY{=5m}", "spy_30m": "SPY{=30m}", "spy_1h": "SPY{=h}", "spy_4h": "SPY{=4h}",
    "spy_1h_s": "SPY{=h,a=s}", "spy_4h_s": "SPY{=4h,a=s}", "spy_w": "SPY{=w}", "spy_mo": "SPY{=mo}",
}
ONLY = sys.argv[3].split(",") if len(sys.argv) > 3 else list(SETS)


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def quote_token():
    c = httpx.Client(base_url=BASE, headers={"User-Agent": "chart-prototype/0.1"}, timeout=30)
    r = c.post("/oauth/token", json={"grant_type": "refresh_token",
                                     "refresh_token": env["TASTY_REFRESH_TOKEN"],
                                     "client_secret": env["TASTY_CLIENT_SECRET"]})
    r.raise_for_status()
    time.sleep(1.0)
    r = c.get("/api-quote-tokens", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    r.raise_for_status()
    d = r.json()["data"]
    return d["token"], d["dxlink-url"]


async def run(token, url):
    ws = await websockets.connect(url, max_size=None)

    async def send(m):
        await ws.send(json.dumps(m))

    await send({"type": "SETUP", "channel": 0, "version": "0.1-DXF-JS/0.3.0",
                "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60})
    await send({"type": "AUTH", "channel": 0, "token": token})
    events = {}

    async def pump(seconds, idle=None):
        end, last = time.time() + seconds, time.time()
        while time.time() < end:
            if idle and time.time() - last > idle:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            m = json.loads(raw)
            if m.get("type") == "FEED_DATA":
                last = time.time()
                events.setdefault(m["channel"], []).extend(m["data"])
            elif m.get("type") == "KEEPALIVE":
                await send({"type": "KEEPALIVE", "channel": 0})

    await pump(3)
    ch = 3
    for name, sym in ((k, SETS[k]) for k in ONLY):
        await send({"type": "CHANNEL_REQUEST", "channel": ch, "service": "FEED", "parameters": {"contract": "AUTO"}})
        await send({"type": "FEED_SETUP", "channel": ch, "acceptAggregationPeriod": 0.5, "acceptDataFormat": "FULL"})
        await pump(1.5)
        await send({"type": "FEED_SUBSCRIPTION", "channel": ch, "reset": True,
                    "add": [{"type": "Candle", "symbol": sym, "fromTime": 0}]})
        await pump(30, idle=3)
        rows = {}
        for e in events.get(ch, []):
            t = e.get("time")
            o, h, lo, c = (num(e.get(k)) for k in ("open", "high", "low", "close"))
            if e.get("eventType") == "Candle" and isinstance(t, (int, float)) and t > 0 and None not in (o, h, lo, c):
                rows[int(t)] = [int(t), o, h, lo, c, num(e.get("volume")) or 0.0]
        data = [rows[t] for t in sorted(rows)]
        (OUT / f"{name}.json").write_text(json.dumps(data))
        print(name, len(data), "candles")
        await send({"type": "FEED_SUBSCRIPTION", "channel": ch, "reset": True})
        await send({"type": "CHANNEL_CANCEL", "channel": ch})
        await pump(2.0)
        ch += 2
    await ws.close()


OUT.mkdir(parents=True, exist_ok=True)
asyncio.run(run(*quote_token()))
