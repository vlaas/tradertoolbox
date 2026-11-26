"""The backend of the browser suite (`make e2e`): the real process on the fake broker, with a
control for the tests.

    uv run python tools/e2e_backend.py --port 4178 --control-port 4179 --frontend ../frontend/dist

Everything it writes is in a temporary directory that goes with it: the configuration, the state
file and the copies. The password is the one the suite knows; it protects nothing but that. The
synthetic market starts in a regular session, so it moves at any hour, and no call leaves the
machine: neither the broker nor FRED is asked (``s3t.system.backend``).

The suite runs one of these per browser engine (``frontend/playwright.config.ts``), so that a
restart in one engine breaks no test of another.

**The control** is a small HTTP server of this tool on the loopback address and on its own port. It
is not part of the application: the application has no such route, and SEC-02's enumeration of the
routes does not see it. It answers with no body:

- ``POST /restart?down=S``: the backend stops as it does on SIGTERM (a browser gets the close
  1012), stays away S seconds, and starts again on the same port and the same state file, so a
  login survives. 202 at once.
- ``POST /drop-stream``: the fake broker closes its stream sessions, and the backend's session
  reconnects (BRK-06). 204.
- ``POST /quote?symbol=SPY&bid=111.11&ask=111.13``: the fake broker's next tick sends that bid and
  ask (``FakeDxLink.fixed_quotes``). ``DELETE /quote?symbol=SPY``: the market's quote again. 204.

While the backend is away, everything but a restart is 503.
"""

import argparse
import asyncio
import contextlib
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from s3t.__main__ import install_logging
from s3t.api.app import create_app
from s3t.api.server import SIGNALS, listen, serve
from s3t.broker.fake.broker import FakeBroker
from s3t.config import Config, load_config
from s3t.store.state import StateStore
from s3t.system.backend import Backend, open_backend
from s3t.system.login import set_password

PASSWORD = "e2e-password-of-the-browser-suite"
CONFIG = """
[server]
listen_address = "127.0.0.1"
port = {port}
[paths]
env_file = "none.env"
data_dir = "data"
state_file = "data/state.sqlite"
frontend_dir = "{frontend}"
[broker]
source = "fake"
fake_start = 2026-11-20T15:00:00Z
account_nickname = "main"
[store]
daily_copy = false
"""
LOOPBACK = "127.0.0.1"


class Refused(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class Control:
    """What the tests may do to the running backend."""

    def __init__(self) -> None:
        self.backend: Backend | None = None
        self.stop = asyncio.Event()
        self.down_s: float | None = None
        """Set by a restart: how long the backend stays away."""

    def _fake(self) -> FakeBroker:
        if self.backend is None:
            raise Refused("503 Service Unavailable")
        broker = self.backend.broker
        assert isinstance(broker, FakeBroker)
        return broker

    def act(self, method: str, path: str, query: dict[str, str]) -> str:
        if (method, path) == ("POST", "/restart"):
            if self.down_s is None:
                self.down_s = float(query.get("down", "0"))
                self.stop.set()
            return "202 Accepted"
        if (method, path) == ("POST", "/drop-stream"):
            self._fake().dxlink.drop()
        elif (method, path) == ("POST", "/quote"):
            bid, ask = float(query["bid"]), float(query["ask"])
            self._fake().dxlink.fixed_quotes[query["symbol"]] = (bid, ask)
        elif (method, path) == ("DELETE", "/quote"):
            self._fake().dxlink.fixed_quotes.pop(query["symbol"], None)
        else:
            raise Refused("404 Not Found")
        return "204 No Content"

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, target, _ = (await reader.readline()).decode("ascii").split(" ", 2)
            while (await reader.readline()).strip():
                pass  # the headers say nothing here
            url = urlsplit(target)
            status = self.act(method, url.path, dict(parse_qsl(url.query)))
        except Refused as e:
            status = e.status
        except (ValueError, KeyError, UnicodeDecodeError):
            status = "400 Bad Request"
        head = f"HTTP/1.1 {status}\r\ncontent-length: 0\r\nconnection: close\r\n\r\n"
        writer.write(head.encode())
        with contextlib.suppress(ConnectionError):
            await writer.drain()
        writer.close()


async def away(seconds: float) -> bool:
    """Waits while the backend is away. False when a signal ended the wait: then it stays away."""
    loop = asyncio.get_running_loop()
    ended = asyncio.Event()
    for number in SIGNALS:
        loop.add_signal_handler(number, ended.set)
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ended.wait(), seconds)
    finally:
        for number in SIGNALS:
            loop.remove_signal_handler(number)
    return not ended.is_set()


async def run(config: Config, control_port: int) -> None:
    control = Control()
    async with await asyncio.start_server(control.handle, LOOPBACK, control_port):
        while True:
            sock = listen(config.server)
            with contextlib.closing(sock):
                async with open_backend(config) as backend:
                    control.backend = backend
                    await serve(create_app(backend), sock, stop=control.stop)
                    control.backend = None
            if control.down_s is None or not await away(control.down_s):
                return
            control.down_s, control.stop = None, asyncio.Event()


async def prepare(config: Path) -> None:
    store = await StateStore.open(load_config(config).paths.state_file)
    await set_password(store, PASSWORD)
    await store.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="The backend of the browser suite.")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--control-port", type=int, required=True)
    parser.add_argument("--frontend", type=Path, required=True, help="the built frontend")
    args = parser.parse_args()
    install_logging()
    with tempfile.TemporaryDirectory(prefix="s3t-e2e-") as directory:
        path = Path(directory) / "s3t.toml"
        path.write_text(CONFIG.format(port=args.port, frontend=args.frontend.resolve()))
        asyncio.run(prepare(path))
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run(load_config(path), args.control_port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
