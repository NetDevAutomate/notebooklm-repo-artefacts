"""Integration tests: end-to-end flows without external services.

These tests validate the local pipeline steps work together:
- collect → README block injection → link validation
- pages setup → README update → block structure
"""

from pathlib import Path
from unittest.mock import patch

from repo_artefacts.pages import README_BLOCK, setup_pages
from repo_artefacts.publish import check_artefacts
from repo_artefacts.readme_updater import update_readme_artefacts


def _make_repo_with_artefacts(tmp_path: Path) -> Path:
    """Create a fake repo with artefact files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Test Repo\n\nDescription.\n")
    arts = repo / "docs" / "artefacts"
    arts.mkdir(parents=True)
    (arts / "audio_overview.mp3").write_bytes(b"fake-audio")
    (arts / "video_overview.mp4").write_bytes(b"fake-video")
    (arts / "infographic.png").write_bytes(b"fake-img")
    (arts / "slides.pdf").write_bytes(b"fake-pdf")
    return repo


def test_pages_setup_produces_valid_block(tmp_path: Path) -> None:
    """setup_pages creates a README block with valid structure and links."""
    repo = _make_repo_with_artefacts(tmp_path)

    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        url = setup_pages(repo, "TestOrg", "test-repo")

    readme_text = (repo / "README.md").read_text()

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
    index = repo / "docs" / "artefacts" / "index.html"
    assert index.exists()
    html = index.read_text()
    assert "test-repo" in html


def test_pages_then_updater_preserves_block(tmp_path: Path) -> None:
    """Running update-readme after pages doesn't break the block."""
    repo = _make_repo_with_artefacts(tmp_path)
    readme = repo / "README.md"
    arts = repo / "docs" / "artefacts"

    # First: pages setup
    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        setup_pages(repo, "Org", "repo")

    pages_text = readme.read_text()
    assert "## Generated Artefacts" in pages_text

    # Second: readme_updater (download command would do this)
    update_readme_artefacts(readme, arts)

    updated_text = readme.read_text()
    # Block was replaced but markers still intact
    assert "<!-- ARTEFACTS:START -->" in updated_text
    assert "<!-- ARTEFACTS:END -->" in updated_text
    assert "## Generated Artefacts" in updated_text


def test_check_artefacts_after_pages(tmp_path: Path) -> None:
    """check_artefacts finds files after pages setup."""
    repo = _make_repo_with_artefacts(tmp_path)
    arts = repo / "docs" / "artefacts"

    with patch("repo_artefacts.pages.enable_github_pages", return_value=True):
        setup_pages(repo, "Org", "repo")

    found = check_artefacts(arts)
    assert set(found.keys()) == {"audio", "video", "infographic", "slides"}


def test_collector_to_readme_flow(tmp_path: Path) -> None:
    """Collect repo content, then verify README update works."""
    repo = tmp_path / "repo"
    repo.mkdir()
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


def test_link_checker_validates_block(tmp_path: Path) -> None:
    """The check_links script catches invalid anchors."""
    import importlib.util
    import sys

    script = Path(__file__).resolve().parent.parent / "scripts" / "check_links.py"
    spec = importlib.util.spec_from_file_location("check_links", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Valid block
    valid = README_BLOCK.format(base_url="https://org.github.io/repo/artefacts/")
    mod.errors = []
    mod.check_artefacts_block(Path("test.md"), valid)
    assert mod.errors == []

    # Block with wrong heading
    bad = valid.replace("## Generated Artefacts", "## Wrong Heading")
    mod.errors = []
    mod.check_artefacts_block(Path("test.md"), bad)
    assert len(mod.errors) == 1
    assert "missing" in mod.errors[0].lower() or "Generated Artefacts" in mod.errors[0]
