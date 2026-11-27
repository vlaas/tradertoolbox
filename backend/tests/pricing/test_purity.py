"""`pricing` is a pure library (PRC-01): a check of its source, beside the code review.

Every module of s3t/pricing may import only the allow-list below, calls no built-in that does
I/O or runs code, declares no `global`, and binds no mutable container at module level. The
standard-library logger is allowed: without a handler, which only the application attaches, it
does no I/O (decided in WP-0.2, 2026-11-22, proposed to the owner).
"""

import ast
from pathlib import Path

import pytest

PRICING = Path(__file__).resolve().parents[2] / "src" / "s3t" / "pricing"

ALLOWED_IMPORTS = {
    "collections.abc",
    "dataclasses",
    "datetime",
    "logging",
    "math",
    "numpy",
    "scipy.optimize",  # since WP-1.1: the SVI and SSVI fits (least_squares)
    "scipy.special",
    "types",
    "typing",
}
FORBIDDEN_CALLS = {"open", "print", "input", "eval", "exec", "compile", "__import__", "breakpoint"}
MUTABLE_CALLS = {"list", "dict", "set", "bytearray", "defaultdict", "OrderedDict", "deque"}
MUTABLE_NODES = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)


def _allowed(module: str) -> bool:
    return module == "s3t.pricing" or module.startswith("s3t.pricing.") or module in ALLOWED_IMPORTS


def violations(source: str) -> list[str]:
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if not _allowed(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.level or not _allowed(node.module or ""):
                found.append(f"from {'.' * node.level}{node.module or ''} import")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                found.append(f"call {node.func.id}()")
        elif isinstance(node, ast.Global):
            found.append(f"global {', '.join(node.names)}")
    for node in tree.body:  # module level only
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        if isinstance(value, MUTABLE_NODES) or (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in MUTABLE_CALLS
        ):
            found.append(f"mutable module-level value at line {node.lineno}")
    return found


MODULES = sorted(PRICING.glob("*.py"))


def test_the_package_is_found():
    names = {p.stem for p in MODULES}
    assert {"bsm", "black76", "escrowed", "forward", "clocks", "rates"} <= names
    assert {"parity", "svi", "ssvi", "surface", "carry"} <= names  # WP-1.1


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_module_is_pure(path: Path):
    assert violations(path.read_text()) == []


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "from pathlib import Path",
        "import httpx",
        "from scipy import stats",
        "from scipy import optimize",
        "import scipy.interpolate",
        "from . import bsm",
        "def f():\n    open('x')",
        "def f():\n    global X",
        "CACHE = {}",
        "SEEN: list[int] = []",
        "TABLE = dict(a=1)",
    ],
)
def test_the_check_catches(source: str):
    assert violations(source) != []


def test_source_headers_name_the_origin():
    """PRC-03: a module taken from another repository says which one and at which commit."""
    for path in MODULES:
        head = path.read_text().split('"""')[1]
        if "Source:" in head:
            assert "volcalc" in head and "f53be83" in head, path.name
    ported = {p.name for p in MODULES if "Source:" in p.read_text().split('"""')[1]}
    assert ported == {
        "bsm.py",
        "black76.py",
        "forward.py",
        "clocks.py",
        "rates.py",
        "parity.py",  # WP-1.1, with the three below
        "svi.py",
        "ssvi.py",
        "surface.py",
    }
