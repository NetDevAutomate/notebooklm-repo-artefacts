"""Tests for publish module: verify_pages and git helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_artefacts.publish import check_artefacts, git_commit_and_push, verify_pages


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


def test_git_commit_and_push_no_changes(tmp_path: Path) -> None:
    """git_commit_and_push handles no-changes gracefully."""
    # Init a git repo with nothing to commit
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True
    )
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    # No changes to commit
    result = git_commit_and_push(tmp_path, "no changes")
    assert result is True


def test_check_artefacts_partial(tmp_path: Path) -> None:
    """check_artefacts works with partial artefact sets."""
    (tmp_path / "audio_overview.mp3").write_bytes(b"a")
    (tmp_path / "slides.pdf").write_bytes(b"s")

    found = check_artefacts(tmp_path)
    assert "audio" in found
    assert "slides" in found
    assert "video" not in found
    assert "infographic" not in found
