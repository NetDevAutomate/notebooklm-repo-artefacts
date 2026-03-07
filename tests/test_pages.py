"""Tests for repo_artefacts.pages module."""

from pathlib import Path
from unittest.mock import patch

from repo_artefacts.pages import README_BLOCK, get_github_info, setup_pages


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
    assert "<!-- ARTEFACTS:END -->" in updated
    assert "## Generated Artefacts" in updated
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
    assert "## Generated Artefacts" in updated


# --- README block structure tests ---


def test_readme_block_has_correct_heading() -> None:
    """README_BLOCK uses 'Generated Artefacts' heading."""
    block = README_BLOCK.format(base_url="https://example.github.io/repo/artefacts/")
    assert "## Generated Artefacts" in block
    assert "Repo Deep Dive" not in block


def test_readme_block_has_markers() -> None:
    """README_BLOCK is wrapped in ARTEFACTS markers."""
    block = README_BLOCK.format(base_url="https://x.github.io/r/artefacts/")
    assert block.startswith("<!-- ARTEFACTS:START -->")
    assert block.endswith("<!-- ARTEFACTS:END -->")


def test_readme_block_has_four_artefact_links() -> None:
    """README_BLOCK contains links for all four artefact types."""
    base = "https://example.github.io/repo/artefacts/"
    block = README_BLOCK.format(base_url=base)
    assert f"[Listen to the Audio Overview]({base})" in block
    assert f"[Watch the Video Overview]({base}#video)" in block
    assert f"[View the Infographic]({base}#infographic)" in block
    assert f"[Browse the Slide Deck]({base}#slides)" in block


def test_readme_block_anchors_match_player() -> None:
    """Anchors in README_BLOCK match the index.html section IDs."""
    import re

    block = README_BLOCK.format(base_url="https://x.github.io/r/artefacts/")
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
    block = README_BLOCK.format(base_url="https://x.github.io/r/artefacts/")
    lines = block.splitlines()
    table_lines = [l for l in lines if l.startswith("|")]
    # Header row + separator + 4 data rows = 6
    assert len(table_lines) == 6
