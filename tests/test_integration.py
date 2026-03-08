"""Integration tests: end-to-end flows without external services.

These tests validate the local pipeline steps work together:
- collect -> README block injection -> link validation
- pages setup -> README update -> block structure
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from repo_artefacts.pages import README_BLOCK, setup_pages
from repo_artefacts.publish import check_artefacts

# Make scripts/ importable for check_links
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from check_links import check_artefacts_block


def test_pages_setup_produces_valid_block(artefacts_repo: Path) -> None:
    """setup_pages creates a README block with valid structure and links."""
    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        url = setup_pages(artefacts_repo, "TestOrg", "test-repo")

    readme_text = (artefacts_repo / "README.md").read_text()

    # Block structure
    assert "<!-- ARTEFACTS:START -->" in readme_text
    assert "<!-- ARTEFACTS:END -->" in readme_text
    assert "## Generated Artefacts" in readme_text

    # All four links present with correct base URL
    assert f"[Listen to the Audio Overview]({url})" in readme_text
    assert f"[Watch the Video Overview]({url}#video)" in readme_text
    assert f"[View the Infographic]({url}#infographic)" in readme_text
    assert f"[Browse the Slide Deck]({url}#slides)" in readme_text

    # Player page created
    index = artefacts_repo / "docs" / "artefacts" / "index.html"
    assert index.exists()
    html = index.read_text()
    assert "test-repo" in html


def test_check_artefacts_after_pages(artefacts_repo: Path) -> None:
    """check_artefacts finds files after pages setup."""
    arts = artefacts_repo / "docs" / "artefacts"

    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        setup_pages(artefacts_repo, "Org", "repo")

    found = check_artefacts(arts)
    assert set(found.keys()) == {"audio", "video", "infographic", "slides"}


def test_collector_to_readme_flow(tmp_path: Path) -> None:
    """Collect repo content, then verify README update works."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "README.md").write_text("# My Repo\n")
    src = repo / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')\n")

    out = repo / "docs" / "artefacts" / "content.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    from repo_artefacts.collector import collect_repo_content

    collect_repo_content(repo, out)

    assert out.exists()
    assert "hello" in out.read_text()


def test_link_checker_validates_block() -> None:
    """check_artefacts_block catches invalid anchors."""
    # Valid block
    valid = README_BLOCK.format(base_url="https://org.github.io/repo/artefacts/")
    errors = check_artefacts_block(Path("test.md"), valid)
    assert errors == []

    # Block with wrong heading
    bad = valid.replace("## Generated Artefacts", "## Wrong Heading")
    errors = check_artefacts_block(Path("test.md"), bad)
    assert len(errors) == 1
    assert "Generated Artefacts" in errors[0]
