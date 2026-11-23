"""Repository guards: no market data or credentials (GEN-07), no machine-specific values in
code and configuration (GEN-08), and the subscription manager as the only caller of
subscribe and unsubscribe (SUB-02).

The files examined are those git would commit: tracked files plus untracked files that are
not ignored. Documents are covered by review, not by this scan.
"""

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

DATA_SUFFIXES = {
    ".parquet", ".arrow", ".feather", ".ipc", ".jsonl", ".ndjson", ".csv", ".tsv",
    ".sqlite", ".sqlite3", ".db", ".duckdb", ".h5", ".hdf5", ".zst", ".gz",
}  # fmt: skip
MUST_BE_IGNORED = [
    ".env",
    ".env.local",
    "backend/.env",
    "config/s3t.toml",
    "data/candles/SPY.parquet",
]
# Code and configuration; documents are reviewed instead.
SCANNED = ("backend", "frontend", "proto", "config", "Makefile", ".gitignore")
MACHINE_SPECIFIC = re.compile(
    r"/Users/[A-Za-z]"
    r"|/home/[a-z]"
    r"|[A-Za-z]:\\Users\\"
    r"|\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"
)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def committable() -> list[str]:
    result = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode != 0:
        pytest.skip("not a git work tree: the repository guards need git")
    return [f for f in result.stdout.split("\0") if f]


@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_local_files_are_ignored(path: str) -> None:
    committable()  # skips outside a work tree
    assert git("check-ignore", "-q", "--no-index", path).returncode == 0, path


def test_no_data_or_environment_files() -> None:
    offending = [
        f
        for f in committable()
        if Path(f).suffix.lower() in DATA_SUFFIXES
        or Path(f).name == ".env"
        or Path(f).name.startswith(".env.")
    ]
    assert offending == []


def test_no_machine_specific_values_in_code() -> None:
    this = Path(__file__).resolve()
    hits = []
    for f in committable():
        path = REPO / f
        if not f.startswith(SCANNED) or path.resolve() == this or not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if MACHINE_SPECIFIC.search(line):
                hits.append(f"{f}:{n}")
    assert hits == []


# Outside ``s3t.subs`` nothing calls subscribe or unsubscribe (SUB-02), except these files.
SUBSCRIBE_CALLERS = {
    "backend/src/s3t/broker/recording.py": "the recording wrapper passes the manager's calls on",
    "backend/tools/stream_check.py": "the live check of the session alone, without the feed",
}


def test_only_the_subscription_manager_calls_subscribe() -> None:
    callers = set()
    for f in committable():
        if not f.startswith(("backend/src/s3t/", "backend/tools/")) or not f.endswith(".py"):
            continue
        if f.startswith("backend/src/s3t/subs/") or not (REPO / f).is_file():
            continue
        tree = ast.parse((REPO / f).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("subscribe", "unsubscribe")
            ):
                callers.add(f)
    assert callers == set(SUBSCRIBE_CALLERS)
