"""Every data file the code loads at runtime must be declared as package data.

A missing entry only shows up after ``pip install`` — the source tree still has the file — so the
failure lands on a user, not on CI. This test compares the tree with the declaration.
"""

import fnmatch
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "auditcodes"


def _patterns() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["tool"]["setuptools"]["package-data"]["auditcodes"]


def _data_files() -> list[str]:
    out = []
    for f in PACKAGE.rglob("*"):
        if f.is_file() and f.suffix not in (".py", ".pyc") and "__pycache__" not in f.parts:
            out.append(f.relative_to(PACKAGE).as_posix())
    return sorted(out)


def test_every_data_file_is_declared():
    patterns = _patterns()
    missing = [f for f in _data_files() if not any(fnmatch.fnmatch(f, p) for p in patterns)]
    assert not missing, f"not covered by package-data in pyproject.toml: {missing}"


def test_known_runtime_assets_present():
    expected = ["app/static/app.css", "app/templates/base.html", "app/templates/question.html", "export/templates/document.html"]
    files = _data_files()
    assert all(e in files for e in expected), [e for e in expected if e not in files]


@pytest.mark.parametrize("pattern", _patterns())
def test_no_stale_patterns(pattern):
    assert any(fnmatch.fnmatch(f, pattern) for f in _data_files()), f"pattern {pattern!r} matches nothing"
