"""Tests for repo_artefacts.readme_updater module."""

from pathlib import Path

from repo_artefacts.readme_updater import update_readme_artefacts


def test_update_creates_section(tmp_path: Path) -> None:
    """Appends artefacts section when no markers exist."""
    readme = tmp_path / "README.md"
    readme.write_text("# My Project\n\nSome content.\n")
    arts = tmp_path / "artefacts"
    arts.mkdir()
    (arts / "audio_overview.mp3").write_bytes(b"fake")
    (arts / "slides.pdf").write_bytes(b"fake")

    update_readme_artefacts(readme, arts)

    text = readme.read_text()
    assert "<!-- ARTEFACTS:START -->" in text
    assert "<!-- ARTEFACTS:END -->" in text
    assert "🎧 Audio Overview" in text
    assert "📊 Slide Deck" in text


def test_update_replaces_existing_block(tmp_path: Path) -> None:
    """Replaces content between existing markers."""
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Proj\n\n<!-- ARTEFACTS:START -->\nold stuff\n<!-- ARTEFACTS:END -->\n\n## Footer\n"
    )
    arts = tmp_path / "artefacts"
    arts.mkdir()
    (arts / "infographic.png").write_bytes(b"fake")

    update_readme_artefacts(readme, arts)

    text = readme.read_text()
    assert "old stuff" not in text
    assert "🖼️ Infographic" in text
    assert "## Footer" in text  # Content after block preserved


def test_update_skips_md_and_content_files(tmp_path: Path) -> None:
    """Skips .md files and *_content.pdf files."""
    readme = tmp_path / "README.md"
    readme.write_text("# Proj\n")
    arts = tmp_path / "artefacts"
    arts.mkdir()
    (arts / "repo_content.pdf").write_bytes(b"fake")
    (arts / "notes.md").write_bytes(b"fake")
    (arts / "video_overview.mp4").write_bytes(b"fake")

    update_readme_artefacts(readme, arts)

    text = readme.read_text()
    assert "repo_content" not in text
    assert "notes.md" not in text
    assert "🎬 Video Overview" in text


def test_update_no_artefacts_dir(tmp_path: Path) -> None:
    """Handles missing artefacts directory gracefully."""
    readme = tmp_path / "README.md"
    readme.write_text("# Proj\n")

    update_readme_artefacts(readme, tmp_path / "nonexistent")

    # README unchanged
    assert readme.read_text() == "# Proj\n"


def test_update_empty_artefacts_dir(tmp_path: Path) -> None:
    """Shows 'no artefacts' message when directory is empty."""
    readme = tmp_path / "README.md"
    readme.write_text("# Proj\n")
    arts = tmp_path / "artefacts"
    arts.mkdir()

    update_readme_artefacts(readme, arts)

    text = readme.read_text()
    assert "No artefacts generated yet" in text
