"""Domain exceptions for repo-artefacts.

Library code raises these. The CLI layer catches and translates to typer.Exit().
"""

from __future__ import annotations


class RepoArtefactsError(Exception):
    """Base exception for all repo-artefacts errors.

    Catch this to handle any error from the library in one except clause.
    """


class GitRemoteError(RepoArtefactsError):
    """Could not determine GitHub org/repo from git remote."""


class CollectionError(RepoArtefactsError):
    """Failed to collect repository content."""
