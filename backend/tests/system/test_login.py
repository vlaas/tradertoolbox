"""The login (SEC-01): the password, the sessions, and wrong passwords."""

import asyncio
import hashlib
import subprocess
import sys
import threading
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher

from s3t.config import load_config
from s3t.secrets import redact
from s3t.store.login import get_password_hash
from s3t.store.state import StateStore
from s3t.system.login import (
    FREE_TRIES,
    LONGEST_HOLD_S,
    Held,
    Login,
    NoPassword,
    PasswordRefused,
    WrongPassword,
    set_password,
)

from .conftest import CHEAP, PASSWORD

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 25, 10, 15, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[2]


@dataclass
class Rig:
    store: StateStore
    path: Path
    now: datetime = NOW
    mono: float = 100.0
    verified: list[str] = field(default_factory=list)

    def login(self, **options: Any) -> Login:
        options.setdefault("hasher", CHEAP)
        options.setdefault("lifetime", timedelta(hours=48))
        return Login(self.store, now=lambda: self.now, clock=lambda: self.mono, **options)


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    path = tmp_path / "state.sqlite"
    store = await StateStore.open(path)
    yield Rig(store, path)
    await store.aclose()


async def test_a_session_is_opened_found_and_ended(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    login = rig.login()
    opened = await login.log_in(PASSWORD)
    token = opened.token.get_secret_value()
    assert opened.session.created_at == NOW
    assert opened.session.expires_at == NOW + timedelta(hours=48)
    assert await login.session(token) == opened.session == await login.check(opened.session.id)
    assert await login.session(token + "x") is None and await login.session("") is None

    other = await login.log_in(PASSWORD)  # another browser
    await login.log_out(opened.session.id)
    assert await login.session(token) is None
    assert await login.check(other.session.id) == other.session
    rig.now = NOW + timedelta(hours=48)
    assert await login.check(other.session.id) is None


async def test_a_session_ends_48_hours_after_its_last_use(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    login = rig.login()
    opened = await login.log_in(PASSWORD)
    token = opened.token.get_secret_value()
    for day in range(1, 8):  # a week, used once a day
        rig.now = NOW + timedelta(days=day)
        used = await login.session(token)
        assert used is not None and used.expires_at == rig.now + timedelta(hours=48)
        assert used.created_at == NOW
    rig.now += timedelta(hours=48)
    assert await login.session(token) is None
    assert await login.renew(opened.session.id) is None  # an ended one is not renewed


async def test_looking_is_no_use_and_a_minute_is_one_use(rig: Rig) -> None:
    """Each move of the end is a write: at most one a minute."""
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    login = rig.login()
    opened = await login.log_in(PASSWORD)
    rig.now = NOW + timedelta(hours=1)
    assert await login.check(opened.session.id) == opened.session  # looked at, not moved
    renewed = await login.renew(opened.session.id)
    assert renewed is not None and renewed.expires_at == rig.now + timedelta(hours=48)
    writes: list[str] = []
    write = rig.store.write

    async def counted(fn: Any) -> Any:
        writes.append("write")
        return await write(fn)

    rig.store.write = counted  # type: ignore[method-assign]
    for seconds in range(0, 59, 5):
        rig.now = NOW + timedelta(hours=1, seconds=seconds)
        assert await login.renew(opened.session.id) == renewed
    assert writes == []
    rig.now = NOW + timedelta(hours=1, minutes=1, seconds=1)
    assert await login.renew(opened.session.id) != renewed and writes == ["write"]


async def test_the_token_is_random_and_only_its_hash_is_stored(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    login = rig.login()
    sessions = [await login.log_in(PASSWORD) for _ in range(3)]
    tokens = [s.token.get_secret_value() for s in sessions]
    assert len(set(tokens)) == 3 and all(len(t) == 43 for t in tokens)  # 32 bytes in base64url
    assert sessions[0].session.id == hashlib.sha256(tokens[0].encode()).hexdigest()
    files = b"".join(f.read_bytes() for f in rig.path.parent.iterdir())
    assert PASSWORD.encode() not in files and b"$argon2id$" in files
    assert not [t for t in tokens if t.encode() in files]
    # Nor in a log line or in the text of the records.
    assert tokens[0] not in redact(f"cookie {tokens[0]}") and tokens[0] not in repr(sessions)


async def test_the_stored_hash_is_a_strong_one(rig: Rig) -> None:
    """With the parameters that the application uses."""
    await set_password(rig.store, PASSWORD)
    stored = await rig.store.read(get_password_hash)
    assert stored is not None and stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert (await rig.login(hasher=PasswordHasher()).log_in(PASSWORD)).session


async def test_without_a_password_there_is_no_login(rig: Rig) -> None:
    login = rig.login()
    assert not await login.has_password()
    for given in ("", PASSWORD):
        with pytest.raises(NoPassword):
            await login.log_in(given)
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    assert await login.has_password()


@pytest.mark.parametrize("password", ["", "short", "x" * 11, "x" * 1025])
async def test_what_cannot_be_a_password(rig: Rig, password: str) -> None:
    with pytest.raises(PasswordRefused) as info:
        await set_password(rig.store, password, hasher=CHEAP)
    assert not password or password not in str(info.value)
    assert await rig.store.read(get_password_hash) is None


async def test_a_new_password_ends_the_sessions(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    login = rig.login()
    opened = await login.log_in(PASSWORD)
    assert await set_password(rig.store, "another-password", hasher=CHEAP) == 1
    assert await login.check(opened.session.id) is None
    with pytest.raises(WrongPassword):
        await login.log_in(PASSWORD)
    assert await login.log_in("another-password")


async def test_accents_are_one_password_however_they_are_composed(rig: Rig) -> None:
    password = "pääsuke-õunapuu"
    await set_password(rig.store, unicodedata.normalize("NFD", password), hasher=CHEAP)
    assert await rig.login().log_in(unicodedata.normalize("NFC", password))


async def test_wrong_passwords_are_held(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    login = rig.login()
    for _ in range(FREE_TRIES):
        with pytest.raises(WrongPassword):
            await login.log_in("wrong")
    holds = []
    for _ in range(9):
        with pytest.raises(Held) as held:  # the right one too
            await login.log_in(PASSWORD)
        holds.append(held.value.seconds)
        rig.mono += held.value.seconds
        with pytest.raises(WrongPassword):
            await login.log_in("wrong")
    assert holds == [1, 2, 4, 8, 16, 32, 60, 60, 60] and LONGEST_HOLD_S == 60
    rig.mono += 60
    assert await login.log_in(PASSWORD)  # and it ends the count
    for _ in range(FREE_TRIES - 1):
        with pytest.raises(WrongPassword):
            await login.log_in("wrong")
    assert await login.log_in(PASSWORD)


async def test_many_at_once_are_not_many_free_tries(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    login = rig.login()
    results = await asyncio.gather(
        *(login.log_in(f"guess {n}") for n in range(50)), return_exceptions=True
    )
    wrong = [r for r in results if isinstance(r, WrongPassword)]
    assert len(wrong) == FREE_TRIES and len(results) - len(wrong) == 45
    assert all(isinstance(r, Held) for r in results if r not in wrong)


async def test_the_check_runs_in_a_thread(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    threads: set[bool] = set()

    class Watched(PasswordHasher):
        def verify(self, hash: str | bytes, password: str | bytes) -> Any:
            threads.add(threading.current_thread() is threading.main_thread())
            return super().verify(hash, password)

    await rig.login(hasher=Watched(time_cost=1, memory_cost=8, parallelism=1)).log_in(PASSWORD)
    assert threads == {False}


async def test_an_older_hash_is_made_anew(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)
    before = await rig.store.read(get_password_hash)
    stronger = PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    opened = await rig.login(hasher=stronger).log_in(PASSWORD)
    after = await rig.store.read(get_password_hash)
    assert after != before and after is not None and "t=2" in after
    assert await rig.login().check(opened.session.id)  # the session holds


async def test_a_password_changed_while_it_was_checked(rig: Rig) -> None:
    await set_password(rig.store, PASSWORD, hasher=CHEAP)

    async def slow(fn: Any, *args: Any) -> Any:
        result = fn(*args)
        await set_password(rig.store, "another-password", hasher=CHEAP)
        return result

    with pytest.raises(WrongPassword):
        await rig.login(run=slow).log_in(PASSWORD)


def test_the_tool(tmp_path: Path) -> None:
    """From another process, as it is used."""
    config = tmp_path / "s3t.toml"
    config.write_text(
        '[server]\nlisten_address = "127.0.0.1"\nport = 8740\n'
        '[paths]\nenv_file = ".env"\ndata_dir = "data"\nstate_file = "data/state.sqlite"\n'
    )

    def tool(password: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, "tools/set_password.py", "--stdin", "--config", str(config)]
        return subprocess.run(
            command, cwd=BACKEND, input=password + "\n", capture_output=True, text=True
        )

    refused = tool("short")
    assert refused.returncode == 1 and "12 to 1024" in refused.stderr
    done = tool(PASSWORD)
    assert done.returncode == 0 and "0 sessions ended" in done.stdout
    assert PASSWORD not in done.stdout + done.stderr

    async def log_in() -> None:
        store = await StateStore.open(load_config(config).paths.state_file)
        opened = await Login(store, lifetime=timedelta(days=1)).log_in(PASSWORD)
        assert opened.session.expires_at > datetime.now(UTC)
        await store.aclose()

    asyncio.run(log_in())
    assert "1 sessions ended" in tool("another-password").stdout
