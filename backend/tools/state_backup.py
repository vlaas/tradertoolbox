"""Copy the state file (STO-07). The backend may be running.

    uv run python tools/state_backup.py [--config PATH] [--to FILE]

It reads the configuration, and writes one consistent copy of ``paths.state_file``. Without
``--to`` the copy goes to the folder of the year in ``paths.backup_dir`` (by default ``backups``
beside the state file), and its name holds the time in UTC. An existing file is never overwritten,
and no copy is deleted. The running backend makes a copy a day of its own (``s3t.store.daily``).

To use a copy: stop the backend, remove the state file's ``-wal`` and ``-shm`` files, and put the
copy in the state file's place.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from s3t.config import ConfigError, load_config
from s3t.store.backup import backup, copy_path
from s3t.store.migrate import StoreError


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy the state file, also while it is in use.")
    parser.add_argument("--config", type=Path, help="the configuration file")
    parser.add_argument("--to", type=Path, help="the copy to write; it must not exist")
    args = parser.parse_args()
    try:
        paths = load_config(args.config).paths
        target = args.to or copy_path(paths.backups, paths.state_file, datetime.now(UTC))
        report = backup(paths.state_file, target)
    except (ConfigError, StoreError) as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{report.path}: {report.size} bytes, schema version {report.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
