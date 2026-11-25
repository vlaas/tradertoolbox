"""Set the password of the app (SEC-01). The backend may be running.

    uv run python tools/set_password.py [--config PATH] [--stdin]

It asks for the password twice, without showing it, and stores its argon2id hash in the state file
that the configuration names. Every session ends: each browser logs in anew. With ``--stdin`` the
password is the first line of the standard input.

A state file from before the login is migrated first, and copied before that.
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from s3t.config import ConfigError, load_config
from s3t.store.migrate import StoreError
from s3t.store.state import StateStore
from s3t.system.login import PasswordRefused, set_password


async def store_password(config_path: Path | None, password: str) -> int:
    paths = load_config(config_path).paths
    store = await StateStore.open(paths.state_file, backup_dir=paths.backups)
    try:
        return await set_password(store, password)
    finally:
        await store.aclose()


def ask(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("New password: ")
    if getpass.getpass("The same again: ") != password:
        raise PasswordRefused("the two are not the same")
    return password


def main() -> int:
    parser = argparse.ArgumentParser(description="Set the password of the app.")
    parser.add_argument("--config", type=Path, help="the configuration file")
    parser.add_argument("--stdin", action="store_true", help="read it from the standard input")
    args = parser.parse_args()
    try:
        ended = asyncio.run(store_password(args.config, ask(args.stdin)))
    except (ConfigError, StoreError, PasswordRefused) as e:
        print(e, file=sys.stderr)
        return 1
    print(f"the password is set; {ended} sessions ended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
