"""Tests for repo_artefacts.publish module."""

from pathlib import Path

from repo_artefacts.publish import STANDARD_FILES, check_artefacts


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
