"""Tests for repo_artefacts.notebooklm module (mocked, no API calls)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from repo_artefacts.notebooklm import (
    ARTEFACT_CONFIG,
    ARTEFACT_TYPE_CODE,
    DOWNLOAD_MAP,
    MAX_RETRIES,
    _delete_failed_by_type,
    _poll_by_type,
    _request_artefact,
    _snapshot_artefact_ids,
)


@dataclass
class FakeStatus:
    task_id: str = "fake-task-id"
    status: str = "in_progress"
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


# --- Config validation ---


def test_artefact_config_has_all_types() -> None:
    assert set(ARTEFACT_CONFIG.keys()) == {"audio", "video", "slides", "infographic"}


def test_artefact_type_codes_match_config() -> None:
    assert set(ARTEFACT_TYPE_CODE.keys()) == set(ARTEFACT_CONFIG.keys())


def test_download_map_covers_all_types() -> None:
    labels = {entry[0] for entry in DOWNLOAD_MAP}
    assert labels == {"audio", "video", "slides", "infographic"}


def test_download_map_filenames_are_standard() -> None:
    expected = {
        "audio_overview.mp3",
        "video_overview.mp4",
        "slides.pdf",
        "infographic.png",
    }
    actual = {entry[3] for entry in DOWNLOAD_MAP}
    assert actual == expected


def test_max_retries_is_reasonable() -> None:
    assert 1 <= MAX_RETRIES <= 5


# --- Snapshot ---


@pytest.mark.asyncio
async def test_snapshot_artefact_ids() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(
        return_value=[
            ["id-1", None, 1, None, 3],  # audio, completed
            ["id-2", None, 8, None, 4],  # video, failed
        ]
    )
    result = await _snapshot_artefact_ids(client, "nb-1")
    assert "id-1" in result["audio"]
    assert "id-2" in result["video"]
    assert result["slides"] == set()


# --- Poll by type ---


@pytest.mark.asyncio
async def test_poll_by_type_detects_completion() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["new-id", None, 1, None, 3]])
    status = await _poll_by_type(client, "nb-1", "audio", set())
    assert status == "completed"


@pytest.mark.asyncio
async def test_poll_by_type_detects_failure() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["new-id", None, 7, None, 4]])
    status = await _poll_by_type(client, "nb-1", "infographic", set())
    assert status == "failed"


@pytest.mark.asyncio
async def test_poll_by_type_in_progress() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["new-id", None, 8, None, 1]])
    status = await _poll_by_type(client, "nb-1", "video", set())
    assert status == "in_progress"


@pytest.mark.asyncio
async def test_poll_by_type_known_completed_still_reported() -> None:
    """Known completed IDs still report completed status."""
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["old-id", None, 1, None, 3]])
    status = await _poll_by_type(client, "nb-1", "audio", {"old-id"})
    assert status == "completed"


# --- Delete failed ---


@pytest.mark.asyncio
async def test_delete_failed_by_type() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(
        return_value=[
            ["good-id", None, 7, None, 3],  # completed
            ["bad-id", None, 7, None, 4],  # failed
        ]
    )
    client.artifacts.delete = AsyncMock()
    await _delete_failed_by_type(client, "nb-1", "infographic")
    client.artifacts.delete.assert_called_once_with("nb-1", "bad-id")


@pytest.mark.asyncio
async def test_delete_failed_skips_non_failed() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["ok-id", None, 1, None, 3]])
    client.artifacts.delete = AsyncMock()
    await _delete_failed_by_type(client, "nb-1", "audio")
    client.artifacts.delete.assert_not_called()


# --- Request artefact ---


@pytest.mark.asyncio
async def test_request_artefact_audio() -> None:
    client = MagicMock()
    client.artifacts.generate_audio = AsyncMock(return_value=FakeStatus())
    result = await _request_artefact(client, "nb-1", "audio")
    assert result.task_id == "fake-task-id"
    client.artifacts.generate_audio.assert_called_once()


@pytest.mark.asyncio
async def test_request_artefact_invalid_type() -> None:
    client = MagicMock()
    with pytest.raises(KeyError):
        await _request_artefact(client, "nb-1", "podcast")
