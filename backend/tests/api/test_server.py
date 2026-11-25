"""The web server over real sockets on the loopback address: the one address (SEC-03), the
WebSocket in front of a real client, and the process from its start to its end (SEC-06)."""

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from starlette.types import ASGIApp
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from s3t.api.server import listen, serve
from s3t.config import ServerConfig
from system.conftest import PASSWORD

from .conftest import CONFIG

pytestmark = pytest.mark.anyio

BACKEND = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def serving(app: ASGIApp, sock: socket.socket) -> AsyncIterator[None]:
    """The application on a socket that listens already."""
    task = asyncio.create_task(serve(app, sock))
    await asyncio.sleep(0.2)
    try:
        yield
    finally:
        os.kill(os.getpid(), signal.SIGTERM)  # as it is stopped in use
        await asyncio.wait_for(task, 15)


# ---------------------------------------------------------------------- one address


def test_the_socket_listens_on_the_configured_address_only() -> None:
    """SEC-03."""
    config = ServerConfig(listen_address="127.0.0.1", port=free_port())
    with contextlib.closing(listen(config)) as sock:
        assert sock.getsockname() == ("127.0.0.1", config.port)
        with socket.create_connection(("127.0.0.1", config.port), timeout=1):
            pass  # it listens already
        others = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)}
        for address in {str(a) for a in others} - {"127.0.0.1", "::1"}:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            with socket.socket(family) as client, pytest.raises(OSError):
                client.settimeout(1)
                client.connect((address, config.port))


def test_the_ipv6_loopback() -> None:
    config = ServerConfig(listen_address="::1", port=free_port())
    try:
        sock = listen(config)
    except OSError as e:
        pytest.skip(f"no IPv6 here: {e.strerror}")
    with contextlib.closing(sock):
        assert sock.getsockname()[:2] == ("::1", config.port)
        assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)


def test_a_second_one_fails_before_it_serves() -> None:
    config = ServerConfig(listen_address="127.0.0.1", port=free_port())
    with contextlib.closing(listen(config)), pytest.raises(OSError):
        listen(config)


# ---------------------------------------------------------------------- the process


@pytest.fixture
def config(tmp_path: Path) -> Iterator[Path]:
    file = tmp_path / "s3t.toml"
    text = CONFIG.replace("port = 8740", f"port = {free_port()}")
    text += 'source = "fake"\nfake_start = 2026-11-20T15:00:00Z\n'
    file.write_text(text.replace("[store]\ndaily_copies = 0\n", ""))
    yield file


def command(*args: str) -> list[str]:
    return [sys.executable, "-m", "s3t", *args]


def port_of(config: Path) -> int:
    return int(next(x for x in config.read_text().splitlines() if x.startswith("port")).split()[2])


def start(config: Path) -> subprocess.Popen[str]:
    line = command("--config", str(config))
    return subprocess.Popen(line, cwd=BACKEND, stderr=subprocess.PIPE, text=True)


def set_the_password(config: Path) -> None:
    tool = [sys.executable, "tools/set_password.py", "--stdin", "--config", str(config)]
    subprocess.run(tool, cwd=BACKEND, input=PASSWORD + "\n", text=True, check=True)


def wait_until_up(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 60  # generous: the machine may be loaded
    while time.monotonic() < deadline:
        assert process.poll() is None, process.communicate()[1]
        with contextlib.suppress(httpx.HTTPError):
            httpx.get(f"http://127.0.0.1:{port}/api/session", timeout=5)
            return
    raise AssertionError("the backend did not come up")


async def test_the_process_from_start_to_end(config: Path) -> None:
    set_the_password(config)
    port = port_of(config)
    origin = f"http://127.0.0.1:{port}"
    process = start(config)
    try:
        await asyncio.to_thread(wait_until_up, process, port)

        # A second one stops at once: the first keeps its socket, its broker and its state file.
        second = await asyncio.to_thread(
            subprocess.run,
            command("--config", str(config)),
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert second.returncode == 1 and "cannot listen on 127.0.0.1" in second.stderr
        assert "Traceback" not in second.stderr and "the broker is" not in second.stderr

        async with httpx.AsyncClient(base_url=origin, headers={"origin": origin}) as http:
            answer = await http.post("/api/login", json={"password": PASSWORD})
            assert answer.status_code == 200
            cookie = answer.headers["set-cookie"].split(";")[0]
            health = (await http.get("/api/health")).json()
            assert health["data_time"].startswith("2026-11-20T15:0")  # fake_start

        url = f"ws://127.0.0.1:{port}/api/ws"
        options = {"subprotocols": ["s3t.v1.json"], "additional_headers": {"Cookie": cookie}}
        for wrong in (
            {"origin": "http://evil.example"},
            {"origin": origin, "additional_headers": {}},
        ):
            with pytest.raises(InvalidStatus) as refused:
                await connect(url, **{**options, **wrong})  # type: ignore[arg-type]
            assert refused.value.response.status_code == 403
        async with connect(url, origin=origin, **options) as ws:  # type: ignore[arg-type]
            await ws.send(json.dumps({"subscribe": {"id": 1, "quotes": {"symbol": ["SPY"]}}}))
            bids: set[float] = set()
            async with asyncio.timeout(20):
                while len(bids) < 2:  # the market moves, whatever the hour is here
                    message = json.loads(await ws.recv())
                    bid = message.get("quotes", {}).get("rows", {}).get("bid", ["NaN"])[0]
                    bids |= {bid} - {"NaN"}

            # The end, with a browser still connected.
            process.send_signal(signal.SIGTERM)
            async with asyncio.timeout(15):
                with contextlib.suppress(Exception):
                    while True:
                        await ws.recv()
            assert ws.close_code == 1012  # the service restarts: the browser comes back
        _, log = await asyncio.to_thread(process.communicate, timeout=20)
    finally:
        process.kill()
    assert process.returncode == 0, log
    assert "listening on 127.0.0.1" in log and "SIGTERM: the backend stops" in log
    assert "the broker is fake: SYNTHETIC DATA" in log  # GEN-11
    assert "stream: closed" in log and "Traceback" not in log and " ERROR " not in log
    # The store was closed: nothing is left beside the file. And a daily copy was made.
    data = config.parent / "data"
    assert {f.name for f in data.iterdir()} == {"state.sqlite", "backups"}
    assert len(list((data / "backups").glob("state-daily-*.sqlite"))) == 1


@pytest.mark.parametrize(
    ("change", "says"),
    [
        (('source = "fake"', 'source = "live"'), "no environment file"),
        (('source = "fake"', 'source = "replay"\nrecording = "none"'), "no recording named none"),
        (("[paths]", "[path]"), "paths"),
    ],
)
def test_what_keeps_it_from_starting_is_one_line(
    config: Path, change: tuple[str, str], says: str
) -> None:
    config.write_text(config.read_text().replace(*change))
    done = subprocess.run(
        command("--config", str(config)), cwd=BACKEND, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 1 and says in done.stderr
    assert "Traceback" not in done.stderr


def test_a_state_file_of_another_program_is_refused(config: Path) -> None:
    import sqlite3

    data = config.parent / "data"
    data.mkdir()
    with contextlib.closing(sqlite3.connect(data / "state.sqlite")) as conn:
        conn.execute("CREATE TABLE theirs (x)")
        conn.commit()
    done = subprocess.run(
        command("--config", str(config)), cwd=BACKEND, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 1 and "not a state file of this application" in done.stderr
    assert "Traceback" not in done.stderr
