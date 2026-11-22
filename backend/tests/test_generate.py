"""The stale check of tools/generate.py (QA-05)."""

from pathlib import Path

from generate import OWNED, REPO, compare_trees


def tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_equal_trees(tmp_path: Path) -> None:
    files = {"a.py": "x", "sub/b.ts": "y"}
    assert compare_trees(tree(tmp_path / "e", files), tree(tmp_path / "a", files)) == []


def test_stale_missing_and_orphan(tmp_path: Path) -> None:
    expected = tree(tmp_path / "e", {"same": "1", "changed": "new", "added": "3"})
    actual = tree(tmp_path / "a", {"same": "1", "changed": "old", "removed": "4"})
    assert compare_trees(expected, actual) == [
        ("missing", Path("added")),
        ("stale", Path("changed")),
        ("orphan", Path("removed")),
    ]


def test_absent_tree_is_all_missing(tmp_path: Path) -> None:
    expected = tree(tmp_path / "e", {"a": "1"})
    assert compare_trees(expected, tmp_path / "absent") == [("missing", Path("a"))]


def test_caches_are_ignored(tmp_path: Path) -> None:
    expected = tree(tmp_path / "e", {"a": "1"})
    actual = tree(tmp_path / "a", {"a": "1", "__pycache__/a.pyc": "z", "x/.DS_Store": "z"})
    assert compare_trees(expected, actual) == []


def test_owned_trees_exist_and_are_separate() -> None:
    for t in OWNED:
        assert (REPO / t).is_dir(), t
    for a in OWNED:
        for b in OWNED:
            assert a == b or not a.is_relative_to(b)


def test_generated_trees_are_listed() -> None:
    """The root .gitattributes lists exactly the trees the script owns."""
    listed = set()
    for line in (REPO / ".gitattributes").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("#") and "linguist-generated=true" in parts:
            assert parts[0].endswith("/**"), parts[0]
            listed.add(Path(parts[0].removesuffix("/**")))
    assert listed == set(OWNED)
