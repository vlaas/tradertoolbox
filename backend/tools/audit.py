"""Known vulnerabilities in the locked dependencies of both projects (requirements, section 3).

    uv run python tools/audit.py          # make audit

- Python: every package of ``backend/uv.lock``, all groups, exported by uv and checked by
  pip-audit against the Python advisory database. The project itself is left out.
- npm: ``npm audit`` over ``frontend/package-lock.json``, development packages included.

Both ask public databases over the network, so this is not part of ``make check``. Run it
before a commit that changes dependencies and at each release acceptance. It exits non-zero
when either check finds a vulnerability that is not a named exception, or cannot run.

Decided 2026-11-22: an older version is acceptable while it is maintained and has no known
exploitable vulnerability. A known vulnerability judged not exploitable here is a named
exception below, by advisory, with its reason, as the licence exceptions are. It is printed on
every run. A new advisory fails the audit.
"""

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Excepted:
    package: str
    reason: str


_CHECKER_ONLY = (
    "only inside license-checker-rseidelsohn 5.0.1 (through @npmcli/arborist), a development "
    "tool run over our own node_modules with its own patterns; not in the built app"
)

# Decided 2026-11-22. Revisit when license-checker-rseidelsohn 6 is stable (plan, section 8).
EXCEPTIONS: dict[str, dict[str, Excepted]] = {
    "python": {},
    "npm": {
        "GHSA-3g3m-2fjr-6pw4": Excepted(
            "glob",
            "a walk may continue after its abort signal; nothing here relies on that signal; "
            + _CHECKER_ONLY,
        ),
        "GHSA-g3hv-75wg-m249": Excepted(
            "minimatch",
            "denial of service by crafted glob patterns; no outside pattern reaches it; "
            + _CHECKER_ONLY,
        ),
    },
}


def python_audit() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        requirements = Path(tmp) / "requirements.txt"
        export = [
            "uv",
            "export",
            "--locked",
            "--all-groups",
            "--no-emit-project",
            "--format",
            "requirements.txt",
            "--output-file",
            str(requirements),
            "--quiet",
        ]
        subprocess.run(export, cwd=REPO / "backend", check=True)
        audit = [
            sys.executable,
            "-m",
            "pip_audit",
            "--requirement",
            str(requirements),
            "--require-hashes",
            "--disable-pip",
            "--progress-spinner",
            "off",
            *(f"--ignore-vuln={vuln}" for vuln in EXCEPTIONS["python"]),
        ]
        return subprocess.run(audit, cwd=REPO / "backend").returncode


def npm_audit() -> int:
    run = subprocess.run(
        ["npm", "audit", "--json"], cwd=REPO / "frontend", capture_output=True, text=True
    )
    try:
        report = json.loads(run.stdout)
    except ValueError:
        print(f"npm audit gave no report (exit {run.returncode}): {run.stderr.strip()[:300]}")
        return 1
    if "error" in report:
        print(f"npm audit failed: {report['error'].get('summary', report['error'])}")
        return 1
    lines, failed = evaluate_npm(report, EXCEPTIONS["npm"])
    print("\n".join(lines))
    return 1 if failed else 0


def evaluate_npm(report: dict, allowed: dict[str, Excepted]) -> tuple[list[str], int]:
    """The report lines and the number of advisories that are not named exceptions."""
    found: dict[str, tuple[str, str, str]] = {}
    for name, entry in report.get("vulnerabilities", {}).items():
        for via in entry.get("via", []):
            if isinstance(via, dict):
                advisory = str(via.get("url", "")).rsplit("/", 1)[-1] or str(via.get("source"))
                found[advisory] = (via.get("name", name), via.get("severity", "?"), via["title"])
    lines: list[str] = []
    failed = 0
    for advisory, (package, severity, title) in sorted(found.items()):
        excepted = allowed.get(advisory)
        if excepted is not None and excepted.package == package:
            lines.append(f"excepted {advisory} {package} ({severity}): {excepted.reason}")
        else:
            lines.append(f"VULNERABLE {advisory} {package} ({severity}): {title}")
            failed += 1
    for advisory in sorted(set(allowed) - set(found)):
        lines.append(f"exception {advisory} no longer needed: remove it from tools/audit.py")
    if not found:
        lines.append("No known vulnerabilities found")
    return lines, failed


def main() -> int:
    print("== Python (backend/uv.lock)", flush=True)
    python = python_audit()
    print("\n== npm (frontend/package-lock.json)", flush=True)
    npm = npm_audit()
    failed = [name for name, code in (("Python", python), ("npm", npm)) if code]
    excepted = sum(len(e) for e in EXCEPTIONS.values())
    verdict = (
        f"failed for {', '.join(failed)}"
        if failed
        else f"no known vulnerabilities outside the {excepted} named exceptions"
    )
    print(f"\naudit: {verdict}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
