"""The vulnerability audit's handling of named exceptions (tools/audit.py)."""

from audit import EXCEPTIONS, Excepted, evaluate_npm

ALLOWED = {"GHSA-aaaa-bbbb-cccc": Excepted("glob", "not reachable here")}


def report(*advisories: tuple[str, str]) -> dict:
    vulnerabilities = {
        package: {
            "via": [
                {
                    "url": f"https://github.com/advisories/{advisory}",
                    "name": package,
                    "severity": "moderate",
                    "title": "a title",
                },
                "a-package-that-depends-on-it",
            ]
        }
        for advisory, package in advisories
    }
    return {"vulnerabilities": vulnerabilities}


def test_an_excepted_advisory_passes_and_is_printed() -> None:
    lines, failed = evaluate_npm(report(("GHSA-aaaa-bbbb-cccc", "glob")), ALLOWED)
    assert failed == 0
    assert lines == ["excepted GHSA-aaaa-bbbb-cccc glob (moderate): not reachable here"]


def test_a_new_advisory_fails() -> None:
    lines, failed = evaluate_npm(
        report(("GHSA-aaaa-bbbb-cccc", "glob"), ("GHSA-dddd-eeee-ffff", "tar")), ALLOWED
    )
    assert failed == 1
    assert "VULNERABLE GHSA-dddd-eeee-ffff tar (moderate): a title" in lines


def test_an_exception_holds_for_its_package_only() -> None:
    _, failed = evaluate_npm(report(("GHSA-aaaa-bbbb-cccc", "minimatch")), ALLOWED)
    assert failed == 1


def test_an_unneeded_exception_is_reported() -> None:
    lines, failed = evaluate_npm({"vulnerabilities": {}}, ALLOWED)
    assert failed == 0
    assert lines[0].startswith("exception GHSA-aaaa-bbbb-cccc no longer needed")


def test_every_exception_has_a_reason() -> None:
    for exceptions in EXCEPTIONS.values():
        for advisory, excepted in exceptions.items():
            assert advisory.startswith("GHSA-") and excepted.package and excepted.reason
