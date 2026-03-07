"""Tests for token resolution and GitHub Pages API in pages module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_artefacts.pages import get_github_token


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
