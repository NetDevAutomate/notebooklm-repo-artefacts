"""Tests for repo_artefacts.pages module (including token resolution)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_artefacts.pages import (
    _ARTEFACT_ROWS,
    _build_readme_block,
    get_github_info,
    get_github_token,
    setup_pages,
)

_ALL_TYPES = set(_ARTEFACT_ROWS)

# --- get_github_info ---


def test_get_github_info(tmp_path: Path) -> None:
    """Extract org/repo from git remote output."""
    remote_output = "origin\tgit@github.com:TestOrg/test-repo.git (fetch)\n"
    with patch("subprocess.check_output", return_value=remote_output):
        org, repo = get_github_info(tmp_path)
    assert org == "TestOrg"
    assert repo == "test-repo"


def test_get_github_info_https(tmp_path: Path) -> None:
    """Extract org/repo from HTTPS remote."""
    remote_output = "origin\thttps://github.com/MyOrg/my-repo.git (fetch)\n"
    with patch("subprocess.check_output", return_value=remote_output):
        org, repo = get_github_info(tmp_path)
    assert org == "MyOrg"
    assert repo == "my-repo"


# --- setup_pages ---


def test_setup_pages_creates_files(tmp_path: Path) -> None:
    """setup_pages creates index.html and updates README."""
    readme = tmp_path / "README.md"
    readme.write_text("# Test\n\nSome content.\n")
    artefacts = tmp_path / "docs" / "artefacts"
    artefacts.mkdir(parents=True)
    (artefacts / "audio_overview.mp3").write_bytes(b"x")

    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        url = setup_pages(tmp_path, "TestOrg", "test-repo")

    assert url == "https://testorg.github.io/test-repo/artefacts/"
    assert (tmp_path / "docs" / "artefacts" / "index.html").exists()

    updated = readme.read_text()
    assert "<!-- ARTEFACTS:START -->" in updated
    assert "<!-- ARTEFACTS:END -->" in updated
    assert "## Generated Artefacts" in updated
    assert "testorg.github.io/test-repo/artefacts/" in updated


def test_setup_pages_updates_existing_block(tmp_path: Path) -> None:
    """setup_pages replaces existing ARTEFACTS block."""
    readme = tmp_path / "README.md"
    readme.write_text("# Test\n\n<!-- ARTEFACTS:START -->\nold\n<!-- ARTEFACTS:END -->\n")
    artefacts = tmp_path / "docs" / "artefacts"
    artefacts.mkdir(parents=True)
    (artefacts / "audio_overview.mp3").write_bytes(b"x")

    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        setup_pages(tmp_path, "Org", "repo")

    updated = readme.read_text()
    assert "old" not in updated
    assert "## Generated Artefacts" in updated


# --- README block structure ---


def test_readme_block_has_correct_heading() -> None:
    """README_BLOCK uses 'Generated Artefacts' heading."""
    block = _build_readme_block("https://example.github.io/repo/artefacts/", _ALL_TYPES)
    assert "## Generated Artefacts" in block
    assert "Repo Deep Dive" not in block


def test_readme_block_has_markers() -> None:
    """README_BLOCK is wrapped in ARTEFACTS markers."""
    block = _build_readme_block("https://x.github.io/r/artefacts/", _ALL_TYPES)
    assert block.startswith("<!-- ARTEFACTS:START -->")
    assert block.endswith("<!-- ARTEFACTS:END -->")


def test_readme_block_has_four_artefact_links() -> None:
    """README_BLOCK contains links for all four artefact types."""
    base = "https://example.github.io/repo/artefacts/"
    block = _build_readme_block(base, _ALL_TYPES)
    assert f"[Listen to the Audio Overview]({base})" in block
    assert f"[Watch the Video Overview]({base}#video)" in block
    assert f"[View the Infographic]({base}#infographic)" in block
    assert f"[Browse the Slide Deck]({base}#slides)" in block


def test_readme_block_anchors_match_player() -> None:
    """Anchors in README_BLOCK match the index.html section IDs."""
    block = _build_readme_block("https://x.github.io/r/artefacts/", _ALL_TYPES)
    urls = re.findall(r"\(https://[^)]+\)", block)
    valid_anchors = {"", "#video", "#infographic", "#slides"}
    for url in urls:
        url = url.strip("()")
        fragment = ""
        if "#" in url:
            fragment = "#" + url.split("#", 1)[1]
        if "github.io" in url:
            assert fragment in valid_anchors, f"Invalid anchor: {fragment}"


def test_readme_block_table_format() -> None:
    """README_BLOCK uses a proper markdown table."""
    block = _build_readme_block("https://x.github.io/r/artefacts/", _ALL_TYPES)
    lines = block.splitlines()
    table_lines = [line for line in lines if line.startswith("|")]
    # Header row + separator + 4 data rows = 6
    assert len(table_lines) == 6


# --- Token resolution ---


def test_token_from_env() -> None:
    """GITHUB_TOKEN env var is first priority."""
    with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_test123"}):
        assert get_github_token() == "ghp_test123"


def test_token_from_age_file(tmp_path: Path) -> None:
    """Falls back to tokens.age when env var not set."""
    age_output = 'export GITHUB_TOKEN="ghp_from_age"\nexport OTHER="val"\n'
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("repo_artefacts.pages.Path.home", return_value=tmp_path),
    ):
        # Create fake age files
        (tmp_path / ".config" / "secrets").mkdir(parents=True)
        (tmp_path / ".config" / "secrets" / "tokens.age").write_text("encrypted")
        (tmp_path / ".config" / "age").mkdir(parents=True)
        (tmp_path / ".config" / "age" / "keys.txt").write_text("key")

        with patch("subprocess.check_output", return_value=age_output):
            assert get_github_token() == "ghp_from_age"


def test_token_returns_none_when_nothing_available() -> None:
    """Returns None when no token source is available."""
    with (
        patch.dict("os.environ", {}, clear=True),
        patch(
            "subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "cmd"),
        ),
    ):
        assert get_github_token() is None
