"""Generate the code derived from schemas, or check that the committed copy is current (QA-05).

    uv run python tools/generate.py            # write (make gen)
    uv run python tools/generate.py --check    # compare only (part of make check)

Everything is first generated into a temporary directory:

- Python messages and type stubs from ``proto/`` by the protoc of ``grpcio-tools``;
- TypeScript messages from ``proto/`` by ``protoc-gen-es`` (from ``frontend/node_modules``);
- the OpenAPI schema of :func:`s3t.api.app.create_api`, and TypeScript types from it by
  ``openapi-typescript``;
- ``proto/fixtures/``: the bytes and canonical JSON of fixed messages, written by the fresh
  Python code and decoded by the frontend's unit tests, so the two languages are checked
  against each other.

The schema and the fixtures are made in a process of their own, in which ``s3t.wire`` is the fresh
tree. The application imports the wire code, so it could not be imported before a new message is
there; and one process holds one version of a .proto file.

Each output tree in :data:`OWNED` belongs to this script alone. Writing replaces the tree;
checking compares it file by file and names stale, missing and orphaned files.

Needs ``make setup`` first: the backend environment and ``frontend/node_modules``.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

import grpc_tools

REPO = Path(__file__).resolve().parents[2]
PROTO = REPO / "proto"
NODE_BIN = REPO / "frontend" / "node_modules" / ".bin"

# Output trees, relative to the repository root. Nothing else may be written into them.
PY_TREE = Path("backend/src/s3t/wire")
TS_TREE = Path("frontend/src/gen")
FIXTURE_TREE = Path("proto/fixtures")
OWNED = (PY_TREE, TS_TREE, FIXTURE_TREE)
OPENAPI = "openapi.json"

IGNORED_NAMES = {"__pycache__", ".DS_Store"}

# The fixture message. The frontend test states the same values by hand.
FIXTURE_VALUES: dict[str, Any] = {
    "time_us": -9007199254740993,  # -(2**53) - 1: not exactly representable as a double
    "value": math.nan,
    "column": [0.0, 1.5, math.nan, math.inf, -math.inf, 5e-324],
    "text": "Δ öäü €",
    "kind": "CHECK_KIND_SECOND",
    # "maybe" is left unset: presence must survive both encodings.
}


# Two rows of market.proto: a value of zero, missing values, and an instant to the microsecond.
# The frontend test states the same values by hand.
QUOTE_ROWS_VALUES: dict[str, Any] = {
    "symbol": ["SPY", "SPX"],
    "bid": [0.0, math.nan],
    "ask": [0.05, math.nan],
    "bid_size": [12.0, math.nan],
    "ask_size": [0.0, math.nan],
    "last": [math.nan, 6000.25],
    "last_size": [math.nan, math.nan],
    "day_volume": [0.0, math.nan],
    "prev_close": [599.5, 5990.0],
    "quote_received_us": [1795186800000001.0, math.nan],
    "trade_received_us": [math.nan, 1795186800500000.0],
}


# One message of each direction, and one of each topic (channel.proto, status.proto). The frontend
# test states the same values by hand.
SUBSCRIBE_VALUES: dict[str, Any] = {"subscribe": {"id": 7, "quotes": {"symbol": ["SPY", "SPX"]}}}
QUOTES_VALUES: dict[str, Any] = {
    "id": 7,
    "snapshot": True,
    "quotes": {
        "rows": QUOTE_ROWS_VALUES,
        "notes": {
            "note": [
                {"symbol": "NOSUCH", "kind": "NOTE_KIND_UNKNOWN", "text": "not known"},
            ]
        },
    },
}
STATUS_VALUES: dict[str, Any] = {
    "id": 1,
    "status": {
        "source": {"kind": "SOURCE_KIND_FAKE", "mark": "SYNTHETIC DATA"},
        "data_time_us": 1795186800000001.0,
        "account": "main",
        "stream": {
            "phase": "STREAM_PHASE_CONNECTED",
            "since_us": 1795186800000000.0,
            "reconnects": 2,
            "resubscribing": True,
            "last_gap": {"since_us": 1.0, "until_us": 2.0, "reason": "lost"},
            "aggregation_period": 0.5,
            # last_data_at_us is unset: an instant that is missing.
        },
        "subscriptions": {
            "granted": 3,
            "budget": 10000,
            "classes": [
                {"demand_class": "DEMAND_CLASS_VIEWS", "wanted": 4, "granted": 3, "counted": 3}
            ],
        },
        "rest": {"auth": "AUTH_STATE_VALID", "rate": 2.0, "burst": 4, "held_until_us": 0.0},
        # live_since_us is unset: nothing is live.
    },
}


# The chain topic (WP-1.2): a subscription with a window, and a snapshot of one expiration and one
# row. The call is computed by the backend; the put is not listed. The frontend test states the
# same values by hand.
CHAIN_SUBSCRIBE_VALUES: dict[str, Any] = {
    "subscribe": {
        "id": 9,
        "chain": {
            "underlying": "SPX",
            "window": [
                {"expiration": "SPXW:2026-12-18", "low": 5900.0, "high": 6100.0},
                {"expiration": "SPX:2026-12-18"},
            ],
        },
    }
}
CHAIN_VALUES: dict[str, Any] = {
    "id": 9,
    "snapshot": True,
    "chain": {
        "head": {
            "underlying": "SPX",
            "kind": "index",
            "spot": 6004.19,
            "spot_source": "trade",
            "data_time_us": 1795186800000001.0,
            "stale_after_s": 120.0,
            # earnings and earnings_time are empty, earnings_estimated is unset: an index.
        },
        "expiration": [
            {
                "index": 0,
                "key": "SPXW:2026-12-18",
                "root": "SPXW",
                "date": "2026-12-18",
                "settlement": "pm",
                "settles_at_us": 1797627600000000.0,
                "kind": "regular",
                "multiplier": 100.0,
                "standard": True,
                "strikes": 337,
                "forward": 6024.5,
                "forward_source": "parity",
                "atm_iv": 0.1394,
                # expected_move is unset: the quotes at the money are not there yet.
                "expected_move_method": "straddle 0.6/0.3/0.1",
                "broker_iv": 0.1392,
                "ex_dividend": [],
                "earnings_before": False,
            }
        ],
        "rows": {
            "expiration": [0],
            "strike": [6000.0],
            "iv": [0.1412],
            "call": {
                "symbol": ["SPXW  261218C06000000"],
                "bid": [89.9],
                "ask": [91.8],
                "bid_size": [math.nan],
                "ask_size": [12.0],
                "quote_us": [1795186800000001.0],
                "stale": [False],
                "iv": [0.1411],
                "delta": [0.5422],
                "gamma": [0.00194],
                "theta": [-2.2556],
                "vega": [5.7472],
                "source": ["GREEKS_SOURCE_OWN"],
                "volume": [790.0],
                "open_interest": [0.0],
            },
            "put": {
                "symbol": [""],
                "bid": [math.nan],
                "ask": [math.nan],
                "bid_size": [math.nan],
                "ask_size": [math.nan],
                "quote_us": [math.nan],
                "stale": [False],
                "iv": [math.nan],
                "delta": [math.nan],
                "gamma": [math.nan],
                "theta": [math.nan],
                "vega": [math.nan],
                "source": ["GREEKS_SOURCE_NONE"],
                "volume": [math.nan],
                "open_interest": [math.nan],
            },
        },
        "notes": {
            "note": [
                {"symbol": "SPX:2026-12-18", "kind": "NOTE_KIND_LEFT_OUT", "text": "unreadable"}
            ]
        },
    },
}


def proto_files() -> list[Path]:
    return sorted(p.relative_to(PROTO) for p in PROTO.rglob("*.proto"))


def generate(out: Path) -> None:
    """Write every owned tree below ``out``, laid out as in the repository."""
    py_root = out / "backend" / "src"  # protoc writes s3t/wire/... below it
    ts_root = out / TS_TREE
    for d in (py_root, ts_root, out / FIXTURE_TREE):
        d.mkdir(parents=True, exist_ok=True)

    _protoc(py_root, ts_root)
    _add_init_files(out / PY_TREE)
    subprocess.run([sys.executable, str(Path(__file__)), "--derive", str(out)], check=True)
    subprocess.run(
        [str(_node_tool("openapi-typescript")), str(ts_root / OPENAPI)]
        + ["-o", str(ts_root / "api.ts")],
        check=True,
        cwd=REPO / "frontend",
        stdout=subprocess.DEVNULL,
    )


def _protoc(py_root: Path, ts_root: Path) -> None:
    well_known = Path(grpc_tools.__file__).parent / "_proto"
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{PROTO}",
        f"-I{well_known}",
        f"--python_out={py_root}",
        f"--pyi_out={py_root}",
        f"--plugin=protoc-gen-es={_node_tool('protoc-gen-es')}",
        f"--es_out={ts_root}",
        "--es_opt=target=ts",
        *[str(p) for p in proto_files()],
    ]
    subprocess.run(cmd, check=True, cwd=PROTO)


def _add_init_files(tree: Path) -> None:
    for d in [tree, *(p for p in tree.rglob("*") if p.is_dir())]:
        (d / "__init__.py").write_text('"""Generated by tools/generate.py. Do not edit."""\n')


def derive(out: Path) -> None:
    """Write the schema and the fixtures below ``out``, with the fresh wire code there."""
    # No bytecode is written, so nothing but generated sources lands in the output tree.
    sys.dont_write_bytecode = True
    import s3t

    wire = ModuleType("s3t.wire")
    wire.__path__ = [str(out / PY_TREE)]
    sys.modules["s3t.wire"] = wire
    s3t.wire = wire  # type: ignore[attr-defined]
    _openapi(out / TS_TREE / OPENAPI)
    fixtures(out)


def _openapi(schema: Path) -> None:
    from s3t.api.app import create_api

    doc = create_api().openapi()
    schema.write_text(json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n")


def fixtures(out: Path) -> None:
    from s3t.wire.v1 import channel_pb2, check_pb2, market_pb2

    def write(name: str, proto: str, test: str, message: Any) -> None:
        _fixture(out / FIXTURE_TREE / f"{name}.json", proto, test, message)

    write("wirecheck", "check", "wire.test.ts", check_pb2.WireCheck(**FIXTURE_VALUES))
    write("quoterows", "market", "market.test.ts", market_pb2.QuoteRows(**QUOTE_ROWS_VALUES))
    for name, message in (
        ("subscribe", channel_pb2.ClientMessage(**SUBSCRIBE_VALUES)),
        ("quotes", channel_pb2.ServerMessage(**QUOTES_VALUES)),
        ("status", channel_pb2.ServerMessage(**STATUS_VALUES)),
        ("chain_subscribe", channel_pb2.ClientMessage(**CHAIN_SUBSCRIBE_VALUES)),
        ("chain", channel_pb2.ServerMessage(**CHAIN_VALUES)),
    ):
        write(name, "channel", "channel.test.ts", message)


def _fixture(path: Path, proto: str, test: str, message: Any) -> None:
    from google.protobuf import json_format

    doc = {
        "about": f"Generated by backend/tools/generate.py from proto/s3t/wire/v1/{proto}.proto. "
        f"Bytes and canonical proto3 JSON of one {message.DESCRIPTOR.name}, written by the Python "
        f"code and decoded by frontend/src/{test}.",
        "binary_hex": message.SerializeToString(deterministic=True).hex(),
        "json": json_format.MessageToDict(message),
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def _node_tool(name: str) -> Path:
    tool = NODE_BIN / name
    if not tool.exists():
        sys.exit(f"generate: {tool.relative_to(REPO)} is missing; run `make setup` first")
    return tool


def _files(tree: Path) -> dict[Path, Path]:
    if not tree.exists():
        return {}
    found = {}
    for p in tree.rglob("*"):
        rel = p.relative_to(tree)
        if p.is_file() and not IGNORED_NAMES.intersection(rel.parts):
            found[rel] = p
    return found


def compare_trees(expected: Path, actual: Path) -> list[tuple[str, Path]]:
    """Differences of ``actual`` against ``expected`` as (kind, relative path), sorted.

    Kinds: ``stale`` (content differs), ``missing`` (generated, not present), ``orphan``
    (present, no longer generated).
    """
    want, have = _files(expected), _files(actual)
    problems = []
    for rel in sorted(want.keys() | have.keys()):
        if rel not in have:
            problems.append(("missing", rel))
        elif rel not in want:
            problems.append(("orphan", rel))
        elif want[rel].read_bytes() != have[rel].read_bytes():
            problems.append(("stale", rel))
    return problems


def check(fresh: Path, trees: Iterable[Path] = OWNED) -> list[str]:
    return [
        f"{kind:<8}{tree / rel}"
        for tree in trees
        for kind, rel in compare_trees(fresh / tree, REPO / tree)
    ]


def write(fresh: Path, trees: Iterable[Path] = OWNED) -> None:
    for tree in trees:
        target = REPO / tree
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(fresh / tree, target, ignore=shutil.ignore_patterns(*IGNORED_NAMES))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="compare only; exit 1 if stale")
    parser.add_argument("--derive", type=Path, help=argparse.SUPPRESS)  # for generate() itself
    args = parser.parse_args(argv)
    if args.derive is not None:
        derive(args.derive)
        return 0

    with tempfile.TemporaryDirectory(prefix="s3t-gen-") as tmp:
        fresh = Path(tmp)
        generate(fresh)
        if args.check:
            problems = check(fresh)
            if problems:
                print("generated code is not current; run `make gen`:", file=sys.stderr)
                for line in problems:
                    print(f"  {line}", file=sys.stderr)
                return 1
            print(f"generated code is current ({', '.join(str(t) for t in OWNED)})")
            return 0
        write(fresh)
        print(f"generated {', '.join(str(t) for t in OWNED)}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
