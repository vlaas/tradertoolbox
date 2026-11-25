"""Fixtures for the tests of the process as a whole."""

import pytest
from argon2 import PasswordHasher

CHEAP = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
"""An argon2id that takes no time. Its own parameters have a test of their own."""
PASSWORD = "canary-password-4b7e0c"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
