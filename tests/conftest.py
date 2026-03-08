"""Shared test fixtures for repo-artefacts test suite."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def make_repo(tmp_path: Path) -> Callable[[dict[str, str]], Path]:
    """Factory: creates a repo directory with given files and .git marker."""

    def _make(files: dict[str, str]) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        (repo / ".git").mkdir(exist_ok=True)
        for name, content in files.items():
            p = repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return repo

    return _make


@pytest.fixture
def artefacts_repo(tmp_path: Path) -> Path:
    """Pre-built repo with all four standard artefact files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "README.md").write_text("# Test Repo\n\nDescription.\n")
    arts = repo / "docs" / "artefacts"
    arts.mkdir(parents=True)
    (arts / "audio_overview.mp3").write_bytes(b"fake-audio")
    (arts / "video_overview.mp4").write_bytes(b"fake-video")
    (arts / "infographic.png").write_bytes(b"fake-img")
    (arts / "slides.pdf").write_bytes(b"fake-pdf")
    return repo


@pytest.fixture
def artefacts_dir(artefacts_repo: Path) -> Path:
    """The docs/artefacts directory from artefacts_repo."""
    return artefacts_repo / "docs" / "artefacts"
