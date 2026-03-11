"""Tests for repo_artefacts.notebooklm module (mocked, no API calls)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from repo_artefacts.notebooklm import (
    ARTEFACT_CONFIG,
    DOWNLOAD_MAP,
    MAX_RETRIES,
    NAME_TO_TYPE,
    ArtefactStatus,
    ArtefactType,
    RawArtefact,
    _delete_existing_by_type,
    _parse_raw_artefacts,
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


# --- Type definitions ---


def test_artefact_status_values() -> None:
    assert ArtefactStatus.COMPLETED == 3
    assert ArtefactStatus.FAILED == 4
    assert ArtefactStatus(3) is ArtefactStatus.COMPLETED


def test_artefact_type_values() -> None:
    assert ArtefactType.AUDIO == 1
    assert ArtefactType.VIDEO == 8


def test_name_to_type_covers_config() -> None:
    assert set(NAME_TO_TYPE.keys()) == set(ARTEFACT_CONFIG.keys())


def test_raw_artefact_from_raw_valid() -> None:
    art = RawArtefact.from_raw(["id-1", None, 1, None, 3])
    assert art is not None
    assert art.id == "id-1"
    assert art.type_code is ArtefactType.AUDIO
    assert art.status is ArtefactStatus.COMPLETED
    assert art.is_completed
    assert art.type_name == "audio"


def test_raw_artefact_from_raw_too_short() -> None:
    assert RawArtefact.from_raw(["id-1", None]) is None


def test_raw_artefact_from_raw_unknown_type() -> None:
    assert RawArtefact.from_raw(["id-1", None, 99, None, 3]) is None


def test_parse_raw_artefacts_filters() -> None:
    raw = [
        ["id-1", None, 1, None, 3],  # valid: audio completed
        ["id-2", None],  # too short
        ["id-3", None, 99, None, 3],  # unknown type
        ["id-4", None, 8, None, 4],  # valid: video failed
    ]
    result = _parse_raw_artefacts(raw)
    assert len(result) == 2
    assert result[0].id == "id-1"
    assert result[1].id == "id-4"


# --- Config validation ---


def test_artefact_config_has_all_types() -> None:
    assert set(ARTEFACT_CONFIG.keys()) == {"audio", "video", "slides", "infographic"}


def test_artefact_config_has_method_key() -> None:
    for name, cfg in ARTEFACT_CONFIG.items():
        assert "method" in cfg, f"{name} missing 'method'"
        assert "instructions" in cfg, f"{name} missing 'instructions'"


def test_download_map_covers_all_types() -> None:
    labels = {entry.label for entry in DOWNLOAD_MAP}
    assert labels == {"audio", "video", "slides", "infographic"}


def test_download_map_filenames_are_standard() -> None:
    expected = {
        "audio_overview.mp3",
        "video_overview.mp4",
        "slides.pdf",
        "infographic.png",
    }
    actual = {entry.filename for entry in DOWNLOAD_MAP}
    assert actual == expected


def test_max_retries_is_reasonable() -> None:
    assert 1 <= MAX_RETRIES <= 5


# --- Snapshot ---


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


async def test_poll_by_type_detects_completion() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["new-id", None, 1, None, 3]])
    status = await _poll_by_type(client, "nb-1", "audio", set())
    assert status == "completed"


async def test_poll_by_type_detects_failure() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["new-id", None, 7, None, 4]])
    status = await _poll_by_type(client, "nb-1", "infographic", set())
    assert status == "failed"


async def test_poll_by_type_in_progress() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["new-id", None, 8, None, 1]])
    status = await _poll_by_type(client, "nb-1", "video", set())
    assert status == "in_progress"


async def test_poll_by_type_known_completed_still_reported() -> None:
    """Known completed IDs still report completed status."""
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["old-id", None, 1, None, 3]])
    status = await _poll_by_type(client, "nb-1", "audio", {"old-id"})
    assert status == "completed"


# --- Delete existing by type ---


async def test_delete_existing_deletes_failed() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(
        return_value=[
            ["good-id", None, 7, None, 3],  # completed
            ["bad-id", None, 7, None, 4],  # failed
        ]
    )
    client.artifacts.delete = AsyncMock()
    # Default: delete ALL existing (including completed)
    await _delete_existing_by_type(client, "nb-1", "infographic")
    assert client.artifacts.delete.call_count == 2


async def test_delete_existing_failed_only() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(
        return_value=[
            ["good-id", None, 7, None, 3],  # completed
            ["bad-id", None, 7, None, 4],  # failed
        ]
    )
    client.artifacts.delete = AsyncMock()
    # failed_only=True: only delete failed, leave completed
    await _delete_existing_by_type(client, "nb-1", "infographic", failed_only=True)
    client.artifacts.delete.assert_called_once_with("nb-1", "bad-id")


async def test_delete_existing_skips_other_types() -> None:
    client = MagicMock()
    client.artifacts._list_raw = AsyncMock(return_value=[["ok-id", None, 1, None, 3]])
    client.artifacts.delete = AsyncMock()
    # Audio (type 1) shouldn't be deleted when targeting infographic
    await _delete_existing_by_type(client, "nb-1", "infographic")
    client.artifacts.delete.assert_not_called()


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
