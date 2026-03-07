"""Tests for repo_artefacts.pages module."""

from pathlib import Path
from unittest.mock import patch

from repo_artefacts.pages import get_github_info, setup_pages


def test_get_github_info(tmp_path: Path) -> None:
    """Extract org/repo from git remote output."""
    remote_output = "origin\tgit@github.com:TestOrg/test-repo.git (fetch)\n"
    with patch("subprocess.check_output", return_value=remote_output):
        org, repo = get_github_info(tmp_path)
    assert org == "TestOrg"
    assert repo == "test-repo"


def test_setup_pages_creates_files(tmp_path: Path) -> None:
    """setup_pages creates index.html and updates README."""
    readme = tmp_path / "README.md"
    readme.write_text("# Test\n\nSome content.\n")

    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        url = setup_pages(tmp_path, "TestOrg", "test-repo")

    assert url == "https://testorg.github.io/test-repo/artefacts/"
    assert (tmp_path / "docs" / "artefacts" / "index.html").exists()

    updated = readme.read_text()
    assert "<!-- ARTEFACTS:START -->" in updated
    assert "Repo Deep Dive" in updated
    assert "testorg.github.io/test-repo/artefacts/" in updated


def test_setup_pages_updates_existing_block(tmp_path: Path) -> None:
    """setup_pages replaces existing ARTEFACTS block."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Test\n\n<!-- ARTEFACTS:START -->\nold\n<!-- ARTEFACTS:END -->\n"
    )

    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        setup_pages(tmp_path, "Org", "repo")

    updated = readme.read_text()
    assert "old" not in updated
    assert "Repo Deep Dive" in updated
