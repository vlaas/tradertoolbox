"""Repository guards: no market data or credentials (GEN-07), no machine-specific values in
code and configuration (GEN-08), the subscription manager as the only caller of subscribe
and unsubscribe (SUB-02), the keys of the broker's instrument documents inside the broker
module only (GEN-09), one module that opens SQLite files (STO-01) and one that imports the
calendar library, one implementation of the numerics (GEN-05), services that do not know who calls
them (API-01) and messages that do not know what carries them (API-08).

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


KEY = re.compile(r"[a-z0-9]+(-[a-z0-9]+)+")


def _keys(path: Path) -> set[str]:
    """The string constants of a file that look like a key of a broker document."""
    tree = ast.parse(path.read_text())
    constants = (n.value for n in ast.walk(tree) if isinstance(n, ast.Constant))
    return {value for value in constants if isinstance(value, str) and KEY.fullmatch(value)}


def test_broker_document_keys_stay_in_the_broker_module() -> None:
    """GEN-09, for the documents that are typed (WP-0.5): instruments and chains. The market
    metrics are still the broker's documents, so their keys are not in the list. The words
    without a hyphen (``symbol``, ``strikes``) are ordinary words and are left to review."""
    src = REPO / "backend" / "src" / "s3t"
    keys = _keys(src / "broker" / "tastytrade" / "instruments.py")
    assert {"streamer-symbol", "strike-price", "option-chains", "expires-at"} <= keys
    inside = (src / "broker" / "tastytrade", src / "broker" / "fake")
    leaks = {
        str(path.relative_to(src)): sorted(found)
        for path in src.rglob("*.py")
        if not path.is_relative_to(inside[0]) and not path.is_relative_to(inside[1])
        if (found := _keys(path) & keys)
    }
    assert leaks == {}


# A module that only some files may import, and those files.
IMPORTERS = {
    "sqlite3": ("backend/src/s3t/store/", "the application's state is in one file (STO-01)"),
    "exchange_calendars": ("backend/src/s3t/refdata/exchange.py", "one reader of the library"),
    "pandas": ("backend/src/s3t/refdata/exchange.py", "it comes with exchange_calendars"),
    "argon2": ("backend/src/s3t/system/", "one place hashes the password (SEC-01)"),
    "uvicorn": ("backend/src/s3t/api/server.py", "one place starts a server (SEC-03)"),
}


def _imports(path: Path) -> set[str]:
    """The top-level names of everything a file imports, inside its functions too."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found.add((node.module or "").split(".")[0])
    return found


def test_the_modules_with_one_importer() -> None:
    seen: dict[str, set[str]] = {module: set() for module in IMPORTERS}
    for f in committable():
        if f.startswith(("backend/src/s3t/", "backend/tools/")) and f.endswith(".py"):
            for module in _imports(REPO / f) & IMPORTERS.keys() if (REPO / f).is_file() else ():
                seen[module].add(f)
    for module, (allowed, reason) in IMPORTERS.items():
        assert all(f.startswith(allowed) for f in seen[module]), f"{module}: {reason}"
    assert all(seen[module] for module in IMPORTERS if module != "pandas")


SRC = REPO / "backend" / "src" / "s3t"
WEB = {"fastapi", "starlette", "uvicorn", "websockets", "httpx"}


def _modules(path: Path) -> set[str]:
    """The full names of everything a file imports, with the names taken from a module."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            found |= {node.module, *(f"{node.module}.{alias.name}" for alias in node.names)}
    return found


def _inside(modules: set[str], package: str) -> set[str]:
    return {m for m in modules if m == package or m.startswith(f"{package}.")}


def test_the_messages_do_not_know_what_carries_them() -> None:
    """API-08: one module is the carrier, and another one starts the server. The messages, the
    topics and what they are made of know neither."""
    for name in ("channel", "topics", "status", "rows"):
        path = SRC / "api" / f"{name}.py"
        assert not _imports(path) & WEB, name
        for carrier in ("ws", "server", "app", "guard", "routes", "frontend"):
            assert not _inside(_modules(path), f"s3t.api.{carrier}"), f"{name}: {carrier}"
    websocket = {"starlette.websockets", "fastapi.WebSocket"}
    users = {p.name for p in (SRC / "api").glob("*.py") if _modules(p) & websocket}
    assert users == {"ws.py"}


def test_the_services_do_not_know_who_calls_them() -> None:
    """API-01: a route and an MCP tool call the same function, so a service knows neither."""
    for path in SRC.rglob("*.py"):
        package = path.relative_to(SRC).parts[0]
        if package in ("api", "mcp", "__main__.py", "wire"):
            continue
        assert not _inside(_modules(path), "s3t.api"), path.name
        assert not _inside(_modules(path), "s3t.mcp"), path.name
        assert not _imports(path) & {"fastapi", "starlette", "uvicorn"}, path.name


# What computes a price, a volatility, a Greek or a forward belongs to these (GEN-05). The synthetic
# market prices its options with the same functions.
NUMERICS = {"scipy", "statistics"}
NUMERIC_CALLS = {"erf", "erfc", "NormalDist", "ndtr", "norm"}


def test_one_implementation_of_the_numerics() -> None:
    """GEN-05, the structure: the pricing core is the only one that has the tools for a second one,
    and nothing at the edge of the process computes."""
    for path in SRC.rglob("*.py"):
        package = path.relative_to(SRC).parts[0]
        if package in ("pricing", "wire"):
            continue
        assert not _imports(path) & NUMERICS, path.name
        names = {
            n.attr for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Attribute)
        }
        taken = {m.rsplit(".", 1)[-1] for m in _modules(path)}
        assert not (names | taken) & NUMERIC_CALLS, path.name
        if package in ("api", "system", "mcp"):
            assert "numpy" not in _imports(path), path.name
            assert not _inside(_modules(path), "s3t.pricing"), path.name
