"""Tests for repo_artefacts.collector module."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from repo_artefacts.collector import (
    MAX_SOURCE_LINES,
    MAX_TOTAL_BYTES,
    collect_repo_content,
)
from repo_artefacts.exceptions import CollectionError


def test_collects_readme(make_repo: Callable[[dict[str, str]], Path]) -> None:
    """README is always collected first."""
    repo = make_repo({"README.md": "# Hello\n\nWorld."})
    out = repo / "out.md"
    collect_repo_content(repo, out)

    text = out.read_text()
    assert "# Hello" in text
    assert "World." in text


def test_collects_docs(make_repo: Callable[[dict[str, str]], Path]) -> None:
    """Files under docs/ are collected."""
    repo = make_repo(
        {
            "README.md": "# R",
            "docs/guide.md": "# Guide\n\nSome guide.",
        },
    )
    out = repo / "out.md"
    collect_repo_content(repo, out)
    assert "Some guide." in out.read_text()


def test_collects_config(make_repo: Callable[[dict[str, str]], Path]) -> None:
    """Project config files are collected."""
    repo = make_repo(
        {
            "README.md": "# R",
            "pyproject.toml": '[project]\nname = "test"',
        },
    )
    out = repo / "out.md"
    collect_repo_content(repo, out)
    assert 'name = "test"' in out.read_text()


def test_collects_source_files(make_repo: Callable[[dict[str, str]], Path]) -> None:
    """Source files under src/ are collected."""
    repo = make_repo(
        {
            "README.md": "# R",
            "src/main.py": "print('hello')",
        },
    )
    out = repo / "out.md"
    collect_repo_content(repo, out)
    assert "print('hello')" in out.read_text()


def test_skips_ignored_dirs(make_repo: Callable[[dict[str, str]], Path]) -> None:
    """Files in .git, node_modules, etc. are skipped."""
    repo = make_repo(
        {
            "README.md": "# R",
            "node_modules/pkg/index.js": "bad",
            "__pycache__/mod.py": "bad",
            "src/good.py": "good",
        },
    )
    out = repo / "out.md"
    collect_repo_content(repo, out)

    text = out.read_text()
    assert "bad" not in text
    assert "good" in text


def test_skips_large_source_files(make_repo: Callable[[dict[str, str]], Path]) -> None:
    """Source files over MAX_SOURCE_LINES are skipped."""
    big_content = "\n".join(f"line {i}" for i in range(MAX_SOURCE_LINES + 10))
    repo = make_repo(
        {
            "README.md": "# R",
            "src/big.py": big_content,
            "src/small.py": "small",
        },
    )
    out = repo / "out.md"
    collect_repo_content(repo, out)

    text = out.read_text()
    assert "small" in text
    assert f"line {MAX_SOURCE_LINES + 5}" not in text


def test_respects_size_budget(make_repo: Callable[[dict[str, str]], Path]) -> None:
    """Total output stays under MAX_TOTAL_BYTES."""
    files: dict[str, str] = {"README.md": "# R"}
    chunk = "x" * 10_000 + "\n"
    for i in range(60):
        files[f"src/mod_{i:02d}.py"] = chunk

    repo = make_repo(files)
    out = repo / "out.md"
    collect_repo_content(repo, out)

    assert out.stat().st_size <= MAX_TOTAL_BYTES * 1.1  # small overhead for headings


def test_rejects_non_git_repo(tmp_path: Path) -> None:
    """Raises CollectionError for a directory without .git."""
    (tmp_path / "README.md").write_text("# Hi")
    out = tmp_path / "out.md"
    with pytest.raises(CollectionError, match="not a git repository"):
        collect_repo_content(tmp_path, out)


def test_rejects_non_directory(tmp_path: Path) -> None:
    """Raises CollectionError for a non-existent path."""
    out = tmp_path / "out.md"
    with pytest.raises(CollectionError, match="not a directory"):
        collect_repo_content(tmp_path / "nonexistent", out)
