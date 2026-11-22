"""The licence policy of tools/licences.py (QA-07)."""

import pytest

from licences import expression_allowed, licence_allowed, violations


@pytest.mark.parametrize(
    "expression",
    ["MIT", "(MIT)", "MIT OR GPL-3.0", "(Apache-2.0 AND BSD-3-Clause)", "(MIT OR CC0-1.0)",
     "GPL-2.0 OR (MIT AND ISC)"],
)  # fmt: skip
def test_allowed_expressions(expression: str) -> None:
    assert expression_allowed(expression)


@pytest.mark.parametrize(
    "expression",
    ["MPL-2.0", "(MIT AND CC-BY-3.0)", "MIT AND GPL-3.0", "Apache-2.0 WITH LLVM-exception",
     "(MIT", "MIT)", "MIT OR", "AND MIT", "", "MIT*", "UNKNOWN"],
)  # fmt: skip
def test_refused_expressions(expression: str) -> None:
    assert not expression_allowed(expression)


def test_metadata_spellings() -> None:
    assert licence_allowed("3-Clause BSD License")
    assert licence_allowed("MIT License; Apache Software License")
    assert not licence_allowed("MIT License; Mozilla Public License 2.0 (MPL 2.0)")
    assert not licence_allowed("")


def test_exception_needs_the_name_and_the_same_licence() -> None:
    assert violations("python", [("hypothesis", "1", "MPL-2.0")]) == []
    assert violations("python", [("hypothesis", "1", "GPL-3.0")]) != []
    assert violations("python", [("other", "1", "MPL-2.0")]) != []


def test_own_package_is_not_a_dependency() -> None:
    assert violations("python", [("s3t", "0.0.1", "UNKNOWN")]) == []
    assert violations("npm", [("s3t-frontend", "0.0.1", "UNLICENSED")]) == []
