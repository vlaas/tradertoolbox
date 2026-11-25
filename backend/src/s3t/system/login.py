"""The login of the one user (SEC-01). Proposed 2026-11-25 (WP-0.7).

- **The password** is stored as an argon2id hash with the library's parameters (RFC 9106, the
  low-memory ones: 64 MiB, three passes). It is set by ``tools/set_password.py`` on the machine,
  never over the network, and setting it ends every session. A login with older parameters stores
  the hash anew.
- **A session** is 256 random bits. The browser has them in a cookie, the state file has their
  SHA-256, so a copy of the state file opens no session. It ends at a fixed time, at a logout, or
  with the password.
- **Wrong passwords.** The first five cost nothing. After that each one holds every login, 1 s at
  first, twice as long each time, at most 60 s. The right password ends it. There is one user, so
  one count; it is not kept over a restart. One password is checked at a time, and the hold is
  looked at inside the same lock, so that many requests at once are not many free tries.

A check takes some 64 MiB and a tenth of a second by design, so it runs in a thread.
"""

import asyncio
import hashlib
import logging
import secrets
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import SecretStr

from s3t.broker.limits import Clock
from s3t.secrets import REDACTOR
from s3t.store.login import (
    SessionRow,
    add_session,
    delete_expired,
    delete_session,
    find_session,
    get_password_hash,
    replace_password_hash,
    set_password_hash,
)
from s3t.store.state import StateStore

SHORTEST, LONGEST = 12, 1024
FREE_TRIES = 5
FIRST_HOLD_S, LONGEST_HOLD_S = 1.0, 60.0
TOKEN_BYTES = 32

LOG = logging.getLogger(__name__)

type Run = Callable[..., Awaitable[Any]]
"""Runs a blocking function elsewhere: ``asyncio.to_thread``."""


class LoginRefused(Exception):
    """No session was opened."""


class WrongPassword(LoginRefused):
    pass


class NoPassword(LoginRefused):
    """No password has been set yet."""


class Held(LoginRefused):
    """There were too many wrong passwords."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"held for {seconds:.0f} s")
        self.seconds = seconds


class PasswordRefused(ValueError):
    """This cannot be a password. The message does not repeat it."""


@dataclass(frozen=True)
class Session:
    """A session as the application knows it: without its token."""

    id: str
    """The SHA-256 of the token. Not a secret that opens anything, but no reason to show it."""
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class NewSession:
    token: SecretStr
    """For the cookie, and for nothing else."""
    session: Session


def _normal(password: str) -> str:
    # One password, whichever way a keyboard composes its accents.
    return unicodedata.normalize("NFC", password)


def _id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _session(row: SessionRow) -> Session:
    return Session(row.token_sha256, row.created_at, row.expires_at)


async def set_password(
    store: StateStore,
    password: str,
    *,
    hasher: PasswordHasher | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Set the password. Returns how many sessions it ended."""
    if not SHORTEST <= len(_normal(password)) <= LONGEST:
        raise PasswordRefused(f"a password has {SHORTEST} to {LONGEST} characters")
    password_hash = await asyncio.to_thread((hasher or PasswordHasher()).hash, _normal(password))
    return await store.write(lambda conn: set_password_hash(conn, password_hash, now()))


class Login:
    def __init__(
        self,
        store: StateStore,
        *,
        lifetime: timedelta,
        hasher: PasswordHasher | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Clock = time.monotonic,
        run: Run = asyncio.to_thread,
    ) -> None:
        self._store, self._lifetime = store, lifetime
        self._hasher = hasher or PasswordHasher()
        self._now, self._clock, self._in_thread = now, clock, run
        self._lock = asyncio.Lock()
        self._failures = 0
        self._held_until = 0.0

    def __repr__(self) -> str:
        return "Login()"

    async def has_password(self) -> bool:
        return await self._store.read(get_password_hash) is not None

    async def log_in(self, password: str) -> NewSession:
        """Open a session. Raises :class:`LoginRefused`."""
        async with self._lock:
            wait = self._held_until - self._clock()
            if wait > 0:
                raise Held(wait)
            password_hash = await self._store.read(get_password_hash)
            if password_hash is None:
                raise NoPassword("no password has been set")
            given = _normal(password[: LONGEST + 1])
            try:
                await self._in_thread(self._hasher.verify, password_hash, given)
            except VerifyMismatchError:
                self._wrong()
                raise WrongPassword("wrong password") from None
            if self._hasher.check_needs_rehash(password_hash):
                anew = str(await self._in_thread(self._hasher.hash, given))
                old, password_hash = password_hash, anew
                await self._store.write(lambda conn: replace_password_hash(conn, old, anew))
            opened = await self._open(password_hash)
            if opened is None:  # it was changed while it was checked
                raise WrongPassword("wrong password")
            self._failures = 0
            return opened

    def _wrong(self) -> None:
        self._failures += 1
        over = self._failures - FREE_TRIES
        if over >= 0:
            hold = min(FIRST_HOLD_S * 2.0 ** min(over, 10), LONGEST_HOLD_S)
            self._held_until = self._clock() + hold
            LOG.warning("login: %d wrong passwords; held for %.0f s", self._failures, hold)

    async def _open(self, password_hash: str) -> NewSession | None:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        REDACTOR.register(token, kind="session")
        at = self._now()
        row = SessionRow(_id(token), at, at + self._lifetime)
        await self._store.write(lambda conn: delete_expired(conn, at))
        if not await self._store.write(lambda conn: add_session(conn, row, password_hash)):
            return None
        LOG.info("login: a session was opened")
        return NewSession(SecretStr(token), _session(row))

    async def session(self, token: str) -> Session | None:
        """The session of a token, if it is one now."""
        return await self.check(_id(token))

    async def check(self, session_id: str) -> Session | None:
        """The session, if it is still one."""
        at = self._now()
        row = await self._store.read(lambda conn: find_session(conn, session_id, at))
        return None if row is None else _session(row)

    async def log_out(self, session_id: str) -> None:
        if await self._store.write(lambda conn: delete_session(conn, session_id)):
            LOG.info("login: a session was ended")
