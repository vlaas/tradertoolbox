"""The licence check (QA-07): every dependency of backend/ and frontend/, runtime and
development, carries an allowed licence or is a named exception.

    uv run python tools/licences.py

The licences are collected by pip-licenses (the backend environment, with its system
packages) and license-checker-rseidelsohn (all of frontend/node_modules). The policy is
applied here, not by those tools: an SPDX expression is evaluated properly, so
``MIT AND CC-BY-3.0`` needs both parts allowed and ``MIT OR CC0-1.0`` needs one.

Decided 2026-11-22. The lists below are also given under QA-07 in the technical requirements.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Permissive licences, as SPDX identifiers and as the metadata spellings Python packages use.
ALLOWED = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "0BSD",
        "Zlib",
        "PSF-2.0",
        "Python-2.0",
        "BlueOak-1.0.0",
        "CC0-1.0",
        # Trove classifiers, as pip-licenses reports them.
        "MIT License",
        "Apache Software License",
        "BSD License",
        "3-Clause BSD License",
        "ISC License (ISCL)",
        "Python Software Foundation License",
    }
)


@dataclass(frozen=True)
class Exception_:
    licence: str
    """The licence exactly as reported: a change of licence ends the exception."""
    reason: str


_MPL_LIGHTNINGCSS = Exception_(
    "MPL-2.0", "build tool of Vite, used unmodified, not part of the built app"
)
_CC_BY_DATA = "licence-list data inside the licence checker itself; development only"

EXCEPTIONS: dict[str, dict[str, Exception_]] = {
    "python": {
        "hypothesis": Exception_("MPL-2.0", "development only, used unmodified"),
        # Proposed 2026-11-22 (WP-0.3): brought in by httpx; reported by its classifier.
        "certifi": Exception_(
            "Mozilla Public License 2.0 (MPL 2.0)",
            "CA certificate bundle of httpx, data used unmodified",
        ),
    },
    "npm": {
        "lightningcss": _MPL_LIGHTNINGCSS,
        # Its platform binaries; only the one for the running machine is installed.
        **{
            f"lightningcss-{platform}": _MPL_LIGHTNINGCSS
            for platform in (
                "darwin-x64",
                "darwin-arm64",
                "linux-x64-gnu",
                "linux-x64-musl",
                "linux-arm64-gnu",
                "linux-arm64-musl",
            )
        },
        "spdx-exceptions": Exception_("CC-BY-3.0", _CC_BY_DATA),
        "spdx-ranges": Exception_("(MIT AND CC-BY-3.0)", _CC_BY_DATA),
    },
}

# The projects themselves are not dependencies; their licence is still open.
OWN_PACKAGES = {"python": {"s3t"}, "npm": {"s3t-frontend"}}

_TOKEN = re.compile(r"\(|\)|[^\s()]+")


def expression_allowed(expression: str, allowed: frozenset[str] = ALLOWED) -> bool:
    """Whether an SPDX licence expression is satisfied by the allowed set.

    OR needs one side, AND needs both; ``X WITH Y`` counts as one licence that must itself be
    allowed. Anything that does not parse is not allowed.
    """
    tokens = _TOKEN.findall(expression)
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("unexpected end")
        pos += 1
        return tokens[pos - 1]

    def parse_or() -> bool:
        result = parse_and()
        while peek() == "OR":
            take()
            right = parse_and()
            result = result or right
        return result

    def parse_and() -> bool:
        result = parse_atom()
        while peek() == "AND":
            take()
            right = parse_atom()
            result = result and right
        return result

    def parse_atom() -> bool:
        token = take()
        if token == "(":
            result = parse_or()
            if take() != ")":
                raise ValueError("unbalanced parenthesis")
            return result
        if token in {")", "AND", "OR", "WITH"}:
            raise ValueError(f"unexpected {token}")
        if peek() == "WITH":
            take()
            token = f"{token} WITH {take()}"
        return token in allowed

    try:
        result = parse_or()
    except ValueError:
        return False
    return result and pos == len(tokens)


def licence_allowed(reported: str, allowed: frozenset[str] = ALLOWED) -> bool:
    """A reported licence string: one metadata spelling, an SPDX expression, or several
    classifiers joined by "; " (then every one must be allowed)."""
    parts = [p.strip() for p in reported.split(";") if p.strip()]
    return bool(parts) and all(p in allowed or expression_allowed(p, allowed) for p in parts)


Package = tuple[str, str, str]
"""Name, version, reported licence."""


def violations(ecosystem: str, packages: list[Package]) -> list[str]:
    exceptions = EXCEPTIONS[ecosystem]
    found = []
    for name, version, reported in sorted(packages):
        if name in OWN_PACKAGES[ecosystem] or licence_allowed(reported):
            continue
        exception = exceptions.get(name)
        if exception is not None and exception.licence == reported:
            continue
        found.append(f"{ecosystem}: {name} {version}: {reported!r} is not allowed")
    return found


def python_packages() -> list[Package]:
    out = subprocess.run(
        ["pip-licenses", "--from=mixed", "--with-system", "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [(p["Name"], p["Version"], p["License"]) for p in json.loads(out)]


def npm_packages() -> list[Package]:
    checker = REPO / "frontend" / "node_modules" / ".bin" / "license-checker-rseidelsohn"
    if not checker.exists():
        sys.exit("licences: frontend/node_modules is missing; run `make setup` first")
    out = subprocess.run(
        [str(checker), "--json", "--start", str(REPO / "frontend")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    packages = []
    for key, info in json.loads(out).items():
        name, _, version = key.rpartition("@")
        licence = info.get("licenses", "UNKNOWN")
        # Several licence fields: all of them apply, so all must be allowed.
        packages.append(
            (name, version, licence if isinstance(licence, str) else " AND ".join(licence))
        )
    return packages


def main() -> int:
    python, npm = python_packages(), npm_packages()
    problems = violations("python", python) + violations("npm", npm)
    if problems:
        print("licences: not allowed and not a named exception (tools/licences.py):")
        for line in problems:
            print(f"  {line}")
        return 1
    print(f"licences: {len(python)} Python and {len(npm)} npm packages allowed or excepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
