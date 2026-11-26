"""The backend of the browser suite (`make e2e`): the real process on the fake broker.

    uv run python tools/e2e_backend.py --port 4178 --frontend ../frontend/dist

Everything it writes is in a temporary directory that goes with it: the configuration, the state
file and the copies. The password is the one the suite knows; it protects nothing but that. The
synthetic market starts in a regular session, so it moves at any hour, and no call leaves the
machine: neither the broker nor FRED is asked (``s3t.system.backend``).
"""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from s3t.__main__ import main as backend
from s3t.config import load_config
from s3t.store.state import StateStore
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


async def prepare(config: Path) -> None:
    store = await StateStore.open(load_config(config).paths.state_file)
    await set_password(store, PASSWORD)
    await store.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="The backend of the browser suite.")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--frontend", type=Path, required=True, help="the built frontend")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="s3t-e2e-") as directory:
        config = Path(directory) / "s3t.toml"
        config.write_text(CONFIG.format(port=args.port, frontend=args.frontend.resolve()))
        asyncio.run(prepare(config))
        return backend(["--config", str(config)])


if __name__ == "__main__":
    sys.exit(main())
