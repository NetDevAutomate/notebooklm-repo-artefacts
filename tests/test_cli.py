"""Tests for repo_artefacts.cli module — command registration and helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from repo_artefacts.cli import ALL_ARTEFACTS, app
from repo_artefacts.exceptions import RepoArtefactsError

runner = CliRunner()


# --- Command registration ---


def test_app_has_all_commands() -> None:
    """All expected commands are registered on the Typer app."""
    # Typer uses cmd.name for explicit names, callback.__name__ for derived names
    registered = set()
    for cmd in app.registered_commands:
        if cmd.name:
            registered.add(cmd.name)
        elif cmd.callback:
            registered.add(cmd.callback.__name__)
    expected = {
        "process",
        "generate",
        "download",
        "list",
        "delete",
        "pages",
        "publish",
        "pipeline",
        "migrate",
        "validate",
        "clean",
    }
    assert expected <= registered


# --- _handle_errors decorator ---


def test_handle_errors_catches_domain_exception() -> None:
    """_handle_errors translates RepoArtefactsError to exit code 1."""
    with patch(
        "repo_artefacts.pages.get_github_info",
        side_effect=RepoArtefactsError("test error"),
    ):
        result = runner.invoke(app, ["pages", "/nonexistent"])
    assert result.exit_code == 1


# --- _get_notebook_id ---


def test_generate_requires_notebook_id() -> None:
    """generate exits with error when no notebook ID provided."""
    result = runner.invoke(app, ["generate"], env={"NOTEBOOK_ID": ""})
    assert result.exit_code == 1
    assert "No notebook ID" in result.output


# --- ALL_ARTEFACTS constant ---


def test_all_artefacts_has_four_types() -> None:
    assert len(ALL_ARTEFACTS) == 4
    assert set(ALL_ARTEFACTS) == {"audio", "video", "slides", "infographic"}


# --- Artefact selection logic ---


@pytest.mark.parametrize(
    "flags,expected_types",
    [
        ([], ["audio", "video", "slides", "infographic"]),
        (["--audio", "--video"], ["audio", "video"]),
        (["--exclude", "slides"], ["audio", "video", "infographic"]),
        (["--audio"], ["audio"]),
    ],
)
def test_artefact_selection_flags(flags: list[str], expected_types: list[str]) -> None:
    """Pipeline artefact selection resolves correctly from CLI flags."""
    audio = "--audio" in flags
    video = "--video" in flags
    slides = "--slides" in flags
    infographic = "--infographic" in flags
    exclude = [flags[flags.index("--exclude") + 1]] if "--exclude" in flags else []

    selected = [
        a
        for a, flag in [
            ("audio", audio),
            ("video", video),
            ("slides", slides),
            ("infographic", infographic),
        ]
        if flag
    ]

    if selected:
        target = selected
    elif exclude:
        bad = {e.lower() for e in exclude}
        target = [a for a in ALL_ARTEFACTS if a not in bad]
    else:
        target = list(ALL_ARTEFACTS)

    assert target == expected_types


# --- validate command ---


def test_validate_no_readme(tmp_path: Path) -> None:
    """validate exits with error when README.md doesn't exist."""
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 1


def test_validate_no_artefact_block(tmp_path: Path) -> None:
    """validate reports no block when README has no ARTEFACTS markers."""
    (tmp_path / "README.md").write_text("# My Project\nNo artefacts here.")
    result = runner.invoke(app, ["validate", str(tmp_path)])
    assert result.exit_code == 0
    assert "No artefacts block" in result.output


# --- clean command ---


def test_clean_requires_store() -> None:
    """clean exits with error when no store configured."""
    with patch("repo_artefacts.config.CONFIG_FILE", Path("/nonexistent")):
        result = runner.invoke(app, ["clean"])
    assert result.exit_code == 1
