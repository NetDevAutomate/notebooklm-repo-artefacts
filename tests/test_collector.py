"""Tests for repo_artefacts.collector module."""

from pathlib import Path

from repo_artefacts.collector import (
    MAX_SOURCE_LINES,
    MAX_TOTAL_BYTES,
    collect_repo_content,
)


def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a fake repo directory with given files."""
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


def test_collects_readme(tmp_path: Path) -> None:
    """README is always collected first."""
    repo = _make_repo(tmp_path, {"README.md": "# Hello\n\nWorld."})
    out = tmp_path / "out.md"
    collect_repo_content(repo, out)

    text = out.read_text()
    assert "# Hello" in text
    assert "World." in text


def test_collects_docs(tmp_path: Path) -> None:
    """Files under docs/ are collected."""
    repo = _make_repo(
        tmp_path,
        {
            "README.md": "# R",
            "docs/guide.md": "# Guide\n\nSome guide.",
        },
    )
    out = tmp_path / "out.md"
    collect_repo_content(repo, out)
    assert "Some guide." in out.read_text()


def test_collects_config(tmp_path: Path) -> None:
    """Project config files are collected."""
    repo = _make_repo(
        tmp_path,
        {
            "README.md": "# R",
            "pyproject.toml": '[project]\nname = "test"',
        },
    )
    out = tmp_path / "out.md"
    collect_repo_content(repo, out)
    assert 'name = "test"' in out.read_text()


def test_collects_source_files(tmp_path: Path) -> None:
    """Source files under src/ are collected."""
    repo = _make_repo(
        tmp_path,
        {
            "README.md": "# R",
            "src/main.py": "print('hello')",
        },
    )
    out = tmp_path / "out.md"
    collect_repo_content(repo, out)
    assert "print('hello')" in out.read_text()


def test_skips_ignored_dirs(tmp_path: Path) -> None:
    """Files in .git, node_modules, etc. are skipped."""
    repo = _make_repo(
        tmp_path,
        {
            "README.md": "# R",
            "node_modules/pkg/index.js": "bad",
            "__pycache__/mod.py": "bad",
            "src/good.py": "good",
        },
    )
    out = tmp_path / "out.md"
    collect_repo_content(repo, out)

    text = out.read_text()
    assert "bad" not in text
    assert "good" in text


def test_skips_large_source_files(tmp_path: Path) -> None:
    """Source files over MAX_SOURCE_LINES are skipped."""
    big_content = "\n".join(f"line {i}" for i in range(MAX_SOURCE_LINES + 10))
    repo = _make_repo(
        tmp_path,
        {
            "README.md": "# R",
            "src/big.py": big_content,
            "src/small.py": "small",
        },
    )
    out = tmp_path / "out.md"
    collect_repo_content(repo, out)

    text = out.read_text()
    assert "small" in text
    assert f"line {MAX_SOURCE_LINES + 5}" not in text


def test_respects_size_budget(tmp_path: Path) -> None:
    """Total output stays under MAX_TOTAL_BYTES."""
    files: dict[str, str] = {"README.md": "# R"}
    chunk = "x" * 10_000 + "\n"
    for i in range(60):
        files[f"src/mod_{i:02d}.py"] = chunk

    repo = _make_repo(tmp_path, files)
    out = tmp_path / "out.md"
    collect_repo_content(repo, out)

    assert out.stat().st_size <= MAX_TOTAL_BYTES * 1.1  # small overhead for headings
