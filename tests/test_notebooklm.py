"""Tests for repo_artefacts.notebooklm module (mocked, no API calls)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from repo_artefacts.notebooklm import (
    ARTEFACT_CONFIG,
    CONCURRENCY_LIMIT,
    MAX_RETRIES,
    NAME_TO_ARTIFACT_TYPE,
    POLL_WINDOW,
    _delete_existing_by_type,
    _request_artefact,
    _wait_for_artefact,
)


@dataclass
class FakeArtifact:
    """Mimics upstream notebooklm.Artifact."""

    id: str
    kind: MagicMock  # str enum-like
    status: int
    is_completed: bool = False
    is_failed: bool = False


@dataclass
class FakeStatus:
    task_id: str = "fake-task-id"
    status: str = "in_progress"
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @property
    def is_completed(self) -> bool:
        """Alias — Artifact uses is_completed, GenerationStatus uses is_complete."""
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


# --- Type definitions ---


def test_name_to_artifact_type_covers_config() -> None:
    assert set(NAME_TO_ARTIFACT_TYPE.keys()) == set(ARTEFACT_CONFIG.keys())


def test_name_to_artifact_type_values() -> None:
    from notebooklm import ArtifactType

    assert NAME_TO_ARTIFACT_TYPE["audio"] == ArtifactType.AUDIO
    assert NAME_TO_ARTIFACT_TYPE["video"] == ArtifactType.VIDEO
    assert NAME_TO_ARTIFACT_TYPE["slides"] == ArtifactType.SLIDE_DECK
    assert NAME_TO_ARTIFACT_TYPE["infographic"] == ArtifactType.INFOGRAPHIC


# --- Config validation ---


def test_artefact_config_has_all_types() -> None:
    assert set(ARTEFACT_CONFIG.keys()) == {"audio", "video", "slides", "infographic"}


def test_artefact_config_has_method_key() -> None:
    for name, cfg in ARTEFACT_CONFIG.items():
        assert "method" in cfg, f"{name} missing 'method'"
        assert "instructions" in cfg, f"{name} missing 'instructions'"


def test_max_retries_is_reasonable() -> None:
    assert 1 <= MAX_RETRIES <= 5


def test_concurrency_limit_is_positive() -> None:
    assert CONCURRENCY_LIMIT >= 1


def test_poll_window_is_reasonable() -> None:
    assert 10 <= POLL_WINDOW <= 300


# --- Delete existing by type ---


async def test_delete_existing_deletes_failed() -> None:
    from notebooklm import ArtifactType

    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            FakeArtifact(id="good-id", kind=ArtifactType.INFOGRAPHIC, status=3, is_completed=True),
            FakeArtifact(id="bad-id", kind=ArtifactType.INFOGRAPHIC, status=4, is_failed=True),
        ]
    )
    client.artifacts.delete = AsyncMock()
    # Default: delete ALL existing (including completed)
    await _delete_existing_by_type(client, "nb-1", "infographic")
    assert client.artifacts.delete.call_count == 2


async def test_delete_existing_failed_only() -> None:
    from notebooklm import ArtifactType

    client = MagicMock()
    client.artifacts.list = AsyncMock(
        return_value=[
            FakeArtifact(id="good-id", kind=ArtifactType.INFOGRAPHIC, status=3, is_completed=True),
            FakeArtifact(id="bad-id", kind=ArtifactType.INFOGRAPHIC, status=4, is_failed=True),
        ]
    )
    client.artifacts.delete = AsyncMock()
    # failed_only=True: only delete failed, leave completed
    await _delete_existing_by_type(client, "nb-1", "infographic", failed_only=True)
    client.artifacts.delete.assert_called_once_with("nb-1", "bad-id")


async def test_delete_existing_skips_other_types() -> None:
    from notebooklm import ArtifactType

    client = MagicMock()
    # Upstream list() with artifact_type filter only returns matching types
    # So when targeting infographic, audio artifacts are never returned
    client.artifacts.list = AsyncMock(
        return_value=[]  # No infographic artifacts exist
    )
    client.artifacts.delete = AsyncMock()
    # Audio shouldn't be deleted when targeting infographic
    await _delete_existing_by_type(client, "nb-1", "infographic")
    client.artifacts.delete.assert_not_called()
    # Verify the list call used the correct artifact_type filter
    client.artifacts.list.assert_called_once_with("nb-1", artifact_type=ArtifactType.INFOGRAPHIC)


# --- Request artefact ---


async def test_request_artefact_audio() -> None:
    client = MagicMock()
    client.artifacts.generate_audio = AsyncMock(return_value=FakeStatus())
    result = await _request_artefact(client, "nb-1", "audio")
    assert result.task_id == "fake-task-id"
    client.artifacts.generate_audio.assert_called_once()


async def test_request_artefact_invalid_type() -> None:
    client = MagicMock()
    with pytest.raises(KeyError):
        await _request_artefact(client, "nb-1", "podcast")


# --- Wait for artefact ---


async def test_wait_for_artefact_completes() -> None:
    client = MagicMock()
    client.artifacts.get = AsyncMock(return_value=FakeStatus(task_id="task-1", status="completed"))
    result = await _wait_for_artefact(client, "nb-1", "task-1", 60.0, "audio")
    assert result.is_completed
    client.artifacts.get.assert_called_once()
