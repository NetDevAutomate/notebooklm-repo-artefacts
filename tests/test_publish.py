"""Tests for repo_artefacts.publish module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_artefacts.publish import (
    STANDARD_FILES,
    check_artefacts,
    git_commit_and_push,
    verify_pages,
)

# --- check_artefacts ---


def test_check_artefacts_finds_files(tmp_path: Path) -> None:
    """Detects standard artefact files."""
    (tmp_path / "audio_overview.mp3").write_bytes(b"fake")
    (tmp_path / "video_overview.mp4").write_bytes(b"fake")
    (tmp_path / "infographic.png").write_bytes(b"fake")
    (tmp_path / "slides.pdf").write_bytes(b"fake")

    found = check_artefacts(tmp_path)
    assert set(found.keys()) == {"audio", "video", "infographic", "slides"}


def test_check_artefacts_empty_dir(tmp_path: Path) -> None:
    """Returns empty dict for empty directory."""
    assert check_artefacts(tmp_path) == {}


def test_check_artefacts_prefers_first_match(tmp_path: Path) -> None:
    """First matching file per type wins (m4a before mp3)."""
    (tmp_path / "audio_overview.m4a").write_bytes(b"m4a")
    (tmp_path / "audio_overview.mp3").write_bytes(b"mp3")

    found = check_artefacts(tmp_path)
    assert found["audio"].name == "audio_overview.m4a"


def test_standard_files_covers_all_types() -> None:
    """STANDARD_FILES maps to all four artefact types."""
    types = set(STANDARD_FILES.values())
    assert types == {"audio", "video", "infographic", "slides"}


def test_check_artefacts_partial(tmp_path: Path) -> None:
    """check_artefacts works with partial artefact sets."""
    (tmp_path / "audio_overview.mp3").write_bytes(b"a")
    (tmp_path / "slides.pdf").write_bytes(b"s")

    found = check_artefacts(tmp_path)
    assert "audio" in found
    assert "slides" in found
    assert "video" not in found
    assert "infographic" not in found


# --- verify_pages ---


def test_verify_pages_success() -> None:
    """verify_pages returns True on 200 response."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert verify_pages("https://example.github.io/repo/artefacts/", max_wait=5)


def test_verify_pages_timeout() -> None:
    """verify_pages returns False after timeout."""
    import urllib.error

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("not found"),
    ):
        assert not verify_pages("https://bad.url/", max_wait=1)


# --- git_commit_and_push ---


def _init_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


def test_git_commit_and_push_no_changes(tmp_path: Path) -> None:
    """git_commit_and_push handles no-changes gracefully."""
    _init_git_repo(tmp_path)

    # No TOOL_OUTPUTS paths exist, so nothing to stage
    result = git_commit_and_push(tmp_path, "no changes", branch="main")
    assert result is True


def test_git_commit_and_push_detached_head(tmp_path: Path) -> None:
    """git_commit_and_push refuses on detached HEAD."""
    _init_git_repo(tmp_path)
    # Detach HEAD
    subprocess.run(["git", "checkout", "--detach"], cwd=tmp_path, capture_output=True)
    result = git_commit_and_push(tmp_path, "should fail")
    assert result is False
